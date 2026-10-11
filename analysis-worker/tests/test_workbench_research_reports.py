"""A research report uses the selected run, target and auditable observations."""
import asyncio
import copy
import json
import time

import httpx
from fastapi.testclient import TestClient

from app.workbench import pipeline
from app.workbench.server import create_app
from app.workbench.store import Store
from app.workbench.jobs import CancelledByUser, JobRunner


def source(handle, number, content):
    return {"type": "social", "source_type": "twitter", "account_key": f"twitter:{handle}",
            "title": content, "content": content, "source_name": handle,
            "url": f"https://x.com/{handle}/status/{number}",
            "published_at": "2026-10-08T00:00:00Z", "metadata": {"content_kind": "body",
                "collected_at": "2026-10-09T00:00:00Z", "collection_method": "visible_dom"}}


def create_run(store, handle="alpha", content="公開団体の声明には集会の計画が記載されている。"):
    params = {"research_goal": "比较公开声明的论证、行动及关联证据缺口", "start_date": "2026-10-01",
              "end_date": "2026-10-09", "source": "twitter", "max_accounts": 30}
    job = store.create_job("research", params)
    run = store.create_research_run(job["id"], params)
    store.checkpoint_research(run["id"], nodes=[{"account_key": f"twitter:{handle}"}],
        items=[source(handle, 1, content)], status="succeeded", phase="finished",
        summary={"stop_reason": "account_budget", "skipped_count": 5},
        analysis={"ai": {"complete": True}, "affiliations": []})
    store.update_job(job["id"], status="succeeded")
    return run


