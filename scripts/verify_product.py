"""Exercise the real local backend and backup recovery in disposable profiles.

No external source or AI endpoint is contacted. Can use a packaged interpreter
and backend entry to verify distribution bytes rather than development imports.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--backend-entry", default=str(ROOT / "desktop/backend_entry.py"))
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Unique profiles make rerunning safe and keep previous failure evidence.
    profiles = output / ("flow-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3))
    profiles.mkdir()
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "external_services_used": False,
              "python": str(Path(args.python).resolve()), "backend_entry": str(Path(args.backend_entry).resolve()),
              "profiles": str(profiles), "checks": []}

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        result["checks"].append(name)
        print("PASS " + name, flush=True)

    @contextmanager
    def backend(profile):
        profile.mkdir(exist_ok=True)
        ready = profile / "ready.json"
        token = secrets.token_hex(32)
        environment = {**os.environ, "WORKBENCH_SESSION_TOKEN": token, "PYTHONUTF8": "1",
                       "PYTHONDONTWRITEBYTECODE": "1", "DEEPSEEK_API_KEY": ""}
        for field in ("WORKBENCH_DATABASE_URL", "WORKBENCH_DB_PATH", "PYTHONHOME", "PYTHONPATH"):
            environment.pop(field, None)
        with (profile / "process.log").open("wb") as logs:
            process = subprocess.Popen([args.python, "-B", args.backend_entry, "--data-dir", str(profile),
                                        "--ready-file", str(ready)], env=environment, stdout=logs, stderr=logs,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            base = None
            def request(route, data=None, method=None, raw=False, expected=200):
                body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
                req = Request(base + route, data=body, method=method,
                              headers={"X-Workbench-Token": token, "Content-Type": "application/json"})
                try:
                    with urlopen(req, timeout=90) as response:
                        content, status, headers = response.read(), response.status, response.headers
                except HTTPError as error:
                    content, status, headers = error.read(), error.code, error.headers
                if status != expected:
                    raise AssertionError(f"{route}: expected HTTP {expected}, received {status}")
                return (content, headers) if raw else json.loads(content)
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError("backend exited before ready; see process.log")
                    if ready.exists():
                        base = "http://127.0.0.1:" + str(json.loads(ready.read_text("utf-8"))["port"])
                        try:
                            if request("/api/v2/health")["ok"]:
                                break
                        except (OSError, AssertionError):
                            pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("backend did not become ready in 60 seconds")
                yield request
            finally:
                if process.poll() is None and base:
                    try:
                        request("/api/v2/lifecycle/shutdown", {}, "POST")
                    except Exception:
                        pass
                try:
                    code = process.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)
                    raise AssertionError("backend failed to shut down cleanly")
                check("backend exited cleanly and removed ready marker", code == 0 and not ready.exists())

    def terminal(request, jid):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = request("/api/v2/jobs/" + jid)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(0.1)
        raise AssertionError("local report job did not finish in 30 seconds")

    try:
        profile = profiles / "source"
        with backend(profile) as request:
            check("backup available on actual SQLite storage", request("/api/v2/capabilities")["backup"]["available"])
            material = {"title": "憲法九条の公開資料", "url": "https://example.org/product-acceptance/1",
                        "content": "沖縄の市民団体による平和と憲法九条の公開討論。", "type": "news",
                        "source_name": "Deterministic acceptance fixture", "published_at": "2026-10-08T09:00:00+09:00"}
            imported = request("/api/v2/items/import", {"items": [material, material, {**material, "url": "javascript:invalid"}]})
            check("import preserves valid evidence and counts duplicates and rejects", imported["inserted"] == 1 and imported["duplicates"] == 1 and imported["rejected"] == 1)
            item_id = request("/api/v2/items")["items"][0]["id"]
            detail = request("/api/v2/items/" + item_id)
            check("stored material keeps body and original URL", detail["content"] == material["content"] and detail["url"] == material["url"])
            request("/api/v2/drafts/collection", {"schema": 1, "values": {"keyword": "平和", "max_records": 1000}, "revision": 0}, "PUT")
            stale = request("/api/v2/drafts/collection", {"schema": 1, "values": {"keyword": "古い入力"}, "revision": 0}, "PUT", expected=409)
            check("draft conflict retains newer saved input", stale["error"]["code"] == "draft_conflict" and request("/api/v2/drafts/collection")["draft"]["values"]["keyword"] == "平和")
            params = {"title": "製品検証 · 証拠整理", "start_date": "2026-10-08", "end_date": "2026-10-08",
                      "mode": "evidence", "include_news": True, "include_social": False}
            created = request("/api/v2/jobs", {"kind": "report", "params": params}, expected=202)
            completed = terminal(request, created["id"])
            check("persistent worker generates evidence report", completed["status"] == "succeeded")
            report_id = completed["result"]["report_id"]
            report = request("/api/v2/reports/" + report_id)
            content, headers = request("/api/v2/reports/" + report_id + "/download", raw=True)
            check("download matches immutable report and source snapshot", content.decode("utf-8") == report["markdown"] and report["source_count"] == 1 and report["snapshot"])
            backup, headers = request("/api/v2/backup", raw=True)
            backup_path = profiles / "verified-backup.sqlite3"
            backup_path.write_bytes(backup)
            check("online backup download has SQLite content and filename", backup.startswith(b"SQLite format 3\0") and "peace-atlas-backup-" in headers["Content-Disposition"])
            with sqlite3.connect(backup_path) as connection:
                check("downloaded snapshot integrity and foreign keys", connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and connection.execute("PRAGMA foreign_key_check").fetchall() == [])
            result["backup_sha256"] = hashlib.sha256(backup).hexdigest()
            result["report_id"], result["item_id"] = report_id, item_id
        with backend(profile) as request:
            check("restart preserves materials reports and draft", request("/api/v2/items")["total"] == 1 and request("/api/v2/reports/" + report_id)["markdown"] == report["markdown"] and request("/api/v2/drafts/collection")["draft"]["values"]["max_records"] == 1000)
        restored = profiles / "restored"
        restored.mkdir()
        shutil.copyfile(backup_path, restored / "workbench.sqlite3")
        with backend(restored) as request:
            check("backup restores in a separate real application profile", request("/api/v2/items")["total"] == 1 and request("/api/v2/reports/" + report_id)["snapshot"] == report["snapshot"] and request("/api/v2/items/" + item_id)["content"] == material["content"])
            check("restored draft keeps saved research budget", request("/api/v2/drafts/collection")["draft"]["values"]["max_records"] == 1000)
        result["ok"] = True
    except BaseException as error:
        result["ok"], result["failure"] = False, str(error)
        raise
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "product-flow.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    main()
