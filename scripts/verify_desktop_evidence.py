"""Verify native smoke output and its downloaded database with bundled Python."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    profile = Path(args.profile).resolve()
    smoke = json.loads((profile / "desktop-smoke.json").read_text("utf-8"))
    exit_record = json.loads((profile / "native-exit.json").read_text("utf-8-sig"))
    checks = smoke["checks"]
    assert exit_record["exit_code"] == 0 and smoke["health"]["ok"]
    assert not list(profile.glob("ready-*.json"))
    desktop_log = (profile / "logs/desktop.log").read_text("utf-8")
    backend_log = (profile / "logs/backend.log").read_text("utf-8")
    assert "Backend exited code=0" in desktop_log
    assert not re.search(r"\bERROR\b|Future exception", backend_log)
    assert checks["secure_storage"]
    assert checks["collection"]["result"]["inserted"] > 0
    assert checks["deduplication"]["duplicates"] > 0 and checks["deduplication"]["inserted"] == 0
    assert checks["report"]["source_count"] > 0
    assert checks["budgets"]["boundary_preview_ready"] and checks["budgets"]["preview_created_jobs"] == 0
    for name in ("overview", "workspace"):
        typography = checks["typography"][name]
        assert not typography["horizontal_overflow"] and typography["weight"] == "900"
        for family in ("Atlas Mincho", "Atlas Signal"):
            assert any(font["family"] == family and font["status"] == "loaded" for font in typography["fonts"])
    backup = checks["backup"]
    snapshot = Path(backup["file"]).resolve()
    assert snapshot.is_relative_to(profile / "smoke-downloads")
    assert backup["state"] == "completed" and backup["source_draft_unchanged"]
    with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        draft, revision = connection.execute("SELECT data,revision FROM wb_drafts WHERE key='collection'").fetchone()
        assert json.loads(draft)["values"]["keyword"] == backup["draft_keyword"]
        assert revision == backup["draft_revision"]
        items = connection.execute("SELECT COUNT(*) FROM wb_items").fetchone()[0]
        reports = connection.execute("SELECT COUNT(*) FROM wb_reports").fetchone()[0]
        assert items == smoke["overview"]["stats"]["items"] and reports == smoke["overview"]["stats"]["reports"]
    record = {"ok": True, "executable": exit_record["executable"], "profile": str(profile),
              "native_exit_code": 0, "backend_exit_code": 0, "ready_files_remaining": 0,
              "backend_log_errors": 0, "secure_storage": True, "live_news_inserted": items,
              "repeat_duplicates": checks["deduplication"]["duplicates"], "evidence_reports": reports,
              "backup": {"file": str(snapshot), "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                         "size_bytes": snapshot.stat().st_size, "quick_check": "passed", "foreign_keys": "passed",
                         "recent_committed_draft": "passed", "records": items, "reports": reports},
              "budgets_preview_without_enqueue": "passed", "offline_fonts_and_layout": "passed",
              "raw_result": str(profile / "desktop-smoke.json")}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
