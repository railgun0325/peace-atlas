"""Verify graceful backend shutdown when its desktop parent pipe disappears.

Uses disposable profiles and local HTTP only. The abrupt-parent scenario exits
an actual intermediate process, rather than merely setting a parent-PID flag.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def spawn_backend(args, profile, environment, log):
    return subprocess.Popen([args.python, "-B", args.backend_entry, "--data-dir", str(profile),
                             "--ready-file", str(profile / "ready.json"), "--watch-parent-stdin"],
                            stdin=subprocess.PIPE, stdout=log, stderr=log, env=environment,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def helper(args):
    profile = Path(args.helper).resolve()
    with (profile / "process.log").open("wb") as log:
        child = spawn_backend(args, profile, os.environ, log)
        (profile / "child.json").write_text(json.dumps({"pid": child.pid}), "utf-8")
        deadline = time.monotonic() + 90
        while not (profile / "release-parent").exists():
            if child.poll() is not None or time.monotonic() >= deadline:
                os._exit(2)
            time.sleep(0.05)
        # Deliberately bypass Popen/context cleanup: the OS closes the parent's
        # pipe handle and the child must detect EOF and shut down itself.
        os._exit(0)


class ProcessObserver:
    def __init__(self, pid):
        self.pid, self.handle = pid, None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            self.kernel.GetExitCodeProcess.restype = wintypes.BOOL
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.OpenProcess(0x1000, False, pid)
            if not self.handle:
                raise OSError("cannot observe the isolated child process")

    def exit_code(self):
        if os.name == "nt":
            from ctypes import byref, wintypes
            code = wintypes.DWORD()
            if not self.kernel.GetExitCodeProcess(self.handle, byref(code)):
                raise OSError("cannot read isolated child exit code")
            return None if code.value == 259 else code.value
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return 0  # POSIX observer proves disappearance, not its exact status.
        return None

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--backend-entry", default=str(ROOT / "desktop/backend_entry.py"))
    parser.add_argument("--helper", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.helper:
        helper(args)
        return
    if not args.output_dir:
        parser.error("--output-dir is required")
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    run = output / ("parent-exit-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3))
    run.mkdir()
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "external_services_used": False,
              "python": str(Path(args.python).resolve()), "backend_entry": str(Path(args.backend_entry).resolve()),
              "profiles": str(run), "checks": []}
    token = secrets.token_hex(32)
    environment = {**os.environ, "WORKBENCH_SESSION_TOKEN": token, "DEEPSEEK_API_KEY": "",
                   "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    for name in ("WORKBENCH_DATABASE_URL", "WORKBENCH_DB_PATH", "WORKBENCH_DATA_DIR", "PYTHONHOME", "PYTHONPATH"):
        environment.pop(name, None)

    def request(base, route, data=None):
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        with urlopen(Request(base + route, data=body,
                             headers={"X-Workbench-Token": token, "Content-Type": "application/json"},
                             method="PUT" if "drafts/" in route and data is not None else None), timeout=5) as response:
            return json.loads(response.read())

    def ready(profile, process):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError("backend or helper exited before becoming ready")
            marker = profile / "ready.json"
            if marker.exists():
                base = "http://127.0.0.1:" + str(json.loads(marker.read_text("utf-8"))["port"])
                try:
                    if request(base, "/api/v2/health")["ok"]:
                        return base
                except OSError:
                    pass
            time.sleep(0.1)
        raise AssertionError("backend did not become ready")

    def save_and_check(base, profile, marker):
        saved = request(base, "/api/v2/drafts/collection", {"schema": 1, "values": {"keyword": marker}, "revision": 0})
        assert saved["draft"]["values"]["keyword"] == marker

    def check_database(profile, marker, *, parent_eof=True):
        with closing(sqlite3.connect((profile / "workbench.sqlite3").as_uri() + "?mode=ro", uri=True)) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            data = connection.execute("SELECT data FROM wb_drafts WHERE key='collection'").fetchone()[0]
            assert json.loads(data)["values"]["keyword"] == marker
        assert not (profile / "ready.json").exists()
        if parent_eof:
            assert "桌面父进程" in (profile / "logs/backend.log").read_text("utf-8")

    try:
        for scenario in ("pipe-eof-after-ready", "pipe-eof-at-startup", "held-parent-pipe-http-shutdown"):
            profile = run / scenario
            profile.mkdir()
            with (profile / "process.log").open("wb") as log:
                process = spawn_backend(args, profile, environment, log)
                base = None
                try:
                    marker = "isolated-parent-exit-" + secrets.token_hex(6)
                    if scenario != "pipe-eof-at-startup":
                        base = ready(profile, process)
                        save_and_check(base, profile, marker)
                    if scenario == "held-parent-pipe-http-shutdown":
                        # The desktop keeps its writer open while awaiting a
                        # normal backend exit. A watcher blocked on buffered
                        # stdin must not crash Python's interpreter teardown.
                        request(base, "/api/v2/lifecycle/shutdown", {})
                    else:
                        process.stdin.close()
                    assert process.wait(timeout=40) == 0
                    if not process.stdin.closed:
                        process.stdin.close()
                    assert not (profile / "ready.json").exists()
                    if base:
                        check_database(profile, marker, parent_eof=scenario != "held-parent-pipe-http-shutdown")
                    result["checks"].append({"scenario": scenario, "backend_exit_code": 0,
                                             "ready_removed": True, "saved_data_checked": bool(base)})
                    print("PASS " + scenario, flush=True)
                finally:
                    if process.poll() is None:
                        if base:
                            try:
                                request(base, "/api/v2/lifecycle/shutdown", {})
                            except OSError:
                                pass
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                            process.wait(timeout=10)

        profile = run / "abrupt-real-parent-exit"
        profile.mkdir()
        with (profile / "helper.log").open("wb") as log:
            parent = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--helper", str(profile),
                                       "--python", args.python, "--backend-entry", args.backend_entry], env=environment,
                                      stdout=log, stderr=log,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            observer, base = None, None
            try:
                deadline = time.monotonic() + 10
                while not (profile / "child.json").exists():
                    assert parent.poll() is None and time.monotonic() < deadline
                    time.sleep(0.05)
                observer = ProcessObserver(json.loads((profile / "child.json").read_text("utf-8"))["pid"])
                base = ready(profile, parent)
                marker = "isolated-abrupt-parent-" + secrets.token_hex(6)
                save_and_check(base, profile, marker)
                (profile / "release-parent").touch()
                assert parent.wait(timeout=10) == 0
                deadline = time.monotonic() + 40
                while observer.exit_code() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert observer.exit_code() == 0
                check_database(profile, marker)
                result["checks"].append({"scenario": "abrupt-real-parent-exit", "parent_exit_code": 0,
                                         "backend_exit_code": 0 if os.name == "nt" else "disappeared",
                                         "ready_removed": True, "saved_data_checked": True})
                print("PASS abrupt-real-parent-exit", flush=True)
            finally:
                if parent.poll() is None:
                    parent.terminate()
                    parent.wait(timeout=10)
                if observer and observer.exit_code() is None and base:
                    try:
                        request(base, "/api/v2/lifecycle/shutdown", {})
                    except OSError:
                        pass
                    deadline = time.monotonic() + 15
                    while observer.exit_code() is None and time.monotonic() < deadline:
                        time.sleep(0.1)
                if observer:
                    observer.close()
        result["ok"] = True
    except BaseException as error:
        result.update(ok=False, failure=str(error))
        raise
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "parent-lifecycle.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    main()
