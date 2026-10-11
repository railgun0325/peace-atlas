"""Exports preserve evidence beyond graph display caps, without network access."""
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.workbench.research_export import research_markdown
from app.workbench.server import create_app
from app.workbench.store import Store


def start(store):
    job = store.create_job("research", {"keyword": "反戦", "start_date": "2026-10-01", "end_date": "2026-10-08"})
    store.create_research_run(job["id"], job["params"])
    return job["id"]


def node(handle="alpha", **changes):
    return {"account_key": f"twitter:{handle}", "depth": 0, "status": "visited", **changes}


def claim(number=0):
    return {"subject_key": "twitter:alpha", "relationship": "affiliation", "object_label": f"公開団体 {number}",
            "basis": "inferred", "statement": f"候補 {number}", "evidence_urls": ["https://x.com/alpha"]}


def test_downloads_keep_every_material_and_claim_beyond_graph_caps(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)
    store = Store(tmp_path / "full-exports.sqlite3")
    run_id = start(store)
    items = [{"title": f"研究資料 {number}", "content": f"公開情報 {number}", "type": "social",
              "url": f"https://x.com/alpha/status/{number + 1}", "account_key": "twitter:alpha"}
             for number in range(1201)]
    store.checkpoint_research(run_id, nodes=[node()], items=items, claims=[claim(number) for number in range(601)])
    store.update_job(run_id, status="partial", result={"run_id": run_id})
    resumed = store.resume_research_job(run_id, {"resume_run_id": run_id})
    with TestClient(create_app(store, run_worker=False)) as client:
        graph = client.get(f"/api/v2/research/{run_id}").json()
        assert len(graph["items"]) == 1200 and len(graph["claims"]) == 600
        assert graph["items_truncated"] and graph["claims_truncated"] and graph["truncated"]
        assert graph["total_items"] == 1201 and graph["total_claims"] == 601
        downloaded = client.get(f"/api/v2/research/{resumed['id']}/export")
        assert downloaded.status_code == 200
        exported = downloaded.json()
        assert exported["run"]["id"] == run_id
        assert exported["run"]["job_ids"] == [run_id, resumed["id"]]
        assert len(exported["items"]) == exported["total_items"] == 1201
        assert len(exported["claims"]) == exported["total_claims"] == 601
        assert exported["truncated"] is exported["items_truncated"] is exported["claims_truncated"] is False
        assert exported["limit"] is None
        report = client.get(f"/api/v2/research/{run_id}/report")
        assert report.status_code == 200
        assert "材料 1201" in report.text and "分析声明 601" in report.text
        assert "https://x.com/i/status/1201" in report.text
        assert "公開団体 600" in report.text


def test_claim_only_truncation_is_reported_and_full_export_includes_all(tmp_path):
    store = Store(tmp_path / "claim-cap.sqlite3")
    run_id = start(store)
    store.checkpoint_research(run_id, nodes=[node()], claims=[claim(number) for number in range(3)])
    graph = store.research_graph(run_id, limit=1)
    assert len(graph["claims"]) == 2 and graph["total_claims"] == 3
    assert graph["claims_truncated"] and graph["truncated"]
    assert "受展示上限限制的节选" in research_markdown(graph)
    exported = store.export_research(run_id)
    assert len(exported["claims"]) == 3 and not exported["truncated"]
    with pytest.raises(LookupError):
        store.export_research("missing")


def test_markdown_keeps_zero_depth_inference_basis_and_human_review_evidence(tmp_path):
    store = Store(tmp_path / "review-evidence.sqlite3")
    run_id = start(store)
    store.checkpoint_research(run_id, nodes=[node(), node("beta", depth=1, parent_key="twitter:alpha")],
        claims=[claim()], edges=[{"source_key": "twitter:alpha", "target_key": "twitter:beta",
            "relationship": "mentions", "basis": "inferred", "evidence_urls": ["https://x.com/alpha/status/9"]}])
    saved = store.get_research_run(run_id)["claims"][0]
    store.review_research_claim(run_id, saved["id"], "confirmed", note="组织官网公开资料人工核对",
                                evidence_url="https://example.org/organization/official")
    report = research_markdown(store.export_research(run_id))
    assert "| twitter:alpha | 0 |" in report
    assert "| twitter:alpha | mentions | twitter:beta | inferred |" in report
    assert "状态：confirmed" in report
    assert "组织官网公开资料人工核对" in report
    assert "https://example.org/organization/official" in report
    assert "人工复核时间：" in report and "## 已观察到的关系" not in report


@pytest.mark.parametrize("postgres", [False, True])
def test_export_keeps_one_revision_during_concurrent_checkpoint(tmp_path, monkeypatch, postgres):
    """Interleave a real WAL commit; emulate PG's per-statement default reads."""
    store = Store(tmp_path / "snapshot.sqlite3")
    writer = Store(tmp_path / "snapshot.sqlite3")
    run_id = start(store)
    original_item = {"title": "Original", "content": "First committed capture", "type": "news",
                     "url": "https://example.org/first"}
    new_item = {"title": "Later", "content": "Concurrent committed capture", "type": "news",
                "url": "https://example.org/later"}
    store.checkpoint_research(run_id, items=[original_item], summary={"record_count": 1})
    sqlite_connect = writer._connect
    calls, committed = [], []

    class Cursor:
        def __init__(self, cursor, sql):
            self.cursor, self.sql = cursor, sql

        def fetchone(self):
            row = self.cursor.fetchone()
            if self.sql.startswith("SELECT * FROM wb_research_runs") and not committed:
                writer.checkpoint_research(run_id, items=[new_item], summary={"record_count": 2})
                committed.append(True)
            return row

        def fetchall(self):
            return self.cursor.fetchall()

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=()):
            calls.append(sql)
            if sql.startswith("SET TRANSACTION"):
                # SQLite WAL supplies the stable snapshot used by the fake PG
                # connection. Without this SET, each SELECT sees a fresh commit.
                assert sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                return self.connection.execute("BEGIN")
            translated = sql.replace("%s", "?").replace("(i.metadata::jsonb)->'atlas_cleanup'->>'status'", "json_extract(i.metadata, '$.atlas_cleanup.status')")
            return Cursor(self.connection.execute(translated, params), sql)

    @contextmanager
    def transaction(write=False):
        assert not write
        connection = sqlite_connect()
        try:
            if not postgres:
                connection.execute("BEGIN")
            yield Connection(connection)
            connection.commit()
        finally:
            connection.close()

    monkeypatch.setattr(store, "_postgres", postgres)
    monkeypatch.setattr(store, "_transaction", transaction)
    exported = store.export_research(run_id)
    assert committed == [True]
    assert exported["summary"]["record_count"] == exported["total_items"] == 1
    assert exported["items"][0]["url"] == "https://example.org/first"
    if postgres:
        assert calls[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    else:
        assert not any(sql.startswith("SET TRANSACTION") for sql in calls)
    latest = writer.export_research(run_id)
    assert latest["summary"]["record_count"] == latest["total_items"] == 2
