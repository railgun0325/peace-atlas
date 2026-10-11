"""Independently check the native restore result against SQLite evidence.

Read-only verification of isolated source, installed database and retained old
database. No credentials are decrypted and no external service is contacted.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3


# This verifier is independent of backend imports and deliberately freezes the
# one permitted release migration. A later schema needs a reviewed contract.
PRIOR_TABLES = frozenset({
    "wb_accounts", "wb_items", "wb_jobs", "wb_job_events", "wb_reports", "wb_account_keys",
    "wb_item_revisions", "wb_relations", "wb_job_batches", "wb_job_batch_jobs",
    "wb_research_runs", "wb_research_jobs", "wb_research_nodes", "wb_research_items",
    "wb_research_edges", "wb_research_observations", "wb_research_claims",
    "wb_research_claim_reviews", "wb_research_claim_observations", "wb_drafts", "wb_draft_revisions",
})
DEFERRED_TABLE_SQL = """CREATE TABLE IF NOT EXISTS wb_research_deferred (
    run_id TEXT NOT NULL REFERENCES wb_research_runs(id), account_key TEXT NOT NULL,
    component TEXT NOT NULL CHECK(component IN ('header','items','relations','discovery_paths','evidence_urls')),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0), record TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id,account_key,component,ordinal))"""


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def read_json(path):
    return json.loads(path.read_text("utf-8-sig"))


def open_readonly(path):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    return connection


def table_rows(connection, table):
    assert re.fullmatch(r"wb_[a-z_]+", table)
    return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]


def verify_table_compatibility(original, installed):
    """Accept exact current schema or the one empty-table prior release upgrade."""
    current_tables = PRIOR_TABLES | {"wb_research_deferred"}
    table_names = lambda connection: {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table'")}
    source_tables = table_names(original)
    assert source_tables in (PRIOR_TABLES, current_tables), "source must have the fixed complete 21- or 22-table schema"
    assert table_names(installed) == current_tables, "installed database must have exactly the fixed 22 tables"

    def schema(connection, *, deferred):
        operator = "=" if deferred else "<>"
        return [tuple(row) for row in connection.execute(
            f"SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE tbl_name {operator} ? ORDER BY type,name",
            ("wb_research_deferred",))]

    # Compare SQL bytes, not a broad normalizer that could conceal a changed
    # constraint/index/trigger. SQLite itself removes IF NOT EXISTS from DDL.
    assert schema(original, deferred=False) == schema(installed, deferred=False), "existing schema changed during restore"
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.execute(DEFERRED_TABLE_SQL)
        frozen_deferred = schema(expected, deferred=True)
    assert schema(installed, deferred=True) == frozen_deferred, "installed deferred schema differs from the fixed DDL"
    if "wb_research_deferred" in source_tables:
        assert schema(original, deferred=True) == frozen_deferred, "source deferred schema differs from the fixed DDL"
        assert table_rows(original, "wb_research_deferred") == table_rows(installed, "wb_research_deferred"), "saved deferred evidence changed"
    else:
        assert installed.execute("SELECT COUNT(*) FROM wb_research_deferred").fetchone()[0] == 0, "legacy restore invented deferred work"
    for pragma in ("application_id", "user_version"):
        assert original.execute(f"PRAGMA {pragma}").fetchone()[0] == installed.execute(f"PRAGMA {pragma}").fetchone()[0], "database version changed"
    return sorted(current_tables)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--fixture-metadata", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    profile = Path(args.profile).resolve()
    metadata_path = Path(args.fixture_metadata).resolve()
    output = Path(args.output).resolve()
    assert not output.is_relative_to(profile), "verification output must be outside the protected acceptance profile"
    assert output != metadata_path, "verification output cannot replace fixture expectations"
    metadata = read_json(metadata_path)
    smoke = read_json(profile / "desktop-smoke.json")
    exit_record = read_json(profile / "native-exit.json")
    restored = smoke["checks"]["restore"]
    assert exit_record["exit_code"] == 0 and smoke["health"]["ok"]
    assert not list(profile.glob("ready-*.json"))
    assert not (profile / ".restore-transaction.json").exists()
    desktop_log = (profile / "logs/desktop.log").read_text("utf-8")
    backend_log = (profile / "logs/backend.log").read_text("utf-8")
    assert desktop_log.count("Backend exited code=0") >= 2
    assert not re.search(r"\bERROR\b|Future exception", backend_log)
    assert smoke["checks"]["secure_storage"]
    for flag in ("source_unchanged", "queued_interrupted", "original_preserved", "connection_unchanged",
                 "social_unchanged", "markers_differ", "ui_fixture_visible", "same_browser_window",
                 "backend_restarted", "session_token_changed"):
        assert restored[flag], flag
    assert restored["active_jobs"] == 0 and restored["native_dialogs"] == ["open", "confirm"]
    assert restored["source_marker"] == metadata["marker"] == restored["current_marker"]
    source = Path(restored["source_file"]).resolve()
    assert output != source, "verification output cannot replace the source backup"
    assert source == Path(metadata["source_path"]).resolve()
    assert digest(source) == metadata["source_sha256"] == restored["source_sha256"] == restored["source_sha256_after"]
    archive = Path(restored["archive_path"]).resolve()
    assert archive.is_relative_to(profile / "restores")
    archived_files = restored["archived_files"]
    assert {entry.name for entry in archive.iterdir()} == {entry["name"] for entry in archived_files}
    for entry in archived_files:
        archived_path = (archive / entry["name"]).resolve()
        assert archived_path.parent == archive and archived_path.is_file()
        assert archived_path.stat().st_size == entry["size_bytes"] and digest(archived_path) == entry["sha256"]
    current = profile / "workbench.sqlite3"
    old = archive / "workbench.sqlite3"
    for files in (source, current, old):
        assert files.is_file()
        assert not Path(str(files) + "-wal").exists(), "verification requires closed independent databases"
    assert digest(profile / "connection.secure") == restored["connection_sha256"]
    social = Path(restored["social_sentinel"]).resolve()
    assert social.is_relative_to(profile / "social")
    assert digest(social) == restored["social_sha256"]
    expected_counts = restored["summary"]["counts"]
    changed_jobs = []
    checked_tables = []
    with closing(open_readonly(source)) as original, closing(open_readonly(current)) as installed, closing(open_readonly(old)) as prior:
        changed_jobs = [row[0] for row in original.execute("SELECT id FROM wb_jobs WHERE status IN ('queued','running') ORDER BY id")]
        tables = [row[0] for row in original.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")]
        installed_tables = verify_table_compatibility(original, installed)
        for table in tables:
            before, after = table_rows(original, table), table_rows(installed, table)
            if table == "wb_jobs":
                assert len(before) == len(after)
                lookup = {row["id"]: row for row in after}
                for entry in before:
                    result = lookup[entry["id"]]
                    if entry["status"] in {"queued", "running"}:
                        assert result["status"] == "interrupted" and result["cancel_requested"] == 1
                        assert result["finished_at"] and "恢复" in result["error"]
                        fields = set(entry) - {"status", "finished_at", "cancel_requested", "error"}
                        assert all(result[key] == entry[key] for key in fields)
                    else:
                        assert result == entry
            elif table == "wb_research_runs":
                assert len(before) == len(after)
                lookup = {row["id"]: row for row in after}
                for entry in before:
                    result = lookup[entry["id"]]
                    if entry["status"] in {"queued", "running"} or entry["current_job_id"] in changed_jobs:
                        assert result["status"] == "interrupted"
                        assert all(result[key] == entry[key] for key in set(entry) - {"status", "updated_at"})
                    else:
                        assert result == entry
            elif table == "wb_job_events":
                lookup = {row["id"]: row for row in after}
                assert all(lookup[row["id"]] == row for row in before)
                extra = [row for row in after if row["id"] not in {entry["id"] for entry in before}]
                assert len(extra) == len(changed_jobs)
                assert {row["job_id"] for row in extra} == set(changed_jobs)
                assert all(row["level"] == "warning" and "恢复" in row["message"] for row in extra)
            else:
                assert before == after, f"saved evidence changed in {table}"
            checked_tables.append(table)
        if "wb_research_deferred" not in tables:
            checked_tables.append("wb_research_deferred")
        assert sorted(checked_tables) == installed_tables
        for label, table in {"items": "wb_items", "accounts": "wb_accounts", "reports": "wb_reports",
                             "research_runs": "wb_research_runs", "jobs": "wb_jobs", "drafts": "wb_drafts"}.items():
            assert installed.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == expected_counts[label]
        assert len(changed_jobs) == restored["summary"]["interrupted_jobs_count"]
        assert installed.execute("SELECT COUNT(*) FROM wb_jobs WHERE status IN ('queued','running')").fetchone()[0] == 0
        draft = installed.execute("SELECT data FROM wb_drafts WHERE key='collection'").fetchone()[0]
        prior_draft = prior.execute("SELECT data FROM wb_drafts WHERE key='collection'").fetchone()[0]
        assert json.loads(draft)["values"]["keyword"] == metadata["marker"]
        assert json.loads(prior_draft)["values"]["keyword"] == restored["original_current_marker"]
        assert prior.execute("SELECT COUNT(*) FROM wb_items WHERE title=?", (restored["original_current_marker"],)).fetchone()[0] == 1
        # Compare the complete original records captured by the native HTTP
        # check, rather than accepting a matching marker in a changed archive.
        prior_items = []
        for row in table_rows(prior, "wb_items"):
            item_metadata = json.loads(row.pop("metadata"))
            item = {**item_metadata, **row, "metadata": item_metadata}
            for field in ("topics", "entities", "locations"):
                item[field] = json.loads(item[field])
            item["publication_date_unknown"] = bool(item["publication_date_unknown"])
            item["url"] = item["url"] or ""
            if item["type"] == "social":
                item["account_status"] = "candidate"
            prior_items.append(item)
        assert sorted(prior_items, key=lambda row: row["id"]) == sorted(restored["before"]["items"], key=lambda row: row["id"])
        prior_jobs = table_rows(prior, "wb_jobs")
        for row in prior_jobs:
            row["params"] = json.loads(row["params"])
            row["result"] = json.loads(row["result"]) if row["result"] is not None else None
            row["cancel_requested"] = bool(row["cancel_requested"])
        assert sorted(prior_jobs, key=lambda row: row["id"]) == sorted(restored["before"]["jobs"], key=lambda row: row["id"])
        prior_draft_row = prior.execute("SELECT * FROM wb_drafts WHERE key='collection'").fetchone()
        decoded_prior_draft = {**json.loads(prior_draft_row["data"]),
                               "revision": prior_draft_row["revision"], "updated_at": prior_draft_row["updated_at"]}
        assert decoded_prior_draft == restored["before"]["draft"] and prior_draft_row["version"] == 1
        assert prior.execute("SELECT COUNT(*) FROM wb_drafts").fetchone()[0] == 1
        assert len(prior_items) == restored["before"]["overview"]["stats"]["items"]
        assert prior.execute("SELECT COUNT(*) FROM wb_reports").fetchone()[0] == restored["before"]["overview"]["stats"]["reports"]
    assert digest(source) == metadata["source_sha256"], "source backup changed during verification"
    record = {"ok": True, "profile": str(profile), "executable": exit_record["executable"],
              "external_services_used": False, "native_exit_code": 0, "backend_clean_exits": desktop_log.count("Backend exited code=0"),
              "ready_files_remaining": 0, "restore_journal_remaining": False,
              "original_source_sha256": digest(source), "original_source_unchanged": True,
              "installed_database": str(current), "retained_old_database": str(old),
              "sqlite_integrity_and_foreign_keys": "passed", "saved_evidence_tables_compared": checked_tables,
              "schema_compatibility": "fixed_21_to_22_empty_deferred" if len(tables) == 21 else "exact_22_to_22",
              "fixed_deferred_schema_verified": True, "existing_schema_unchanged": True,
              "restored_counts": expected_counts, "interrupted_jobs": changed_jobs,
              "terminal_history_unchanged": True, "research_and_report_evidence_unchanged": True,
              "old_database_marker_preserved": True, "connection_and_social_sentinel_unchanged": True,
              "archived_file_hashes_checked": True, "original_items_jobs_and_draft_compared": True,
              "native_selection_and_confirmation": "isolated acceptance branch passed; source and confirmation injected",
              "operating_system_dialogs_manually_exercised": False, "native_ui_fixture_visible": True,
              "raw_result": str(profile / "desktop-smoke.json")}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"ok": True, "tables_compared": len(checked_tables), "interrupted_jobs": len(changed_jobs),
                      "retained_old_database": str(old)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
