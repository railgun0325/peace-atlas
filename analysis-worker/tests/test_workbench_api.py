import asyncio
import time

from fastapi.testclient import TestClient

from app.workbench.server import create_app
from app.workbench.store import Store


def material(**updates):
    return {"title": "沖縄の平和運動と憲法九条", "url": "https://example.org/evidence/1",
            "content": "沖縄の市民団体が憲法九条について公開討論を行った。", "source_name": "Public archive",
            "type": "news", "published_at": "2026-10-05T18:00:00+09:00", **updates}


def terminal(client, jid):
    for _ in range(150):
        response = client.get(f"/api/v2/jobs/{jid}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.02)
    raise AssertionError("worker did not reach terminal state")


def test_import_dates_deduplication_and_trust_boundary(tmp_path):
    with TestClient(create_app(Store(tmp_path / "api.db"))) as c:
        result = c.post("/api/v2/items/import", json={"items": [material(), material(), material(url="javascript:alert(1)")]})
        assert result.status_code == 200, result.text
        assert result.json()["inserted"] == 1
        assert result.json()["duplicates"] == 1
        assert result.json()["rejected"] == 1
        mentions = c.get("/api/v2/overview").json()["locations"]
        assert any(place["name"] == "沖縄県" and place["count"] == 1 for place in mentions)
        assert all("lat" not in place and "lon" not in place for place in mentions)
        assert c.get("/api/v2/items?start_date=2026-10-05&end_date=2026-10-05").json()["total"] == 1
        assert c.get("/api/v2/items?start_date=2026-10-06&end_date=2026-10-06").json()["total"] == 0
        social = material(url="https://example.org/social/1", type="social", title="別の平和運動", account_status="verified", evidence_url="https://example.org/about")
        c.post("/api/v2/items/import", json={"items": [social]})
        row = c.get("/api/v2/items?type=social").json()["items"][0]
        assert row.get("account_status") != "verified"
        assert c.get("/api/v2/items/missing").status_code == 404


def test_account_verification_is_explicit_and_tags_survive(tmp_path):
    with TestClient(create_app(Store(tmp_path / "accounts.db"))) as c:
        account = c.post("/api/v2/accounts", json={"name": "Peace group", "platform": "twitter", "handle": "peace", "url": "https://x.com/peace", "tags": ["沖縄"]}).json()
        aid = account["id"]
        assert c.post(f"/api/v2/accounts/{aid}/verify", json={"verified": True}).status_code == 422
        verified = c.post(f"/api/v2/accounts/{aid}/verify", json={"verified": True, "evidence_url": "https://example.org/official-account"})
        assert verified.status_code == 200, verified.text
        assert verified.json()["status"] == "verified"
        assert verified.json()["tags"] == ["沖縄"]
        c.patch(f"/api/v2/accounts/{aid}", json={"organization": "Archive"})
        assert c.get("/api/v2/accounts").json()["items"][0]["name"] == "Peace group"


def test_report_jobs_end_to_end_and_immutable_history(tmp_path):
    with TestClient(create_app(Store(tmp_path / "report.db"))) as c:
        c.post("/api/v2/items/import", json={"items": [material()]})
        params = {"start_date": "2026-10-05", "end_date": "2026-10-05", "include_news": True, "include_social": False, "mode": "evidence"}
        reports = []
        for title in ("First run", "Second run"):
            submitted = c.post("/api/v2/jobs", json={"kind": "report", "params": {**params, "title": title}})
            assert submitted.status_code == 202, submitted.text
            job = terminal(c, submitted.json()["id"])
            assert job["status"] == "succeeded", job
            report = c.get("/api/v2/reports/" + job["result"]["report_id"]).json()
            reports.append(report)
            assert report["source_count"] == 1
            assert "https://example.org/evidence/1" in report["markdown"]
            assert report["snapshot"]
            assert c.get(f"/api/v2/reports/{report['id']}/download").status_code == 200
        assert reports[0]["id"] != reports[1]["id"]
        assert c.get("/api/v2/reports/" + reports[0]["id"]).json()["markdown"] == reports[0]["markdown"]
        assert c.get("/api/v2/reports").json()["total"] == 2
        empty = c.post("/api/v2/jobs", json={"kind": "report", "params": {**params, "include_news": False, "include_social": True}}).json()
        assert terminal(c, empty["id"])["status"] == "failed"


def test_collection_failure_cancel_retry_and_partial(tmp_path, monkeypatch):
    from app.workbench import pipeline
    async def broken(params):
        return {"items": [], "diagnostics": [{"ok": False, "message": "fixture unavailable"}]}
    monkeypatch.setattr(pipeline, "collect", broken)
    params = {"source": "google_news", "start_date": "2026-10-05", "end_date": "2026-10-05"}
    with TestClient(create_app(Store(tmp_path / "jobs.db"))) as c:
        job = c.post("/api/v2/jobs", json={"kind": "collect", "params": params}).json()
        assert terminal(c, job["id"])["status"] == "failed"
        async def partial(params):
            return {"items": [pipeline.enrich_item(material())], "diagnostics": [{"ok": True}, {"ok": False, "message": "one source failed"}]}
        monkeypatch.setattr(pipeline, "collect", partial)
        retried = c.post(f"/api/v2/jobs/{job['id']}/retry").json()
        assert retried["id"] != job["id"]
        finished = terminal(c, retried["id"])
        assert finished["status"] == "partial", finished
        assert finished["result"]["inserted"] == 1
        async def slow(params):
            await asyncio.sleep(30)
        monkeypatch.setattr(pipeline, "collect", slow)
        pending = c.post("/api/v2/jobs", json={"kind": "collect", "params": params}).json()
        c.post(f"/api/v2/jobs/{pending['id']}/cancel")
        assert terminal(c, pending["id"])["status"] == "cancelled"


def test_desktop_session_and_cross_site_mutations(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_SESSION_TOKEN", "test-session-token")
    with TestClient(create_app(Store(tmp_path / "security.db"))) as c:
        assert c.get("/api/v2/health").status_code == 401
        assert c.get("/api/v2/health", headers={"x-workbench-token": "test-session-token"}).status_code == 200
        c.cookies.set("wb_session", "test-session-token")
        assert c.get("/api/v2/health").status_code == 200
        assert c.post("/api/v2/accounts", json={}, headers={"origin": "https://unrelated.example"}).status_code == 403


def test_invalid_dates_and_no_selected_sources(tmp_path):
    with TestClient(create_app(Store(tmp_path / "validation.db"))) as c:
        for params in ({"start_date": "2026-10-06", "end_date": "2026-10-05"}, {"start_date": "2026-10-05", "end_date": "2026-10-05", "include_news": False, "include_social": False}):
            r = c.post("/api/v2/jobs", json={"kind": "report", "params": params})
            assert r.status_code == 422
            assert r.json()["error"]["message"]
