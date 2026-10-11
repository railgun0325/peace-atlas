"""Verify retry and result durability through a real isolated desktop backend.

Only local HTTP, evidence-mode reports, and a uniquely owned SQLite profile are
used. The legacy batch fixture contains a report so even a failing old build
cannot start external collection. Normal batch creation still accepts only
collection/research. The CLI matches verify_product.py's portable runtime inputs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--backend-entry", default=str(ROOT / "desktop/backend_entry.py"))
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    profiles = output / ("behavior-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3))
    profiles.mkdir()
    profile = profiles / "isolated"
    profile.mkdir()
    entry = Path(args.backend_entry).resolve()
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "external_services_used": False,
              "profile": str(profile), "python": str(Path(args.python).resolve()), "backend_entry": str(entry),
              "backend_entry_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(), "checks": [],
              "fixture_scope": "One failed evidence report linked to an owned legacy batch; no collector work.",
              "known_boundary": "No request key: response loss after a retry has already failed is not exactly-once."}

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        result["checks"].append(name)
        print("PASS " + name, flush=True)

    def connection():
        database = (profile / "workbench.sqlite3").resolve()
        if not database.is_relative_to(profiles.resolve()) or not database.is_file():
            raise AssertionError("probe database must be inside its uniquely owned output profile")
        value = sqlite3.connect(database, timeout=15)
        value.execute("PRAGMA foreign_keys=ON")
        return value

    @contextmanager
    def backend():
        ready = profile / "ready.json"
        token = secrets.token_hex(32)
        environment = {**os.environ, "WORKBENCH_SESSION_TOKEN": token, "PYTHONUTF8": "1",
                       "PYTHONDONTWRITEBYTECODE": "1", "DEEPSEEK_API_KEY": ""}
        for field in ("WORKBENCH_DATABASE_URL", "WORKBENCH_DB_PATH", "WORKBENCH_DATA_DIR", "WORKBENCH_SOCIAL_DIR",
                      "PYTHONHOME", "PYTHONPATH"):
            environment.pop(field, None)
        opener = build_opener(ProxyHandler({}))
        with (profile / "process.log").open("ab") as logs:
            process = subprocess.Popen([args.python, "-B", str(entry), "--data-dir", str(profile), "--ready-file", str(ready)],
                env=environment, stdout=logs, stderr=logs,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            base = None
            def request(route, data=None, method=None, expected=200):
                body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
                req = Request(base + route, data=body, method=method,
                    headers={"X-Workbench-Token": token, "Content-Type": "application/json"})
                try:
                    with opener.open(req, timeout=15) as response:
                        content, status = response.read(), response.status
                except HTTPError as error:
                    content, status = error.read(), error.code
                if expected is not None and status != expected:
                    raise AssertionError(f"{route}: expected HTTP {expected}, received {status}")
                value = json.loads(content)
                return value if expected is not None else (status, value)
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError("isolated backend exited before ready; see process.log")
                    if ready.exists():
                        base = "http://127.0.0.1:" + str(json.loads(ready.read_text("utf-8"))["port"])
                        try:
                            if request("/api/v2/health")["ok"]:
                                break
                        except (OSError, AssertionError):
                            pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("isolated backend did not become ready in 60 seconds")
                yield request
            finally:
                # Drop only our fault trigger before normal exit, including on
                # failed proof, so an old/new backend never hangs on this fixture.
                if (profile / "workbench.sqlite3").is_file():
                    with connection() as con:
                        con.execute("DROP TRIGGER IF EXISTS daily_terminal_fault")
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
                    raise AssertionError("isolated backend did not exit cleanly")
                check("isolated backend exited cleanly and removed ready marker", code == 0 and not ready.exists())

    def wait(predicate, message):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError(message)

    def terminal(request, job_id):
        def finished():
            job = request("/api/v2/jobs/" + job_id)
            return job if job["status"] not in {"queued", "running"} else None
        return wait(finished, "local report did not reach terminal state")

    try:
        with backend() as request:
            params = {"title": "Owned failed report", "start_date": "2026-10-08", "end_date": "2026-10-08",
                      "mode": "evidence", "include_news": True, "include_social": False}
            failed = request("/api/v2/jobs", {"kind": "report", "params": params}, expected=202)
            check("empty local evidence report fails without external services", terminal(request, failed["id"])["status"] == "failed")
            original_params = request("/api/v2/jobs/" + failed["id"])["params"]
            batch_id, stamp = str(uuid4()), datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            with connection() as con:
                con.execute("INSERT INTO wb_job_batches(id,title,total_records,parent_batch_id,created_at) VALUES(?,?,1,NULL,?)",
                            (batch_id, "Owned legacy report branch", stamp))
                con.execute("INSERT INTO wb_job_batch_jobs(batch_id,job_id,position,target_records,retry_of_job_id) VALUES(?,?,0,1,NULL)",
                            (batch_id, failed["id"]))
            material = {"title": "憲法九条と平和の公開記録", "url": "https://example.org/daily-behavior/evidence",
                        "content": "沖縄の市民団体が憲法九条と平和について公開討論を行った。", "source_name": "Owned local acceptance fixture",
                        "type": "news", "published_at": "2026-10-08T09:00:00+09:00"}
            check("local material fixture saved", request("/api/v2/items/import", {"items": [material]})["inserted"] == 1)
            marker = "receipt-" + secrets.token_hex(8)
            with connection() as con:
                con.execute("""CREATE TRIGGER daily_terminal_fault BEFORE UPDATE OF status ON wb_jobs
                    WHEN NEW.params LIKE '%%%s%%' AND NEW.status IN ('succeeded','failed','partial','cancelled','interrupted')
                    BEGIN SELECT RAISE(ABORT, 'owned short terminal storage fault'); END""" % marker)
            blocking = request("/api/v2/jobs", {"kind": "report", "params": {**params, "title": marker}}, expected=202)
            wait(lambda: request("/api/v2/reports")["total"] == 1, "blocking report was not saved")
            check("report is saved while final status remains pending", request("/api/v2/jobs/" + blocking["id"])["status"] == "running")
            def retry(index):
                branch = f"jobs/{failed['id']}" if index % 2 else f"job-batches/{batch_id}"
                return request("/api/v2/" + branch + "/retry", {}, "POST", expected=None)
            with ThreadPoolExecutor(max_workers=8) as pool:
                replies = list(pool.map(retry, range(8)))
            statuses = [status for status, _ in replies]
            result["concurrent_retry_statuses"] = statuses
            check("mixed concurrent job and batch retries reserve one branch", statuses.count(202) == 1 and statuses.count(409) == 7)
            accepted = next(body for status, body in replies if status == 202)
            retried = accepted["jobs"][0] if "jobs" in accepted else accepted
            lineage = request("/api/v2/jobs/" + retried["id"])["params"]["_workbench_retry"]
            check("retry lineage is durable and original parameters are preserved",
                  lineage == {"parent_job_id": failed["id"], "root_job_id": failed["id"]}
                  and request("/api/v2/jobs/" + failed["id"])["params"] == original_params)
            check("history contains only one retry", request("/api/v2/jobs")["total"] == 3)
            following = request("/api/v2/jobs", {"kind": "report", "params": {**params, "title": "Following owned report"}}, expected=202)
            time.sleep(0.4)
            check("pending terminal write retains lane and does not claim following work",
                  request("/api/v2/jobs/" + retried["id"])["status"] == "queued"
                  and request("/api/v2/jobs/" + following["id"])["status"] == "queued"
                  and request("/api/v2/reports")["total"] == 1)
            with connection() as con:
                con.execute("DROP TRIGGER daily_terminal_fault")
            finished = [terminal(request, job_id) for job_id in (blocking["id"], retried["id"], following["id"])]
            check("cleared fault completes original receipt and subsequent reports", all(job["status"] == "succeeded" for job in finished))
            reports = request("/api/v2/reports")["items"]
            check("each successful job has one report without repeat generation",
                  len(reports) == 3 and {row["job_id"] for row in reports} == {job["id"] for job in finished})
            receipt = request("/api/v2/reports/" + finished[0]["result"]["report_id"])
            check("recovered receipt points to saved source snapshot", receipt["job_id"] == blocking["id"]
                  and receipt["source_count"] == 1 and material["url"] in receipt["markdown"])
            request(f"/api/v2/jobs/{failed['id']}/retry", {}, "POST", expected=422)
            request(f"/api/v2/job-batches/{batch_id}/retry", {}, "POST", expected=422)
            check("successful descendant prevents historical job and batch retry", request("/api/v2/jobs")["total"] == 4)
            check("all owned work has terminal status", request("/api/v2/overview")["stats"]["active_jobs"] == 0)
            result["report_count"], result["job_count"] = 3, 4
        with backend() as request:
            request(f"/api/v2/jobs/{failed['id']}/retry", {}, "POST", expected=422)
            request(f"/api/v2/job-batches/{batch_id}/retry", {}, "POST", expected=422)
            check("restart retains receipts and successful retry guard", request("/api/v2/jobs")["total"] == 4
                  and request("/api/v2/reports")["total"] == 3)
        result["ok"] = True
    except BaseException as error:
        result["ok"], result["failure"] = False, str(error)
        raise
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "daily-behavior.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
