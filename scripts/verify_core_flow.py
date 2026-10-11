"""Opt-in live retrieval -> durable storage -> report -> replay acceptance.

Uses an empty isolated database and the configured connection from environment.
Never prints keys, cookies or model reasoning. Network failures are evidence,
not an empty successful collection. Not part of the offline pytest suite.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
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
    from app.workbench import pipeline
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    database = output / "workbench.sqlite3"
    if database.exists():
        raise ValueError("Live acceptance requires a new empty profile; retain prior evidence.")
    store = Store(database)
    runner = JobRunner(store)
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "isolated_database": str(database),
              "live_network": True, "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
              "checks": [], "jobs": [], "ok": False}

    def check(name, condition, details=None):
        result["checks"].append({"name": name, "ok": bool(condition), "details": details})
        if not condition:
            raise AssertionError(name)

    async def task(kind, params):
        started = time.monotonic()
        row = store.create_job(kind, params)
        runner.wake.set()
        deadline = time.monotonic() + 250
        while time.monotonic() < deadline:
            row = store.get_job(row["id"])
            if row["status"] in {"succeeded", "partial", "failed", "cancelled", "interrupted"}:
                result["jobs"].append({"id": row["id"], "kind": kind, "status": row["status"],
                                       "result": row.get("result"), "error": row.get("error"),
                                       "elapsed_seconds": round(time.monotonic() - started, 3)})
                return row
            await asyncio.sleep(.2)
        raise AssertionError("bounded job did not terminate")

    try:
        await runner.start()
        today = datetime.now(timezone(timedelta(hours=9))).date()
        params = {"source": "google_news", "keyword": args.keyword, "start_date": str(today-timedelta(days=30)),
                  "end_date": str(today), "max_records": 8}
        collection = await task("collect", params)
        check("live public retrieval and queue terminal", collection["status"] in {"succeeded", "partial"}, collection.get("result"))
        items = store.list_items(page_size=200)["items"]
        check("nonempty saved sources", bool(items), {"saved": len(items)})
        check("source URLs dates and provenance retained", all(row["url"].startswith("https://") and row["published_at"]
              and row["metadata"].get("source_url") and row["metadata"].get("collected_at") for row in items))
        (output / "live-sources.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")
        before_ids = {row["url"]: row["id"] for row in items}
        replay = await task("collect", params)
        check("repeat retrieval remains usable", replay["status"] in {"succeeded", "partial"}, replay.get("result"))
        after = store.list_items(page_size=200)["items"]
        check("URL identity stable and no duplicate URLs", len({row["url"] for row in after}) == len(after)
              and all(before_ids.get(row["url"], row["id"]) == row["id"] for row in after))
        if args.original_url:
            selected = after[0]
            capture = await task("collect", {"_news_capture": True, "item_id": selected["id"], "original_url": args.original_url})
            check("publisher public article captured", capture["status"] == "succeeded", capture.get("result"))
            captured = store.get_item(selected["id"])
            check("body improvement retains original ID and prior summary", captured["id"] == selected["id"]
                  and captured["metadata"]["content_kind"] == "public_article_body" and len(captured["content"]) > len(selected["content"])
                  and store.item_revisions(selected["id"])["total"] >= 1)
            replay = await task("collect", params)
            check("later summary does not erase publisher body", store.get_item(selected["id"])["content"] == captured["content"])
            after = store.list_items(page_size=200)["items"]
            (output / "captured-sources.json").write_text(json.dumps(after, ensure_ascii=False, indent=2), "utf-8")
        scope = {"start_date": params["start_date"], "end_date": params["end_date"], "include_news": True, "include_social": False,
                 "title": "真实检索主流程验收 · " + args.keyword, "read_original": True}
        modes = ["evidence", "ai"] if args.real_ai else ["evidence"]
        for mode in modes:
            job = await task("report", {**scope, "mode": mode})
            check(mode + " report generated from actual saved sources", job["status"] == "succeeded", job.get("result"))
            report = store.get_report(job["result"]["report_id"])
            snapshot = report["snapshot"]
            if args.min_public_bodies:
                body_count = sum((row.get("metadata") or {}).get("content_kind") == "public_article_body" for row in snapshot["items"])
                check(mode + " public body coverage", body_count >= args.min_public_bodies,
                      {"body_sources": body_count, "total_sources": len(snapshot["items"])})
            check(mode + " immutable input and mode", report["mode"] == mode and bool(snapshot["items"])
                  and {row["id"] for row in snapshot["items"]} <= {row["id"] for row in after})
            if mode == "ai":
                pipeline._validate_ai_citations(report["markdown"].split("\n---\n", 1)[0], snapshot["items"])
                review = snapshot["analysis_review"]
                check("every eligible source has a checked exact quote", len(review["source_reviews"]) == len(snapshot["items"]))
            (output / (mode + "-report.md")).write_text(report["markdown"], "utf-8")
            (output / (mode + "-snapshot.json")).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), "utf-8")
        await runner.stop()
        await runner.start()
        check("worker restart retains terminal jobs and reports", all(store.get_job(row["id"])["status"] == row["status"] for row in result["jobs"])
              and store.list_reports()["total"] == len(modes))
        with sqlite3.connect(database) as db:
            check("integrity and foreign keys", db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                  and not db.execute("PRAGMA foreign_key_check").fetchall())
        result["ok"] = True
    except Exception as exc:
        result["failure"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
    finally:
        await runner.stop()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["saved_database_sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
        (output / "core-acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps({"ok": result["ok"], "checks": len(result["checks"]), "failure": result.get("failure"),
                          "receipt": str(output / "core-acceptance.json")}, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--backend-root", default=str(Path(__file__).resolve().parents[1] / "analysis-worker"))
    parser.add_argument("--keyword", default="憲法九条")
    parser.add_argument("--real-ai", action="store_true", help="Explicitly call the configured paid model.")
    parser.add_argument("--original-url", default="", help="Explicit publisher URL for the first selected source; title must match.")
    parser.add_argument("--min-public-bodies", type=int, default=0, help="Fail unless the report really includes at least this many captured publisher bodies.")
    raise SystemExit(asyncio.run(accept(parser.parse_args())))
