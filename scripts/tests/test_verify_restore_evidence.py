"""Frozen release evidence contract: only complete 21-to-22 migration is allowed."""
import ast
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("restore_evidence_verifier", ROOT / "scripts/verify_restore_evidence.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)

OLD_TABLES = frozenset({
    "wb_accounts", "wb_items", "wb_jobs", "wb_job_events", "wb_reports", "wb_account_keys",
    "wb_item_revisions", "wb_relations", "wb_job_batches", "wb_job_batch_jobs",
    "wb_research_runs", "wb_research_jobs", "wb_research_nodes", "wb_research_items",
    "wb_research_edges", "wb_research_observations", "wb_research_claims",
    "wb_research_claim_reviews", "wb_research_claim_observations", "wb_drafts", "wb_draft_revisions",
})
DEFERRED_SQL = """CREATE TABLE IF NOT EXISTS wb_research_deferred (
    run_id TEXT NOT NULL REFERENCES wb_research_runs(id), account_key TEXT NOT NULL,
    component TEXT NOT NULL CHECK(component IN ('header','items','relations','discovery_paths','evidence_urls')),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0), record TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id,account_key,component,ordinal))"""


def database(*, deferred=False):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    for table in sorted(OLD_TABLES):
        connection.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, payload TEXT)")
    if deferred:
        connection.execute(DEFERRED_SQL)
    return connection


def cached_row(connection, body="saved source body"):
    connection.execute("INSERT INTO wb_research_deferred VALUES ('run','twitter:child','items',0,?, '2026-10-09T00:00:00Z')", (body,))


def snapshot(connection):
    return tuple(connection.iterdump())


def test_fixed_empty_deferred_table_is_the_only_old_to_new_extension():
    with closing(database()) as source, closing(database(deferred=True)) as installed:
        before = snapshot(source)
        checked = verifier.verify_table_compatibility(source, installed)
        assert checked == sorted(OLD_TABLES | {"wb_research_deferred"})
        assert snapshot(source) == before


def test_current_to_current_keeps_all_schema_and_cached_rows_exact():
    with closing(database(deferred=True)) as source, closing(database(deferred=True)) as installed:
        cached_row(source)
        cached_row(installed)
        before = snapshot(source)
        assert len(verifier.verify_table_compatibility(source, installed)) == 22
        assert snapshot(source) == before


def test_frozen_deferred_schema_matches_the_actual_backend_constant():
    tree = ast.parse((ROOT / "analysis-worker/app/workbench/research_frontier.py").read_text("utf-8"))
    declaration = next(node for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "DEFERRED_TABLE_SQL" for target in node.targets))
    assert ast.literal_eval(declaration.value) == DEFERRED_SQL == verifier.DEFERRED_TABLE_SQL


@pytest.mark.parametrize("target,mutation", [
    ("installed", "DROP TABLE wb_job_events"),
    ("source", "DROP TABLE wb_job_events"),
    ("installed", "CREATE TABLE unknown_extension(value TEXT)"),
    ("source", "CREATE TABLE unknown_extension(value TEXT)"),
    ("installed", "ALTER TABLE wb_items ADD COLUMN extra TEXT"),
    ("source", "ALTER TABLE wb_items ADD COLUMN extra TEXT"),
    ("installed", "CREATE INDEX extra_evidence_index ON wb_items(payload)"),
    ("installed", "CREATE VIEW extra_evidence_view AS SELECT * FROM wb_items"),
    ("installed", "CREATE TRIGGER extra_evidence_trigger AFTER INSERT ON wb_items BEGIN DELETE FROM wb_items; END"),
])
def test_migration_never_allows_other_missing_extra_or_changed_schema(target, mutation):
    with closing(database()) as source, closing(database(deferred=True)) as installed:
        (source if target == "source" else installed).execute(mutation)
        with pytest.raises(AssertionError):
            verifier.verify_table_compatibility(source, installed)


@pytest.mark.parametrize("sql", [
    DEFERRED_SQL.replace("ordinal >= 0", "ordinal >= -1"),
    DEFERRED_SQL.replace("record TEXT NOT NULL", "record TEXT"),
    DEFERRED_SQL.replace("REFERENCES wb_research_runs(id)", ""),
    DEFERRED_SQL.replace("component,ordinal)", "component)"),
    DEFERRED_SQL.replace("'evidence_urls'", "'unsafe_extension'"),
])
def test_migration_rejects_a_deferred_table_with_weaker_or_different_schema(sql):
    with closing(database()) as source, closing(database()) as installed:
        installed.execute(sql)
        with pytest.raises(AssertionError):
            verifier.verify_table_compatibility(source, installed)


def test_old_to_old_is_not_a_completed_installation():
    with closing(database()) as source, closing(database()) as installed:
        with pytest.raises(AssertionError):
            verifier.verify_table_compatibility(source, installed)


def test_old_to_new_cannot_invent_cached_work():
    with closing(database()) as source, closing(database(deferred=True)) as installed:
        cached_row(installed)
        with pytest.raises(AssertionError):
            verifier.verify_table_compatibility(source, installed)


def test_current_to_current_cannot_lose_or_change_cached_work():
    with closing(database(deferred=True)) as source, closing(database(deferred=True)) as installed:
        cached_row(source)
        with pytest.raises(AssertionError):
            verifier.verify_table_compatibility(source, installed)
        cached_row(installed, "changed source body")
        with pytest.raises(AssertionError):
            verifier.verify_table_compatibility(source, installed)


def test_current_to_old_cannot_remove_saved_frontier():
    with closing(database(deferred=True)) as source, closing(database()) as installed:
        with pytest.raises(AssertionError):
            verifier.verify_table_compatibility(source, installed)


def test_current_to_current_does_not_relax_an_identically_forged_deferred_schema():
    with closing(database()) as source, closing(database()) as installed:
        for connection in (source, installed):
            connection.execute(DEFERRED_SQL.replace("ordinal >= 0", "ordinal >= -1"))
        with pytest.raises(AssertionError):
            verifier.verify_table_compatibility(source, installed)
