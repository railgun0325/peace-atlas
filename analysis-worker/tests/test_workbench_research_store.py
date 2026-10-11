"""Research evidence survives restarts, cancellation and concurrent editing."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.workbench.research_repository import DraftConflict
from app.workbench.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "research.sqlite3")
    yield value
    value.close()


def begin(store):
    job = store.create_job("research", {"keywords": ["憲法九条", "反戦"]})
    store.create_research_run(job["id"], job["params"])
    return job["id"]


def nodes():
    return [{"account_key": "twitter:peace_a", "name": "平和A", "depth": 0,
             "discovery_reason": "keyword_hit", "status": "visited", "evidence_urls": ["https://x.com/peace_a/status/10"]},
            {"account_key": "twitter:peace_b", "name": "平和B", "depth": 1, "parent_key": "twitter:peace_a",
             "path": ["twitter:peace_a", "twitter:peace_b"], "discovery_reason": "following", "status": "pending"}]


def post(number=10, **changes):
    return {"title": "憲法九条を守る集会", "content": f"平和の公開集会 {number}", "type": "social",
            "url": f"https://x.com/peace_a/status/{number}", "published_at": "2026-10-06T00:00:00Z",
            "account_key": "twitter:peace_a", "source_name": "X / peace_a", **changes}


def edge(**changes):
    return {"source_key": "twitter:peace_a", "target_key": "twitter:peace_b", "relationship": "follows",
            "basis": "observed", "evidence_urls": ["https://x.com/peace_a/following"],
            "observed_at": "2026-10-06T00:00:00Z", **changes}


def claim(**changes):
    return {"subject_key": "twitter:peace_a", "relationship": "affiliation", "object_label": "公開平和団体",
            "basis": "inferred", "statement": "プロフィールに団体名があるため要確認",
            "evidence_urls": ["https://x.com/peace_a"], **changes}


def test_final_analysis_preserves_actual_relationship_observation_time(store):
    from app.workbench.relationship_analysis import analyze_relationships

    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes(), edges=[edge()], status="running", phase="collecting")
    newer = edge(observed_at="2026-10-07T00:00:00Z")
    store.checkpoint_research(run_id, edges=[newer], phase="collecting")
    analysis = analyze_relationships(
        [{"account_key":"twitter:peace_a"},{"account_key":"twitter:peace_b"}], [],
        [{**newer,"relation":"follows","evidence_url":newer["evidence_urls"][0]}],
    )
    # Rule/model summaries cite existing observations; they are not a new visit.
    assert "observed_at" not in analysis["findings"][0]
    store.checkpoint_research(run_id, edges=analysis["findings"], analysis=analysis, phase="finished", status="partial")
    saved = store.get_research_run(run_id)
    assert len(saved["relations"]) == 1
    assert saved["relations"][0]["observed_at"] == "2026-10-07T00:00:00.000000Z"
    assert saved["relations"][0]["first_observed_at"] == "2026-10-06T00:00:00.000000Z"
    reopened = Store(store.path)
    try:
        assert reopened.get_research_run(run_id)["relations"][0]["observed_at"] == "2026-10-07T00:00:00.000000Z"
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT COUNT(*) FROM wb_relations").fetchone()[0] == 1
            assert db.execute("SELECT last_observed_at FROM wb_relations").fetchone()[0] == "2026-10-07T00:00:00.000000Z"
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        reopened.close()


def test_analysis_can_retain_known_first_time_from_older_null_timestamp(store):
    import json

    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes(), edges=[edge()], status="running")
    # Exact persisted shape exposed by the real task before this repair.
    with sqlite3.connect(store.path) as db:
        edge_id,record = db.execute("SELECT id,record FROM wb_research_edges WHERE run_id=?",(run_id,)).fetchone()
        legacy = json.loads(record)
        legacy.update(observed_at=None,first_observed_at="2026-10-06T00:00:00Z")
        db.execute("UPDATE wb_research_edges SET record=? WHERE id=?",(json.dumps(legacy),edge_id))
    store.checkpoint_research(run_id,edges=[edge(observed_at=None)],phase="finished")
    saved = store.get_research_run(run_id)["relations"][0]
    assert saved["observed_at"] == saved["first_observed_at"] == "2026-10-06T00:00:00Z"
    store.checkpoint_research(run_id,edges=[edge(observed_at="2026-10-07T00:00:00Z")])
    saved = store.get_research_run(run_id)["relations"][0]
    assert saved["observed_at"] == "2026-10-07T00:00:00.000000Z"
    assert saved["first_observed_at"] == "2026-10-06T00:00:00Z"


def test_research_migration_preserves_custom_objects_foreign_keys_and_all_job_kinds(tmp_path):
    path = tmp_path / "migration.sqlite3"
    original = Store(path)
    old = original.create_job("collect", {"source": "google_news"})
    original.add_job_event(old["id"], "kept original event")
    with sqlite3.connect(path) as con:
        schema = con.execute("SELECT sql FROM sqlite_master WHERE name='wb_jobs'").fetchone()[0]
        con.execute(schema.replace("wb_jobs", "old_jobs", 1).replace(",'research'", ""))
        con.execute("INSERT INTO old_jobs SELECT * FROM wb_jobs")
        con.execute("DROP TABLE wb_jobs")
        con.execute("ALTER TABLE old_jobs RENAME TO wb_jobs")
        con.execute("CREATE INDEX custom_job_progress ON wb_jobs(progress)")
        con.execute("CREATE TABLE custom_job_audit(job_id TEXT REFERENCES wb_jobs(id), note TEXT)")
        con.execute("INSERT INTO custom_job_audit VALUES (?, 'historical')", (old["id"],))
        con.execute("CREATE TRIGGER custom_job_note AFTER INSERT ON wb_jobs BEGIN INSERT INTO custom_job_audit VALUES (NEW.id, 'new'); END")
    original.close()
    upgraded = Store(path)
    assert upgraded.get_job(old["id"])["params"] == {"source": "google_news"}
    assert upgraded.job_events(old["id"])[0]["message"] == "kept original event"
    for kind in ("collect", "report", "login", "research"):
        assert upgraded.create_job(kind, {})["kind"] == kind
    with sqlite3.connect(path) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT COUNT(*) FROM custom_job_audit").fetchone()[0] == 5
        assert con.execute("SELECT name FROM sqlite_master WHERE name='custom_job_progress'").fetchone()
    upgraded.close()
    Store(path).close()


def test_checkpoint_and_resume_preserve_canonical_evidence_and_frontier(store):
    run_id = begin(store)
    result = store.checkpoint_research(run_id, items=[post()], nodes=nodes(), edges=[edge()], claims=[claim()],
        frontier=[{"account_key": "twitter:peace_b", "depth": 1}], visited=["twitter:peace_a"],
        query_plan={"queries": ["憲法九条", "反戦"]}, summary={"visited": 1}, phase="expand", status="partial",
        diagnostics=[{"source": "twitter", "code": "rate_limited"}], analysis={"findings": []},
        metadata={"decisions": [{"account_key": "twitter:peace_a", "action": "expand"}]})
    assert result["inserted"] == 1
    run = store.get_research_run(run_id)
    assert len(run["nodes"]) == 2 and len(run["items"]) == 1
    assert run["items"][0]["url"] == "https://x.com/i/status/10"
    assert run["items"][0]["account_key"] == "twitter:peace_a"
    assert run["visited"] == ["twitter:peace_a"]
    assert run["frontier"][0]["account_key"] == "twitter:peace_b"
    assert run["metadata"]["decisions"][0]["action"] == "expand"
    store.update_job(run_id, status="partial", result={"run_id": run_id})
    following = store.create_job("research", {"resume_run_id": run_id})
    attached = store.attach_research_job(run_id, following["id"])
    assert attached["id"] == run_id and attached["job_id"] == following["id"]
    assert attached["job_ids"] == [run_id, following["id"]]
    assert store.get_research_run(following["id"])["id"] == run_id
    assert store.list_research_runs()["total"] == 1
    store.checkpoint_research(run_id, items=[post()], phase="finished", status="succeeded", frontier=[])
    saved = store.get_research_run(run_id)
    assert len(saved["items"]) == 1 and len(saved["nodes"]) == 2
    assert saved["frontier"] == [] and saved["visited"] == ["twitter:peace_a"]


def test_restart_keeps_partial_material_and_human_account_decisions(store):
    account = store.save_account({"name": "Human named A", "platform": "twitter", "handle": "peace_a",
        "organization": "手工核验组织", "status": "verified", "evidence_url": "https://example.org/official"})
    run_id = begin(store)
    store.checkpoint_research(run_id, items=[post()], nodes=nodes(), status="cancelled",
        accounts=[{"platform": "twitter", "handle": "peace_a", "name": "Automatic name", "status": "verified", "organization": "AI claim"}])
    saved = store.get_account(account["id"])
    assert saved["name"] == "Human named A" and saved["status"] == "verified"
    assert saved["organization"] == "手工核验组织"
    reopened = Store(store.path)
    restored = reopened.get_research_run(run_id)
    assert restored["status"] == "cancelled" and len(restored["items"]) == 1
    assert restored["items"][0]["account_status"] == "verified"
    reopened.close()


def test_checkpoint_rolls_back_material_nodes_and_state_on_invalid_edge(store):
    run_id = begin(store)
    with pytest.raises(ValueError, match="端点"):
        store.checkpoint_research(run_id, items=[post()], nodes=nodes(), edges=[edge(target_key="twitter:missing")],
                                  visited=["twitter:peace_a"], summary={"visited": 1})
    assert store.list_accounts() == [] and store.list_items()["total"] == 0
    run = store.get_research_run(run_id)
    assert run["nodes"] == [] and run["visited"] == [] and run["summary"] == {}


def test_checkpoint_rolls_back_on_actual_sql_failure(store):
    run_id = begin(store)
    with sqlite3.connect(store.path) as con:
        con.execute("CREATE TRIGGER research_disk_failure BEFORE INSERT ON wb_research_edges BEGIN SELECT RAISE(ABORT, 'disk failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="disk failed"):
        store.checkpoint_research(run_id, items=[post()], nodes=nodes(), edges=[edge()])
    assert store.list_items()["total"] == 0 and store.list_accounts() == []
    assert store.get_research_run(run_id)["revision"] == 1


def test_only_observed_follows_uses_legacy_relations_and_observations_are_kept(store):
    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes(), edges=[edge(), edge(relationship="mentions"), edge(basis="inferred")])
    account_id = store.get_research_run(run_id)["accounts"][0]["id"]
    assert store.account_relations(account_id)["total"] == 1
    store.checkpoint_research(run_id, edges=[edge(observed_at="2026-10-06T01:00:00Z", evidence_urls=["https://x.com/peace_a/following?new=1"])])
    saved = store.get_research_run(run_id)
    assert len(saved["edges"]) == 3
    observed = next(value for value in saved["edges"] if value["relationship"] == "follows" and value["basis"] == "observed")
    assert len(observed["evidence_urls"]) == 2
    with sqlite3.connect(store.path) as con:
        assert con.execute("SELECT COUNT(*) FROM wb_research_observations").fetchone()[0] == 4


def test_duplicate_existing_item_is_attached_to_each_research(store):
    first, second = begin(store), begin(store)
    store.checkpoint_research(first, items=[post()], nodes=nodes())
    result = store.checkpoint_research(second, items=[post()], nodes=nodes())
    assert result["duplicates"] == 1 and result["items_linked"] == 1
    assert store.get_research_run(first)["items"][0]["id"] == store.get_research_run(second)["items"][0]["id"]


@pytest.mark.parametrize("duplicate_url", ["https://example.org/reprinted-story", ""])
def test_matching_content_retains_the_later_publisher_capture_in_research(store, duplicate_url):
    first, second = begin(store), begin(store)
    news = {"title": "Peace news", "content": "The same public-source article", "type": "news",
            "url": "https://example.org/original-story"}
    store.checkpoint_research(first, items=[news])
    result = store.checkpoint_research(second, items=[{**news, "url": duplicate_url}], summary={"record_count": 1})
    assert result["inserted"] == 1 and result["duplicates"] == result["rejected"] == 0
    assert result["items_linked"] == result["record_count"] == 1
    earlier, later = store.get_research_run(first), store.get_research_run(second)
    assert later["summary"]["record_count"] == 1
    assert later["items"][0]["id"] != earlier["items"][0]["id"]
    assert later["items"][0]["url"] == (duplicate_url or "")
    repeated = store.checkpoint_research(second, items=[{**news, "url": duplicate_url}])
    assert repeated["duplicates"] == 1 and repeated["inserted"] == repeated["rejected"] == 0
    assert repeated["record_count"] == 1


def test_rejected_item_cannot_attach_existing_url_as_research_evidence(store):
    first, second = begin(store), begin(store)
    news = {"title": "Original evidence", "content": "A validated original record", "type": "news",
            "url": "https://example.org/original-story"}
    store.checkpoint_research(first, items=[news])
    result = store.checkpoint_research(second, items=[{**news, "published_at": "invalid-date"}], summary={"record_count": 1})
    assert result["rejected"] == 1 and result["inserted"] == result["duplicates"] == 0
    assert result["items_linked"] == result["record_count"] == 0
    assert store.get_research_run(second)["items"] == []
    assert store.get_research_run(second)["summary"]["record_count"] == 0
    assert len(store.get_research_run(first)["items"]) == 1


def test_claims_require_human_review_and_do_not_write_organization(store):
    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes(), claims=[claim(status="confirmed")])
    current = store.get_research_run(run_id)["claims"][0]
    assert current["status"] == "candidate"
    assert all(not account["organization"] for account in store.get_research_run(run_id)["accounts"])
    with pytest.raises(ValueError, match="evidence_url"):
        store.review_research_claim(run_id, current["id"], "confirmed")
    with pytest.raises(ValueError, match="HTTP"):
        store.review_research_claim(run_id, current["id"], "confirmed", evidence_url="file:///passwords")
    confirmed = store.review_research_claim(run_id, current["id"], "confirmed", note="公开组织官网确认", evidence_url="https://example.org/about")
    assert confirmed["status"] == "confirmed"
    store.checkpoint_research(run_id, claims=[claim(status="rejected", evidence_urls=["https://example.org/second"])])
    reviewed = store.get_research_run(run_id)["claims"][0]
    assert reviewed["status"] == "confirmed" and reviewed["review_note"] == "公开组织官网确认"
    assert len(reviewed["evidence_urls"]) == 2
    assert all(not account["organization"] for account in store.get_research_run(run_id)["accounts"])
    store.review_research_claim(run_id, current["id"], "rejected", note="证据矛盾")
    with sqlite3.connect(store.path) as con:
        assert con.execute("SELECT COUNT(*) FROM wb_research_claim_reviews").fetchone()[0] == 2


def test_graph_filters_are_literal_bounded_and_never_invent_missing_parents(store):
    run_id = begin(store)
    extra = {"account_key": "instagram:peace_c", "name": "Literal 100%_name", "depth": 2,
             "parent_key": "twitter:peace_b", "status": "blocked"}
    store.checkpoint_research(run_id, nodes=[*nodes(), extra], edges=[edge()])
    all_nodes = store.research_graph(run_id, limit=1)
    assert all_nodes["total_nodes"] == 3 and all_nodes["truncated"] and len(all_nodes["nodes"]) == 1
    assert all_nodes["edges"] == []
    filtered = store.research_graph(run_id, platform="instagram", query="100%_", status="blocked")
    assert len(filtered["nodes"]) == 1 and filtered["nodes"][0]["parent_in_view"] is False
    assert store.research_graph(run_id, query="' OR 1=1 --")["nodes"] == []
    assert len(store.research_graph(run_id, relationship="follows")["nodes"]) == 2
    assert len(store.research_graph(run_id, max_depth=0)["nodes"]) == 1
    assert "完整" in filtered["coverage"]


@pytest.mark.parametrize("changes", [{"limit": 0}, {"limit": True}, {"limit": 1001}, {"max_depth": -1},
                                      {"platform": "javascript"}, {"relationship": "evil"}, {"status": "fake"}])
def test_graph_rejects_invalid_filters(store, changes):
    with pytest.raises(ValueError):
        store.research_graph(begin(store), **changes)


def test_news_and_social_are_linked_without_reclassifying_news(store):
    run_id = begin(store)
    news = {"title": "Peace news", "content": "Actual public news evidence", "type": "news", "url": "https://example.org/news"}
    store.checkpoint_research(run_id, items=[news, post()], nodes=nodes())
    assert {entry["type"] for entry in store.get_research_run(run_id)["items"]} == {"news", "social"}


def test_discovery_keeps_shortest_path_and_does_not_requeue_visited_node(store):
    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes())
    store.checkpoint_research(run_id, nodes=[{**nodes()[0], "depth": 2, "parent_key": "twitter:peace_b", "status": "pending"}])
    result = store.get_research_run(run_id)["nodes"][0]
    assert result["account_key"] == "twitter:peace_a" and result["depth"] == 0
    assert result["status"] == "visited" and result["parent_key"] is None


def test_draft_persists_across_restarts_and_stale_edit_or_clear_cannot_destroy_it(store):
    first = store.save_draft("collection", {"keywords": ["憲法九条", "反戦"], "depth": 2}, expected_revision=0, version=1)
    second = store.save_draft("collection", {"keywords": ["平和運動"]}, expected_revision=first["revision"])
    with pytest.raises(DraftConflict):
        store.save_draft("collection", {"keywords": ["stale"]}, expected_revision=first["revision"])
    with pytest.raises(DraftConflict):
        store.clear_draft("collection", expected_revision=first["revision"])
    reopened = Store(store.path)
    assert reopened.get_draft("collection")["data"] == {"keywords": ["平和運動"]}
    assert reopened.clear_draft("collection", expected_revision=second["revision"])
    assert reopened.get_draft("collection") is None
    reopened.close()


def test_concurrent_first_draft_save_accepts_one_and_preserves_others_as_conflicts(store):
    def save(number):
        try:
            return store.save_draft("collection", {"keyword": str(number)}, expected_revision=0)
        except DraftConflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(save, range(8)))
    assert len([value for value in results if value]) == 1
    assert store.get_draft("collection")["revision"] == 1


@pytest.mark.parametrize("data", [{"password": "never-store"}, {"nested": {"cookies": []}}, {"api_key": "never-store"},
                                  {"settings": [{"auth_token": "never-store"}]}, {"sessionid": "never-store"}])
def test_drafts_do_not_store_credentials(store, data):
    with pytest.raises(ValueError, match="密钥"):
        store.save_draft("collection", data)
    assert store.get_draft("collection") is None


def test_unknown_run_and_wrong_job_links_are_rejected(store):
    ordinary = store.create_job("collect", {})
    with pytest.raises(ValueError):
        store.create_research_run(ordinary["id"], {})
    run = begin(store)
    with pytest.raises(ValueError):
        store.attach_research_job(run, ordinary["id"])
    with pytest.raises(LookupError):
        store.checkpoint_research("missing", nodes=[])
    with pytest.raises(LookupError):
        store.research_graph("missing")
    assert store.get_research_run("missing") is None


def test_profile_capture_and_decisions_survive_resume_without_overwriting_global_account(store):
    run_id = begin(store)
    profile = {"verified_in_dom": True, "bio": "平和運動の公開団体", "url": "https://x.com/peace_a", "links": ["https://example.org/about"]}
    store.checkpoint_research(run_id, nodes=nodes(), accounts=[{"platform": "twitter", "handle": "peace_a", "name": "A", "profile": profile}],
                              analysis={"decisions": [{"id": "d1", "action": "expand"}], "ai": {"used": True}})
    store.checkpoint_research(run_id, nodes=nodes(), analysis={"decisions": [{"id": "d2", "action": "stop"}]})
    result = store.get_research_run(run_id)
    assert next(value for value in result["accounts"] if value["account_key"] == "twitter:peace_a")["profile"] == profile
    assert [row["id"] for row in result["decisions"]] == ["d1", "d2"]
    assert result["analysis"]["ai"]["used"] is True


def test_inferred_account_relationship_is_candidate_claim_not_an_observed_edge(store):
    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes(), claims=[{"subject_key": "twitter:peace_a", "object_key": "twitter:peace_b",
        "relationship": "shared_link", "basis": "inferred", "statement": "需要复核", "evidence_urls": ["https://example.org/article"]}])
    result = store.get_research_run(run_id)
    assert result["edges"] == [] and result["claims"][0]["status"] == "candidate"
    assert result["claims"][0]["object_label"] == "twitter:peace_b"


def test_recovery_marks_run_interrupted_and_preserves_visited_frontier(store):
    run_id = begin(store)
    store.claim_job(kind="research")
    store.checkpoint_research(run_id, nodes=nodes(), frontier=[nodes()[1]], visited=["twitter:peace_a"], status="running")
    assert store.recover_jobs() == 1
    result = store.get_research_run(run_id)
    assert result["status"] == "interrupted" and result["visited"] == ["twitter:peace_a"]
    assert result["frontier"][0]["account_key"] == "twitter:peace_b"


def test_checkpoint_invalid_secret_analysis_rolls_back_finished_batch(store):
    run_id = begin(store)
    with pytest.raises(ValueError, match="密钥"):
        store.checkpoint_research(run_id, nodes=nodes(), items=[post()], analysis={"api_key": "not-a-real-key"})
    assert store.get_research_run(run_id)["items"] == []
    assert store.list_accounts() == []


def test_cross_run_claim_review_is_rejected(store):
    first, second = begin(store), begin(store)
    store.checkpoint_research(first, nodes=nodes(), claims=[claim()])
    original = store.get_research_run(first)["claims"][0]
    with pytest.raises(LookupError):
        store.review_research_claim(second, original["id"], "rejected", "wrong run")
    assert store.get_research_run(first)["claims"][0]["status"] == "candidate"


def test_checkpoint_rejects_non_arrays(store):
    with pytest.raises(ValueError, match="数组"):
        store.checkpoint_research(begin(store), nodes={})


def test_postgres_draft_cas_uses_row_lock_and_bound_values(store, monkeypatch):
    from contextlib import contextmanager
    calls = []
    existing = {"key": "collection", "version": 1, "revision": 2, "data": '{"keyword":"old"}', "updated_at": "old"}

    class Cursor:
        rowcount = 1
        def __init__(self, row=None):
            self.row = row
        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, sql, values=()):
            calls.append((sql, values))
            if sql.startswith("SELECT"):
                return Cursor(existing)
            return Cursor()

    @contextmanager
    def transaction(write=False):
        assert write
        yield Connection()

    monkeypatch.setattr(store, "_postgres", True)
    monkeypatch.setattr(store, "_transaction", transaction)
    store.save_draft("collection", {"keyword": "' OR 1=1 --"}, expected_revision=2)
    assert calls[0][0].endswith("FOR UPDATE") and "%s" in calls[0][0]
    assert calls[0][1] == ("collection",)
    assert "' OR 1=1 --" not in calls[1][0]
    assert calls[1][1][1] == 3


def test_concurrent_resume_reserves_one_frontier_and_rolls_back_orphan_jobs(store):
    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes(), frontier=[nodes()[1]], status="partial")
    store.update_job(run_id, status="partial")
    def resume(_):
        try:
            return store.resume_research_job(run_id, {"keyword": "平和"})
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(resume, range(8)))
    assert len([value for value in results if value]) == 1
    assert store.list_jobs()["total"] == 2
    assert store.get_research_run(run_id)["job_id"] == next(value for value in results if value)["id"]


def test_profile_only_research_account_cannot_be_reassigned_to_different_identity(store):
    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes())
    saved = store.get_research_run(run_id)["accounts"][0]
    with pytest.raises(ValueError, match="保留原有证据"):
        store.save_account({"handle": "different_identity"}, saved["id"])
    assert store.get_account(saved["id"])["handle"] == saved["handle"]


def test_cancel_queued_resume_marks_current_run_cancelled_and_preserves_evidence(store):
    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes(), items=[post()], frontier=[nodes()[1]], status="partial")
    store.update_job(run_id, status="partial")
    resume = store.resume_research_job(run_id, {"keyword": "平和"})
    store.request_cancel(resume["id"])
    run = store.get_research_run(run_id)
    assert run["status"] == "cancelled" and len(run["items"]) == 1 and len(run["frontier"]) == 1


def test_clear_then_new_draft_cannot_reuse_revision_and_accept_stale_window(store):
    first = store.save_draft("collection", {"keyword": "first"}, expected_revision=0)
    store.clear_draft("collection", expected_revision=first["revision"])
    reopened = Store(store.path)
    newer = reopened.save_draft("collection", {"keyword": "new after clear"}, expected_revision=0)
    assert newer["revision"] > first["revision"]
    with pytest.raises(DraftConflict):
        reopened.save_draft("collection", {"keyword": "old window"}, expected_revision=first["revision"])
    with pytest.raises(DraftConflict):
        reopened.clear_draft("collection", expected_revision=first["revision"])
    assert reopened.get_draft("collection")["data"] == {"keyword": "new after clear"}
    reopened.close()


@pytest.mark.parametrize("status", ["confirmed", "rejected"])
def test_reviewed_claim_locks_exact_statement_and_metadata_but_appends_new_evidence(store, status):
    run_id = begin(store)
    store.checkpoint_research(run_id, nodes=nodes(), claims=[claim(statement="人工看到的原始文字", metadata={"method": "original"})])
    original = store.get_research_run(run_id)["claims"][0]
    store.review_research_claim(run_id, original["id"], status, evidence_url="https://example.org/official")
    store.checkpoint_research(run_id, claims=[claim(statement="后续自动生成不同措辞", metadata={"method": "automatic"}, evidence_urls=["https://example.org/new-capture"])])
    saved = store.get_research_run(run_id)["claims"][0]
    assert saved["status"] == status and saved["statement"] == "人工看到的原始文字"
    assert saved["metadata"] == {"method": "original"}
    assert saved["evidence_urls"] == ["https://x.com/peace_a", "https://example.org/new-capture"]
    with sqlite3.connect(store.path) as connection:
        captured = connection.execute("SELECT record FROM wb_research_claim_observations WHERE claim_id=?", (original["id"],)).fetchall()
    assert len(captured) == 2 and any("后续自动生成不同措辞" in row[0] for row in captured)
