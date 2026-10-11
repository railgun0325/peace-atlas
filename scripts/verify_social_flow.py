"""Opt-in public X -> independent account backlink -> grounded AI acceptance.

Requires an already user-operated login session and the configured model.
Creates a new empty database, never exports cookies or changes formal data.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


async def accept(args):
    sys.path.insert(0, str(Path(args.backend_root).resolve()))
    from app.workbench.jobs import JobRunner
    from app.workbench.store import Store
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    database = output / "workbench.sqlite3"
    if database.exists(): raise ValueError("Select a new empty acceptance profile.")
    os.environ["WORKBENCH_SOCIAL_DIR"] = str(Path(args.social_dir).resolve())
    store, result = Store(database), {"ok": False, "live_public_network": True, "checks": [], "jobs": [], "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash")}
    runner = JobRunner(store)
    def check(name, condition, details=None):
        result["checks"].append({"name": name, "ok": bool(condition), "details": details})
        if not condition: raise AssertionError(name)
    async def task(kind, params):
        start = time.monotonic()
        row = store.create_job(kind, params)
        runner.wake.set()
        async with asyncio.timeout(360):
            while row["status"] not in {"failed", "partial", "succeeded", "cancelled", "interrupted"}:
                await asyncio.sleep(.2)
                row = store.get_job(row["id"])
        result["jobs"].append({"kind": kind, "status": row["status"], "result": row["result"], "error": row["error"], "elapsed_seconds": round(time.monotonic()-start, 3)})
        return row
    try:
        account = store.save_account({"platform": "twitter", "handle": args.handle, "name": args.handle, "url": "https://x.com/" + args.handle})
        await runner.start()
        verified = await task("collect", {"_account_verification": True, "account_ids": [account["id"]]})
        current = store.get_account(account["id"])
        check("independent exact public backlink verifies account", current["status"] == "verified" and bool(current["evidence_url"]), verified["result"])
        today = datetime.now(timezone(timedelta(hours=9))).date()
        period = {"start_date": str(today-timedelta(days=30)), "end_date": str(today)}
        collected = await task("collect", {**period, "source": "twitter", "mode": "account", "handle": args.handle,
                                "max_records": 3, "max_accounts": 1, "max_scrolls": 2, "relevance": "broad", "include_following": False})
        check("actual public timeline yields saved messages", collected["status"] in {"succeeded", "partial"} and store.overview()["stats"]["social"] > 0, collected["result"])
        modes = ["evidence", "ai"] if args.real_ai else ["evidence"]
        for mode in modes:
            row = await task("report", {**period, "mode": mode, "title": "公开账号消息分析验收", "include_news": False, "include_social": True, "read_original": False})
            check(mode + " social report succeeds", row["status"] == "succeeded", row["result"])
            report = store.get_report(row["result"]["report_id"])
            items = report["snapshot"]["items"]
            check(mode + " uses real verified message evidence", bool(items) and all(item["account_status"] == "verified" and item["use_in_conclusions"] and item["url"].startswith("https://x.com/") for item in items))
            if mode == "ai":
                check("exact quote review covers every message", len(report["snapshot"]["analysis_review"]["source_reviews"]) == len(items))
            (output / (mode + "-report.md")).write_text(report["markdown"], "utf-8")
            (output / (mode + "-snapshot.json")).write_text(json.dumps(report["snapshot"], ensure_ascii=False, indent=2), "utf-8")
        with sqlite3.connect(database) as db:
            check("database integrity and foreign keys", db.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and not db.execute("PRAGMA foreign_key_check").fetchall())
        result["ok"] = True
    except Exception as exc:
        result["failure"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
    finally:
        await runner.stop()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "social-flow-acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps({"ok": result["ok"], "checks": len(result["checks"]), "failure": result.get("failure")}, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--social-dir", required=True, help="Existing local workbench session directory, read only.")
    parser.add_argument("--backend-root", default=str(Path(__file__).resolve().parents[1] / "analysis-worker"))
    parser.add_argument("--handle", default="peace_boat")
    parser.add_argument("--real-ai", action="store_true")
    raise SystemExit(asyncio.run(accept(parser.parse_args())))
