"""Desktop restore preparation preserves evidence and rejects unsafe inputs."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest

from app.workbench.backup import BackupService
from app.workbench import restore
from app.workbench.restore import RestoreError, prepare_restore
from app.workbench.research_export import research_markdown
from app.workbench.store import Store


MARKER = "恢复验收资料20261008"


def populated_backup(directory: Path) -> tuple[Path, dict]:
    """A complete real backup, including queued/running/terminal history."""
    store = Store(directory / "live.sqlite3")
    try:
        material = {"title": MARKER, "url": "https://example.org/restore-marker",
                    "content": "憲法九条と公開平和資料", "type": "news",
                    "published_at": "2026-10-08T09:00:00+09:00", "topics": ["平和"], "entities": ["公開団体"]}
        imported = store.import_items([material])
        account = store.save_account({"name": MARKER, "platform": "twitter", "handle": "restore_marker",
                                      "status": "verified", "evidence_url": "https://example.org/official"})
        draft = store.save_draft("collection", {"schema": 1, "values": {"keyword": MARKER}})
        report = store.save_report({"title": MARKER, "mode": "evidence", "markdown": f"# {MARKER}\n原始资料",
                                   "snapshot": {"version": 1, "items": [material]}, "source_count": 1})
        queued = store.create_job("collect", {"sources": ["google_news"], "keywords": [MARKER]})
        store.add_job_event(queued["id"], "恢复前的等待任务事件")
        running = store.create_job("research", {"keywords": ["平和"]})
        run = store.create_research_run(running["id"], running["params"])
        store.update_job(running["id"], status="running", progress=20)
        store.add_job_event(running["id"], "恢复前已保存检查点")
        store.checkpoint_research(run["id"], status="running", phase="expand",
            nodes=[{"account_key": "twitter:restore_marker", "name": MARKER, "depth": 0, "status": "visited"},
                   {"account_key": "twitter:restore_next", "depth": 1, "status": "pending"}],
            items=[{"title": "公开研究材料", "content": "公開反戦集会", "type": "social",
                    "account_key": "twitter:restore_marker", "url": "https://x.com/restore_marker/status/123"}],
            edges=[{"source_key": "twitter:restore_marker", "target_key": "twitter:restore_next",
                    "relationship": "follows", "basis": "observed", "evidence_urls": ["https://x.com/restore_marker/following"]}],
            claims=[{"subject_key": "twitter:restore_marker", "relationship": "affiliation", "basis": "inferred",
                     "object_label": "公开组织候选", "statement": "需要人工核对", "evidence_urls": ["https://example.org/official"]}],
            frontier=[{"account_key": "twitter:restore_next", "depth": 1}], visited=["twitter:restore_marker"],
            summary={"visited": 1}, query_plan={"queries": ["平和"]}, diagnostics=[{"code": "saved"}],
            analysis={"findings": ["保留已记录证据"]}, metadata={"checkpoint_marker": MARKER})
        finished = store.create_job("collect", {})
        store.update_job(finished["id"], status="succeeded", result={"inserted": 1})
        partial = store.create_job("research", {})
        store.create_research_run(partial["id"], {})
        store.checkpoint_research(partial["id"], status="partial", phase="budget", summary={"saved": 1})
        store.update_job(partial["id"], status="partial", result={"run_id": partial["id"]})
        response = BackupService().response(store)
        source = directory / "完整备份.sqlite3"
        try:
            source.write_bytes(Path(response.path).read_bytes())
        finally:
            response.directory.cleanup()
        return source, {"item": imported["ids"][0], "account": account["id"], "draft": draft,
                        "report": report["id"], "queued": queued["id"], "running": running["id"],
                        "run": run["id"], "finished": finished["id"], "partial": partial["id"]}
    finally:
        store.close()


@pytest.fixture
def backup(tmp_path):
    return populated_backup(tmp_path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mutate(path, sql, params=()):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(sql, params)
        connection.commit()


def reject_unchanged(source, stage, expected_code):
    before, names = source.read_bytes(), set(source.parent.iterdir())
    with pytest.raises(RestoreError) as error:
        prepare_restore(source, stage)
    assert error.value.code == expected_code
    assert error.value.message == str(error.value)
    assert source.read_bytes() == before
    assert not stage.exists()
    assert set(source.parent.iterdir()) == names


def add_retry(source, parent, root, *, kind='collect', metadata=None, identity='restore-retry-child'):
    store = Store(source)
    try:
        params = {'_workbench_retry': metadata if metadata is not None else
                  {'parent_job_id': parent, 'root_job_id': root}}
        child = store.create_job(kind, params)
        with store._transaction(write=True) as connection:
            store._execute(connection, 'UPDATE wb_jobs SET id=? WHERE id=?', (identity, child['id']))
        return identity
    finally:
        store.close()


@pytest.mark.parametrize('bad', [123, True, 'https://example.org/deleted',
    {'source': 'https://example.org/deleted'}, [123], [None], [{}], ['https://example.org/deleted', 42]])
def test_restore_rejects_unreadable_purged_source_keys_without_touching_backup(backup, tmp_path, bad):
    source, ids = backup
    mutate(source, 'UPDATE wb_research_runs SET metadata=? WHERE id=?',
           (json.dumps({'purged_source_keys': bad}), ids['run']))
    reject_unchanged(source, tmp_path / 'invalid-deletion-keys-stage', 'restore_data')


@pytest.mark.parametrize('keys', ['missing', None, [], ['https://example.org/deleted'],
    ['https://example.org/deleted', 'https://x.com/example/status/123']])
def test_restore_preserves_compatible_purged_source_keys_and_readable_research(backup, tmp_path, keys):
    source, ids = backup
    metadata = {'checkpoint_marker': MARKER}
    if keys != 'missing':
        metadata['purged_source_keys'] = keys
    # Use a current structured analysis so this test isolates the deletion-key
    # reader contract instead of the historical free-text findings fixture.
    analysis = {'evidence': [], 'findings': [], 'affiliations': []}
    mutate(source, 'UPDATE wb_research_runs SET metadata=?,analysis=? WHERE id=?',
           (json.dumps(metadata), json.dumps(analysis), ids['run']))
    before = digest(source)
    stage = tmp_path / 'compatible-deletion-keys-stage'
    prepare_restore(source, stage)
    assert digest(source) == before
    restored = Store(stage / 'workbench.sqlite3')
    try:
        run = restored.get_research_run(ids['run'])
        assert run['metadata'] == metadata
        assert run['analysis'] == analysis
        assert restored.get_report(ids['report'])['title'] == MARKER
        with restored._transaction() as connection:
            assert restored._execute(connection, 'PRAGMA foreign_key_check').fetchall() == []
    finally:
        restored.close()


def test_restore_accepts_durable_retry_chain_and_preserves_params(backup, tmp_path):
    source, ids = backup
    child = add_retry(source, ids['queued'], ids['queued'])
    grandchild = add_retry(source, child, ids['queued'], identity='restore-retry-grandchild')
    before = digest(source)
    stage = tmp_path / 'retry-stage'
    prepare_restore(source, stage)
    assert digest(source) == before
    with closing(sqlite3.connect(stage / 'workbench.sqlite3')) as connection:
        value = json.loads(connection.execute('SELECT params FROM wb_jobs WHERE id=?', (grandchild,)).fetchone()[0])
        assert value['_workbench_retry'] == {'parent_job_id': child, 'root_job_id': ids['queued']}
        assert connection.execute('SELECT status FROM wb_jobs WHERE id=?', (grandchild,)).fetchone()[0] == 'interrupted'


@pytest.mark.parametrize('bad', [None, [], {}, {'parent_job_id': '', 'root_job_id': 'x'},
    {'parent_job_id': 1, 'root_job_id': 'x'}, {'parent_job_id': 'x', 'root_job_id': 'x', 'extra': True}])
def test_restore_rejects_invalid_retry_metadata_without_touching_source(backup, tmp_path, bad):
    source, ids = backup
    child = add_retry(source, ids['queued'], ids['queued'])
    mutate(source, 'UPDATE wb_jobs SET params=? WHERE id=?', (json.dumps({'_workbench_retry': bad}), child))
    reject_unchanged(source, tmp_path / 'stage', 'restore_data')


@pytest.mark.parametrize('variant', ['missing-parent', 'wrong-kind', 'wrong-root', 'self-cycle', 'long-cycle'])
def test_restore_rejects_incoherent_retry_branch(backup, tmp_path, variant):
    source, ids = backup
    parent = 'missing-job' if variant == 'missing-parent' else ids['running'] if variant == 'wrong-kind' else ids['queued']
    root = ids['finished'] if variant == 'wrong-root' else ids['queued']
    child = add_retry(source, parent, root)
    if variant == 'self-cycle':
        mutate(source, 'UPDATE wb_jobs SET params=? WHERE id=?',
               (json.dumps({'_workbench_retry': {'parent_job_id': child, 'root_job_id': root}}), child))
    elif variant == 'long-cycle':
        mutate(source, 'UPDATE wb_jobs SET params=? WHERE id=?',
               (json.dumps({'_workbench_retry': {'parent_job_id': child, 'root_job_id': root}}), parent))
    reject_unchanged(source, tmp_path / 'stage', 'restore_data')


def test_restore_honors_legacy_event_parent_for_new_descendant(backup, tmp_path):
    source, ids = backup
    child = add_retry(source, ids['queued'], ids['queued'])
    grandchild = add_retry(source, child, ids['queued'], identity='new-retry-grandchild')
    mutate(source, 'UPDATE wb_jobs SET params=? WHERE id=?', ('{}', child))
    store = Store(source)
    try:
        store.add_job_event(child, f"由任务 {ids['queued']} 重试创建；原记录保留。")
    finally:
        store.close()
    before = digest(source)
    prepare_restore(source, tmp_path / 'stage')
    assert digest(source) == before
    restored = Store(tmp_path / 'stage' / 'workbench.sqlite3')
    try:
        assert restored.get_job(child)['params'] == {}
        assert restored.get_job(grandchild)['params']['_workbench_retry']['root_job_id'] == ids['queued']
    finally:
        restored.close()


@pytest.mark.parametrize('reason', [[], {}, True, 123])
def test_restore_rejects_invalid_retry_stop_reason_before_replacing_database(backup, tmp_path, reason):
    source, ids = backup
    result = {'run_id': ids['partial'], 'summary': {'stop_reason': reason}}
    mutate(source, 'UPDATE wb_jobs SET result=? WHERE id=?', (json.dumps(result), ids['partial']))
    reject_unchanged(source, tmp_path / 'stage', 'restore_data')


@pytest.mark.parametrize('reason', [None, '', 'time_budget', 'record_budget'])
def test_restore_retains_compatible_retry_stop_reasons_and_controls(backup, tmp_path, reason):
    source, ids = backup
    result = {'run_id': ids['partial'], 'summary': {'stop_reason': reason}}
    mutate(source, 'UPDATE wb_jobs SET result=? WHERE id=?', (json.dumps(result), ids['partial']))
    before = digest(source)
    prepare_restore(source, tmp_path / 'stage')
    assert digest(source) == before
    restored = Store(tmp_path / 'stage' / 'workbench.sqlite3')
    try:
        assert restored.get_job(ids['partial'])['result'] == result
        if reason == 'record_budget':
            prior_count = restored.list_jobs()['total']
            with pytest.raises(ValueError):
                restored.retry_job(ids['partial'])
            assert restored.list_jobs()['total'] == prior_count
        else:
            retry = restored.retry_job(ids['partial'])
            assert retry['params']['resume_run_id'] == ids['partial']
            assert retry['status'] == 'queued'
    finally:
        restored.close()


def test_complete_backup_preserves_material_reports_drafts_and_research_without_auto_queue(backup, tmp_path):
    source, ids = backup
    before = source.read_bytes()
    stage = tmp_path / "stage"
    summary = prepare_restore(source, stage)
    candidate = stage / "workbench.sqlite3"
    assert summary == json.loads((stage / "summary.json").read_text(encoding="utf-8"))
    assert summary["backup_file_name"] == source.name
    assert summary["sha256"] == digest(candidate)
    assert summary["size_bytes"] == candidate.stat().st_size
    assert summary["counts"] == {"items": 2, "accounts": 2, "reports": 1, "research_runs": 2, "jobs": 4, "drafts": 1}
    assert summary["interrupted_jobs_count"] == 2
    assert set(path.name for path in stage.iterdir()) == {"workbench.sqlite3", "summary.json"}
    assert source.read_bytes() == before
    with closing(sqlite3.connect(candidate)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM wb_jobs WHERE status IN ('queued','running')").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM wb_research_runs WHERE status IN ('queued','running')").fetchone()[0] == 0
    restored = Store(candidate)
    try:
        assert restored.list_items()["total"] == 2
        assert restored.get_item(ids["item"])["title"] == MARKER
        assert restored.get_account(ids["account"])["status"] == "verified"
        assert restored.get_report(ids["report"])["snapshot"]["items"][0]["title"] == MARKER
        assert restored.get_draft("collection") == ids["draft"]
        assert restored.get_job(ids["finished"])["status"] == "succeeded"
        assert restored.get_job(ids["partial"])["status"] == "partial"
        assert restored.get_research_run(ids["partial"])["status"] == "partial"
        for key in ("queued", "running"):
            job = restored.get_job(ids[key])
            assert job["status"] == "interrupted" and job["cancel_requested"] and job["finished_at"]
            assert len(restored.job_events(ids[key])) == 2
            assert restored.job_events(ids[key])[0]["message"].startswith("恢复前")
            assert restored.job_events(ids[key])[-1]["level"] == "warning"
        research = restored.get_research_run(ids["run"])
        assert research["status"] == "interrupted"
        assert len(research["nodes"]) == 2 and len(research["edges"]) == 1 and len(research["claims"]) == 1
        assert len(research["items"]) == 1
        assert research["frontier"][0]["account_key"] == "twitter:restore_next"
        assert research["visited"] == ["twitter:restore_marker"]
        assert research["analysis"]["findings"] == ["保留已记录证据"]
        assert research["metadata"]["checkpoint_marker"] == MARKER
        assert restored.claim_job() is None
        assert restored.recover_jobs() == 0
    finally:
        restored.close()


def test_source_and_neighboring_files_are_never_changed_or_read(backup, tmp_path):
    source, _ = backup
    secret = tmp_path / "connection.secure"
    secret.write_bytes(b"private-credential-marker")
    source.chmod(0o444)
    try:
        before = digest(source)
        summary = prepare_restore(source, tmp_path / "stage")
        assert digest(source) == before
        assert secret.read_bytes() == b"private-credential-marker"
        assert b"private-credential-marker" not in (tmp_path / "stage" / "workbench.sqlite3").read_bytes()
        assert summary["counts"]["items"] == 2
        assert not Path(str(source) + "-wal").exists()
        assert not Path(str(source) + "-shm").exists()
    finally:
        source.chmod(0o666)


def test_schema_accepts_normal_alter_column_order_and_create_text_differences(backup, tmp_path):
    source, _ = backup
    with closing(sqlite3.connect(source)) as connection:
        # Older Store migrations append metadata/last_observed_at instead of
        # using the fresh template's physical column position.
        connection.execute("ALTER TABLE wb_accounts DROP COLUMN metadata")
        connection.execute("ALTER TABLE wb_accounts ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute("UPDATE sqlite_schema SET sql=replace(sql,'CHECK(status IN', 'CHECK ( status in') WHERE name='wb_jobs'")
        connection.commit()
    summary = prepare_restore(source, tmp_path / "stage")
    assert summary["counts"]["accounts"] == 2


def test_old_report_list_snapshot_and_null_job_result_are_compatible(backup, tmp_path):
    source, ids = backup
    mutate(source, "UPDATE wb_reports SET snapshot='[]'")
    mutate(source, "UPDATE wb_jobs SET result='null' WHERE id=?", (ids["queued"],))
    assert prepare_restore(source, tmp_path / "stage")["counts"]["reports"] == 1


@pytest.mark.parametrize("published", [None, "", "2026-10-08", "2026-10-08 09:15:00",
                                      "2026-10-08T09:15:00Z", "2026-10-08T09:15:00.123456+09:00"])
def test_historical_iso_dates_times_empty_optional_values_and_findings_are_compatible(backup, tmp_path, published):
    source, ids = backup
    mutate(source, "UPDATE wb_items SET published_at=?", (published,))
    # Early SQLite captures used naive timestamps; neither precision nor a Z
    # suffix is needed by their current readers.
    mutate(source, "UPDATE wb_accounts SET created_at='2026-10-08 09:15:00',updated_at='2026-10-08'")
    mutate(source, "UPDATE wb_jobs SET started_at='',finished_at=NULL")
    mutate(source, "UPDATE wb_reports SET start_date='',end_date='2026-10-08',snapshot='[]'")
    mutate(source, "UPDATE wb_research_runs SET query_plan=?,analysis=?,diagnostics=?",
           (json.dumps({"queries": ["平和"]}), json.dumps({"findings": ["保留已记录证据"]}), '[{"outcome":"saved"}]'))
    prepare_restore(source, tmp_path / "stage")
    restored = Store(tmp_path / "stage" / "workbench.sqlite3")
    try:
        assert restored.overview()["stats"]["items"] == 2
        assert restored.get_research_run(ids["run"])["analysis"]["findings"] == ["保留已记录证据"]
        assert "saved" in research_markdown(restored.export_research(ids["run"]))
    finally:
        restored.close()


@pytest.mark.parametrize("table,field,value,error", [
    ("wb_research_runs", "diagnostics", '["corrupt-entry"]', AttributeError),
    ("wb_research_runs", "analysis", '{"decisions":["corrupt-entry"]}', AttributeError),
    ("wb_research_nodes", "record", '{"account_capture":"corrupt-entry"}', AttributeError),
    ("wb_research_nodes", "record", '{"account_capture":{"metadata":[]}}', TypeError),
    ("wb_research_edges", "record", '{"evidence_urls":123}', TypeError),
])
def test_corruption_that_breaks_real_research_readers_is_rejected(backup, tmp_path, table, field, value, error):
    source, ids = backup
    mutate(source, f'UPDATE "{table}" SET "{field}"=?', (value,))
    # Reproduce the downstream failure on an isolated copy; opening Store on
    # the actual source could migrate it and would invalidate the no-write check.
    consumer = tmp_path / "consumer.sqlite3"
    consumer.write_bytes(source.read_bytes())
    broken = Store(consumer)
    try:
        with pytest.raises(error):
            research_markdown(broken.export_research(ids["run"]))
    finally:
        broken.close()
    reject_unchanged(source, tmp_path / "stage", "restore_data")


def test_bad_published_date_that_breaks_real_overview_is_rejected(backup, tmp_path):
    source, _ = backup
    mutate(source, "UPDATE wb_items SET published_at='corrupt-date'")
    consumer = tmp_path / "consumer.sqlite3"
    consumer.write_bytes(source.read_bytes())
    broken = Store(consumer)
    try:
        with pytest.raises(ValueError):
            broken.overview()
    finally:
        broken.close()
    reject_unchanged(source, tmp_path / "stage", "restore_data")


@pytest.mark.parametrize("member,value", [("summary", ["corrupt-entry"]),
                                          ("inserted", "corrupt-count"), ("duplicates", {"corrupt": 1})])
def test_job_result_corruption_that_breaks_real_batch_history_is_rejected(backup, tmp_path, member, value):
    source, ids = backup
    mutate(source, "INSERT INTO wb_job_batches VALUES ('restore-batch','restore batch',1,NULL,'2026-10-08')")
    mutate(source, "INSERT INTO wb_job_batch_jobs VALUES ('restore-batch',?,0,1,NULL)", (ids["partial"],))
    mutate(source, "UPDATE wb_jobs SET result=? WHERE id=?", (json.dumps({member: value}), ids["partial"]))
    consumer = tmp_path / "consumer.sqlite3"
    consumer.write_bytes(source.read_bytes())
    broken = Store(consumer)
    try:
        with pytest.raises((AttributeError, ValueError, TypeError)):
            broken.get_job_batch("restore-batch")
    finally:
        broken.close()
    reject_unchanged(source, tmp_path / "stage", "restore_data")


@pytest.mark.parametrize("table,field,value", [
    ("wb_accounts", "created_at", "corrupt-date"), ("wb_accounts", "updated_at", ""),
    ("wb_items", "published_at", "2026-02-30"), ("wb_items", "created_at", "yesterday"),
    ("wb_jobs", "created_at", "corrupt-date"), ("wb_jobs", "started_at", "corrupt-date"),
    ("wb_jobs", "finished_at", "2026-13-01"), ("wb_job_events", "created_at", "2026-10-08T25:00:00Z"),
    ("wb_reports", "created_at", "corrupt-date"), ("wb_reports", "start_date", "2026-02-29"),
    ("wb_reports", "end_date", "corrupt-date"),
    ("wb_research_runs", "created_at", "corrupt-date"), ("wb_research_runs", "updated_at", "corrupt-date"),
    ("wb_research_nodes", "updated_at", "corrupt-date"), ("wb_research_edges", "created_at", "corrupt-date"),
    ("wb_research_observations", "created_at", "corrupt-date"), ("wb_research_claims", "reviewed_at", "corrupt-date"),
    ("wb_research_claim_observations", "created_at", "corrupt-date"), ("wb_research_jobs", "created_at", "corrupt-date"),
    ("wb_relations", "observed_at", "corrupt-date"), ("wb_relations", "last_observed_at", "corrupt-date"),
    ("wb_drafts", "updated_at", "corrupt-date"),
])
def test_invalid_saved_dates_and_timestamps_are_rejected(backup, tmp_path, table, field, value):
    source, _ = backup
    with closing(sqlite3.connect(source)) as connection:
        assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] > 0
    mutate(source, f'UPDATE "{table}" SET "{field}"=?', (value,))
    reject_unchanged(source, tmp_path / "stage", "restore_data")


def test_backwards_saved_report_period_is_rejected(backup, tmp_path):
    source, _ = backup
    mutate(source, "UPDATE wb_reports SET start_date='2026-10-09',end_date='2026-10-08'")
    reject_unchanged(source, tmp_path / "stage", "restore_data")


@pytest.mark.parametrize("text", [None, "", "已保存的公开资料正文"])
def test_legacy_material_preview_text_is_compatible(backup, tmp_path, text):
    source, ids = backup
    mutate(source, "UPDATE wb_items SET content='',metadata=? WHERE id=?",
           (json.dumps({"clean_text": text, "content_text": text}), ids["item"]))
    prepare_restore(source, tmp_path / "stage")
    restored = Store(tmp_path / "stage" / "workbench.sqlite3")
    try:
        item = restored.get_item(ids["item"])
        assert (item["content"] or item["clean_text"] or item["content_text"] or "")[:120] == (text or "")
    finally:
        restored.close()


@pytest.mark.parametrize("payload", [b"not a database", b"SQLite format 3\0" + b"broken" * 30])
def test_non_sqlite_and_damaged_database_leave_no_stage(tmp_path, payload):
    source = tmp_path / "invalid.sqlite3"
    source.write_bytes(payload)
    reject_unchanged(source, tmp_path / "stage", "restore_not_sqlite" if not payload.startswith(b"SQLite format 3\0") else "restore_integrity")


def test_foreign_key_corruption_is_rejected(backup, tmp_path):
    source, _ = backup
    mutate(source, "INSERT INTO wb_job_events VALUES ('orphan','missing-job','bad','info','2026-10-08')")
    reject_unchanged(source, tmp_path / "stage", "restore_foreign_keys")


@pytest.mark.parametrize("sql", [
    "ALTER TABLE wb_accounts DROP COLUMN metadata",
    "ALTER TABLE wb_items ADD COLUMN future_only TEXT",
    "CREATE TABLE future_records(id TEXT)",
    "PRAGMA user_version=999",
    "DROP INDEX wb_items_account",
])
def test_missing_unknown_or_future_schema_is_rejected(backup, tmp_path, sql):
    source, _ = backup
    mutate(source, sql)
    reject_unchanged(source, tmp_path / "stage", "restore_schema")


@pytest.mark.parametrize("replacement", [
    "CREATE TABLE wb_drafts(key TEXT,version INTEGER NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,updated_at TEXT NOT NULL)",
    "CREATE TABLE wb_drafts(key TEXT PRIMARY KEY,version INTEGER NOT NULL,revision INTEGER NOT NULL,data BLOB NOT NULL,updated_at TEXT NOT NULL)",
    "CREATE TABLE wb_drafts(key TEXT PRIMARY KEY,version INTEGER NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,updated_at TEXT NOT NULL, UNIQUE(data))",
])
def test_forged_primary_key_type_and_unique_constraints_are_rejected(backup, tmp_path, replacement):
    source, _ = backup
    with closing(sqlite3.connect(source)) as connection:
        rows = connection.execute("SELECT * FROM wb_drafts").fetchall()
        connection.execute("DROP TABLE wb_drafts")
        connection.execute(replacement)
        connection.executemany("INSERT INTO wb_drafts VALUES (?,?,?,?,?)", rows)
        connection.commit()
    reject_unchanged(source, tmp_path / "stage", "restore_schema")


def test_forged_foreign_key_and_check_constraints_are_rejected(backup, tmp_path):
    source, _ = backup
    with closing(sqlite3.connect(source)) as connection:
        rows = connection.execute("SELECT * FROM wb_job_events").fetchall()
        connection.execute("DROP TABLE wb_job_events")
        connection.execute("CREATE TABLE wb_job_events(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,message TEXT NOT NULL,level TEXT NOT NULL,created_at TEXT NOT NULL)")
        connection.execute("CREATE INDEX wb_events_job ON wb_job_events(job_id,created_at,id)")
        connection.executemany("INSERT INTO wb_job_events VALUES (?,?,?,?,?)", rows)
        connection.commit()
    reject_unchanged(source, tmp_path / "stage", "restore_schema")


@pytest.mark.parametrize("sql", [
    "CREATE TRIGGER bad AFTER UPDATE ON wb_jobs BEGIN DELETE FROM wb_items; END",
    "CREATE VIEW bad AS SELECT * FROM wb_items",
    "CREATE VIRTUAL TABLE bad USING fts5(content)",
])
def test_views_triggers_virtual_tables_are_rejected_before_execution(backup, tmp_path, sql):
    source, _ = backup
    mutate(source, sql)
    reject_unchanged(source, tmp_path / "stage", "restore_unsafe_schema")


@pytest.mark.parametrize("table,field,value", [
    ("wb_items", "metadata", "["), ("wb_items", "topics", "{}"),
    ("wb_items", "locations", '["invalid"]'), ("wb_items", "entities", '[["invalid"]]'),
    ("wb_reports", "snapshot", '"invalid"'), ("wb_reports", "snapshot", '{"items":{}}'),
    ("wb_jobs", "params", "[]"), ("wb_jobs", "result", "[]"),
    ("wb_research_runs", "analysis", "[]"), ("wb_research_runs", "frontier", "{}"),
    ("wb_research_runs", "frontier", '["invalid"]'),
    ("wb_research_runs", "diagnostics", '[null]'),
    ("wb_research_runs", "analysis", '{"decisions":null}'),
    ("wb_research_runs", "metadata", '{"decisions":[null]}'),
    ("wb_research_nodes", "record", '{"account_capture":null}'),
    ("wb_research_edges", "record", '{"evidence_urls":[{}]}'),
    ("wb_research_claims", "record", '{"evidence_urls":"https://example.org"}'),
    ("wb_research_observations", "record", '{"evidence_urls":null}'),
    ("wb_research_claim_observations", "record", '{"evidence_urls":[123]}'),
    ("wb_research_edges", "record", '{"observed_at":"corrupt-date"}'),
    ("wb_research_edges", "record", '{"first_observed_at":"corrupt-date"}'),
    ("wb_jobs", "result", '{"diagnostics":["invalid"]}'),
    ("wb_research_nodes", "record", "[]"), ("wb_drafts", "data", "null"),
    ("wb_accounts", "metadata", '{"bad":NaN}'),
    ("wb_items", "metadata", '{"clean_text":123}'),
    ("wb_items", "metadata", '{"content_text":["invalid"]}'),
])
def test_sqlite_valid_but_malformed_json_is_rejected(backup, tmp_path, table, field, value):
    source, _ = backup
    mutate(source, f'UPDATE "{table}" SET "{field}"=?', (value,))
    with closing(sqlite3.connect(source)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    reject_unchanged(source, tmp_path / "stage", "restore_data")


@pytest.mark.parametrize("sql", [
    "UPDATE wb_research_runs SET status='future'",
    "UPDATE wb_research_nodes SET depth=-1",
    "UPDATE wb_drafts SET revision='broken'",
    "UPDATE wb_drafts SET key=NULL",
])
def test_invalid_status_counts_and_nullable_sqlite_text_primary_key_are_rejected(backup, tmp_path, sql):
    source, _ = backup
    mutate(source, sql)
    reject_unchanged(source, tmp_path / "stage", "restore_data")


def test_nonempty_output_and_source_parent_are_not_modified(backup, tmp_path):
    source, _ = backup
    stage = tmp_path / "occupied"
    stage.mkdir()
    keep = stage / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    with pytest.raises(RestoreError, match="空目录") as error:
        prepare_restore(source, stage)
    assert error.value.code == "restore_output" and keep.read_text() == "keep"
    with pytest.raises(RestoreError) as error:
        prepare_restore(source, source.parent)
    assert error.value.code == "restore_output" and source.exists()


def test_existing_empty_stage_can_be_used_and_failure_keeps_it_empty(backup, tmp_path):
    source, _ = backup
    stage = tmp_path / "provided-empty"
    stage.mkdir()
    prepare_restore(source, stage)
    assert (stage / "summary.json").is_file()
    failed = tmp_path / "provided-failed"
    failed.mkdir()
    mutate(source, "UPDATE wb_drafts SET data='['")
    with pytest.raises(RestoreError):
        prepare_restore(source, failed)
    assert failed.is_dir() and list(failed.iterdir()) == []


def test_running_source_with_uncheckpointed_wal_is_rejected_without_mutation(tmp_path):
    store = Store(tmp_path / "running.sqlite3")
    keeper = store._connect()
    try:
        keeper.execute("BEGIN")
        keeper.execute("SELECT COUNT(*) FROM wb_items").fetchone()
        store.import_items([{"title": MARKER, "content": MARKER, "type": "news"}])
        source = Path(store.path)
        wal = Path(str(source) + "-wal")
        assert wal.stat().st_size > 0
        before = {path: path.read_bytes() for path in (source, wal, Path(str(source) + "-shm"))}
        with pytest.raises(RestoreError) as error:
            prepare_restore(source, tmp_path / "stage")
        assert error.value.code == "restore_source_active"
        assert all(path.read_bytes() == content for path, content in before.items())
        assert not (tmp_path / "stage").exists()
        # The app's online backup is accepted and includes committed WAL data.
        response = BackupService().response(store)
        try:
            exported = tmp_path / "download.sqlite3"
            exported.write_bytes(Path(response.path).read_bytes())
            assert prepare_restore(exported, tmp_path / "stage")["counts"]["items"] == 1
        finally:
            response.directory.cleanup()
    finally:
        keeper.close()
        store.close()


def test_nonempty_rollback_journal_is_rejected(backup, tmp_path):
    source, _ = backup
    journal = Path(str(source) + "-journal")
    journal.write_bytes(b"unresolved transaction")
    reject_unchanged(source, tmp_path / "stage", "restore_source_active")


def test_deadline_interrupts_sql_and_cleans_template(backup, tmp_path, monkeypatch):
    source, _ = backup
    before = digest(source)
    def long_integrity(connection):
        connection.execute("""WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<100000000)
                              SELECT sum(x) FROM n""").fetchone()
    monkeypatch.setattr(restore, "_integrity", long_integrity)
    started = time.monotonic()
    with pytest.raises(RestoreError) as error:
        prepare_restore(source, tmp_path / "stage", timeout_seconds=0.2)
    assert error.value.code == "restore_timeout"
    assert time.monotonic() - started < 2
    assert not (tmp_path / "stage").exists() and digest(source) == before


def test_backup_deadline_cleans_partially_created_candidate(backup, tmp_path, monkeypatch):
    source, _ = backup
    before = digest(source)
    reached = []
    def expired_backup(connection, candidate, *, progress, **kwargs):
        reached.append(True)
        connection.deadline.end = time.monotonic() - 1
        progress(0, 100, 100)
    monkeypatch.setattr(restore._Connection, "backup", expired_backup)
    with pytest.raises(RestoreError) as error:
        prepare_restore(source, tmp_path / "stage")
    assert error.value.code == "restore_timeout" and reached
    assert not (tmp_path / "stage").exists() and digest(source) == before


def test_source_fingerprint_detects_external_change_and_cleans_candidate(backup, tmp_path, monkeypatch):
    source, _ = backup
    original = restore._interrupt_jobs
    def change_source(connection, deadline):
        count = original(connection, deadline)
        with source.open("ab") as stream:
            stream.write(b"external-change")
        return count
    monkeypatch.setattr(restore, "_interrupt_jobs", change_source)
    with pytest.raises(RestoreError) as error:
        prepare_restore(source, tmp_path / "stage")
    assert error.value.code == "restore_source_changed"
    assert source.read_bytes().endswith(b"external-change")
    assert not (tmp_path / "stage").exists()


def test_wal_appearing_during_preparation_is_rejected(backup, tmp_path, monkeypatch):
    source, _ = backup
    before, original = digest(source), restore._interrupt_jobs
    def add_wal(connection, deadline):
        count = original(connection, deadline)
        Path(str(source) + "-wal").write_bytes(b"new runtime WAL")
        return count
    monkeypatch.setattr(restore, "_interrupt_jobs", add_wal)
    with pytest.raises(RestoreError) as error:
        prepare_restore(source, tmp_path / "stage")
    assert error.value.code == "restore_source_active"
    assert digest(source) == before and not (tmp_path / "stage").exists()


@pytest.mark.parametrize("timeout,code", [(0, "restore_timeout"), (-1, "restore_timeout"),
                                        (float("nan"), "restore_invalid_timeout"), (True, "restore_invalid_timeout")])
def test_invalid_or_expired_initial_deadline_does_not_create_stage(backup, tmp_path, timeout, code):
    source, _ = backup
    with pytest.raises(RestoreError) as error:
        prepare_restore(source, tmp_path / "stage", timeout_seconds=timeout)
    assert error.value.code == code and not (tmp_path / "stage").exists()