def terminal(client, identity):
    for _ in range(250):
        job = client.get(f"/api/v2/jobs/{identity}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    raise AssertionError("report job did not finish")


def model(monkeypatch, *, fail_synthesis_once=False, fail_synthesis_count=0):
    original_client = httpx.AsyncClient
    calls = []
    failures = [fail_synthesis_count or int(fail_synthesis_once)]
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        calls.append((payload, data))
        if "candidate_report" in data:
            assert '[S001]' in data['candidate_report'] and data['source_reviews']
            content = json.dumps({"accepted": True, "issues": []})
        elif payload.get("response_format"):
            content = json.dumps({"source_reviews": [{"citation_id": row["citation_id"],
                "quote": row["content_fragments"][0]["text"][:30], "reading": "来源提出了公开行动计划。",
                "limitations": "计划是否执行及账号主体仍需独立核验。"} for row in data["sources"]]}, ensure_ascii=False)
        else:
            if failures[0]:
                failures[0] -= 1
                return httpx.Response(503)
            content = "# 研究分析\n\n来源称公开团体提出集会计划；执行情况不足以判断。[S001]"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")
    monkeypatch.setattr(pipeline.httpx, "AsyncClient", lambda **kwargs:
        original_client(transport=httpx.MockTransport(handler), **kwargs))
    return calls


def request_params(run):
    return {"research_run_id": run["id"], "start_date": "2026-10-01", "end_date": "2026-10-09",
            "mode": "ai", "read_original": False}


def test_report_binds_goal_and_only_selected_run_without_promoting_identity(tmp_path, monkeypatch):
    store = Store(tmp_path / "research-report.sqlite3")
    run = create_run(store)
    create_run(store, "beta", "不应发送给本次报告的另一个运行资料。")
    calls = model(monkeypatch)
    with TestClient(create_app(store)) as client:
        response = client.post("/api/v2/jobs", json={"kind": "report", "params": request_params(run)})
        assert response.status_code == 202, response.text
        job = terminal(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        saved = client.get("/api/v2/reports/" + job["result"]["report_id"]).json()
        assert len(saved["snapshot"]["items"]) == 1
        assert saved["snapshot"]["items"][0]["account_status"] == "candidate"
        assert saved["snapshot"]["research_context"]["run_id"] == run["id"]
        assert saved["snapshot"]["research_context"]["research_goal"] == "比较公开声明的论证、行动及关联证据缺口"
        assert "不应发送" not in json.dumps(calls, ensure_ascii=False)
        assert calls[-1][1]["analysis_context"]["research"]["run_id"] == run["id"]
        assert saved["snapshot"]["report_audit"]["semantic_support_validated"] is False
        assert saved["snapshot"]["report_quality_review"]["requested"] is True
        assert "账号主体与组织隶属" in saved["markdown"]
        assert store.get_research_run(run["id"])["accounts"][0]["status"] == "candidate"


def test_failed_synthesis_retry_reuses_durable_completed_source_reviews(tmp_path, monkeypatch):
    store = Store(tmp_path / "retry-report.sqlite3")
    run = create_run(store)
    calls = model(monkeypatch, fail_synthesis_once=True)
    with TestClient(create_app(store)) as client:
        created = client.post("/api/v2/jobs", json={"kind": "report", "params": request_params(run)})
        assert created.status_code == 202, created.text
        failed = terminal(client, created.json()["id"])
        assert failed["status"] == "failed", failed
        assert not failed["result"].get("report_id")
        assert failed["result"]["report_checkpoint"]["complete"]
        assert store.list_reports()["total"] == 0
        retried = client.post(f"/api/v2/jobs/{failed['id']}/retry")
        assert retried.status_code == 202, retried.text
        succeeded = terminal(client, retried.json()["id"])
        assert succeeded["status"] == "succeeded", succeeded
        assert len([payload for payload, data in calls if payload.get("response_format") and "candidate_report" not in data]) == 1
        assert len([data for _, data in calls if "candidate_report" in data]) == 1
        assert len(calls) == 4
        assert store.get_job(failed["id"])["status"] == "failed"


def test_saved_report_renders_shared_registry_link_without_promoting_it_to_source_evidence(tmp_path, monkeypatch):
    store = Store(tmp_path / "shared-registry-link.sqlite3")
    run = create_run(store)
    store.checkpoint_research(run["id"], nodes=[{"account_key": "twitter:beta"}], status="succeeded")
    shared = "https://example.org/saved-registry-link"
    for account in store.get_research_run(run["id"])["accounts"]:
        store.save_account({"status": "candidate", "evidence_url": shared}, account["id"])
    model(monkeypatch)
    with TestClient(create_app(store)) as client:
        response = client.post("/api/v2/jobs", json={"kind": "report", "params": request_params(run)})
        assert response.status_code == 202, response.text
        job = terminal(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        report = client.get("/api/v2/reports/" + job["result"]["report_id"]).json()
        markdown = report["markdown"]
        assert "## 运行元数据〔程序生成〕" in markdown
        assert markdown.count(shared) == 2
        section = markdown.split("## 运行元数据〔程序生成〕", 1)[1].split("## ", 1)[0]
        assert "twitter:alpha" in section and "twitter:beta" in section
        assert section.count("candidate") == 2
        assert "不是帖文或简介正文" in section
        assert "不能证明共同发布、互认、背书或组织隶属" in section
        assert "[S001" not in section
        assert report["snapshot"]["report_machine_section_audit"]["changed"] is False
        assert {row["evidence_url"] for row in report["snapshot"]["research_context"]["accounts"]} == {shared}
        assert len(report["snapshot"]["items"]) == 1
        assert all(row["status"] == "candidate" for row in store.get_research_run(run["id"])["accounts"])


def test_report_rejects_missing_or_running_research_before_enqueue(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    store = Store(tmp_path / "invalid-run.sqlite3")
    job = store.create_job("research", {})
    run = store.create_research_run(job["id"], {})
    with TestClient(create_app(store)) as client:
        response = client.post("/api/v2/jobs", json={"kind": "report", "params": request_params(run)})
        assert response.status_code == 422
        assert "运行" in response.text or "完成" in response.text
        response = client.post("/api/v2/jobs", json={"kind": "report", "params": {**request_params(run), "research_run_id": "missing"}})
        assert response.status_code == 404
    assert store.list_jobs()["total"] == 1


def test_valid_research_ai_report_without_connection_does_not_enqueue(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    store = Store(tmp_path / "report-without-connection.sqlite3")
    run = create_run(store)
    initial_jobs = store.list_jobs()["total"]
    with TestClient(create_app(store, run_worker=False)) as client:
        response = client.post("/api/v2/jobs", json={"kind": "report", "params": request_params(run)})
        assert response.status_code == 422
        assert "AI 密钥" in response.json()["error"]["message"]
    assert store.list_jobs()["total"] == initial_jobs
    assert store.list_reports()["total"] == 0


def test_research_evidence_report_does_not_label_candidate_account_verified(tmp_path):
    store = Store(tmp_path / "evidence-boundary.sqlite3")
    run = create_run(store)
    with TestClient(create_app(store)) as client:
        created = client.post("/api/v2/jobs", json={"kind": "report", "params": {**request_params(run), "mode": "evidence"}})
        assert created.status_code == 202, created.text
        job = terminal(client, created.json()["id"])
        assert job["status"] == "succeeded", job
        saved = client.get("/api/v2/reports/" + job["result"]["report_id"]).json()
        assert "账号已核验" not in saved["markdown"]
        assert "账号主体与组织隶属" in saved["markdown"]


def test_retry_rereads_changed_body_instead_of_reusing_previous_quote(tmp_path, monkeypatch):
    store = Store(tmp_path / "changed-body.sqlite3")
    run = create_run(store)
    calls = model(monkeypatch, fail_synthesis_once=True)
    with TestClient(create_app(store)) as client:
        # Ordinary social material remains an appendix until identity is verified.
        account = store.get_research_run(run["id"])["accounts"][0]
        assert client.post(f"/api/v2/accounts/{account['id']}/verify", json={"verified": True, "evidence_url": "https://example.org/official-links"}).status_code == 200
        created = client.post("/api/v2/jobs", json={"kind": "report", "params": {"start_date": "2026-10-01", "end_date": "2026-10-09", "mode": "ai", "read_original": False}})
        failed = terminal(client, created.json()["id"])
        assert failed["status"] == "failed", failed
        updated = source("alpha", 1, "更正：原計画は撤回され、集会は開催しないと声明に記載されている。")
        updated["metadata"]["collected_at"] = "2026-10-09T01:00:00Z"
        # Exercise the supported upgrade from platform-rendered text to its
        # captured original; an arbitrary recapture is deliberately no overwrite.
        updated["metadata"]["text_representation"] = "original_after_platform_toggle"
        assert store.import_social([updated], [], [])["updated"] == 1
        retried = client.post(f"/api/v2/jobs/{failed['id']}/retry")
        assert retried.status_code == 202, retried.text
        succeeded = terminal(client, retried.json()["id"])
        assert succeeded["status"] == "succeeded", succeeded
        assert len([payload for payload, data in calls if payload.get("response_format") and "candidate_report" not in data]) == 2
        assert len([data for _, data in calls if "candidate_report" in data]) == 1
        saved = store.get_report(succeeded["result"]["report_id"])
        assert saved["snapshot"]["analysis_review"]["reading_state"]["resumed_source_count"] == 0
        assert "撤回" in saved["snapshot"]["analysis_review"]["source_reviews"][0]["quote"]


def test_second_failed_synthesis_retains_reused_reviews_for_a_third_attempt(tmp_path, monkeypatch):
    store = Store(tmp_path / "two-failures.sqlite3")
    run = create_run(store)
    calls = model(monkeypatch, fail_synthesis_count=2)
    with TestClient(create_app(store)) as client:
        created = client.post("/api/v2/jobs", json={"kind": "report", "params": request_params(run)})
        first = terminal(client, created.json()["id"])
        assert first["status"] == "failed"
        second_id = client.post(f"/api/v2/jobs/{first['id']}/retry").json()["id"]
        second = terminal(client, second_id)
        assert second["status"] == "failed"
        assert second["result"]["report_checkpoint"]["complete"]
        third_id = client.post(f"/api/v2/jobs/{second_id}/retry").json()["id"]
        third = terminal(client, third_id)
        assert third["status"] == "succeeded", third
        assert len([payload for payload, data in calls if payload.get("response_format") and "candidate_report" not in data]) == 1
        assert len([data for _, data in calls if "candidate_report" in data]) == 1
        assert len(calls) == 5


def test_completed_read_batch_is_saved_before_honouring_cancel(tmp_path, monkeypatch):
    store = Store(tmp_path / "cancel-batch.sqlite3")
    run = create_run(store)
    job = store.create_job("report", request_params(run))
    claimed = store.claim_job()
    assert claimed["id"] == job["id"]
    state = {"complete": True, "source_reviews": [{"citation_id": "S001", "quote": "完成摘录"}],
             "coverage_progress": {"input_source_count": 1, "reviewed_source_count": 1, "pending_source_count": 0}}
    async def build(items, params, **options):
        store.update_job(job["id"], cancel_requested=True)
        await options["checkpoint"](copy.deepcopy(state))
        raise AssertionError("cancellation was not honoured")
    monkeypatch.setattr(pipeline, "build_report", build)
    try:
        with __import__('pytest').raises(CancelledByUser):
            asyncio.run(JobRunner(store)._perform(claimed, asyncio.Event()))
        assert store.get_job(job["id"])["result"]["report_checkpoint"] == state
        assert store.list_reports()["total"] == 0
    finally:
        store.close()
