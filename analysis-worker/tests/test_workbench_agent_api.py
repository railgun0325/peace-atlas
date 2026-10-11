"""Isolated local Agent behavior, not a formal desktop/profile acceptance."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import time

import httpx
import pytest

from app.workbench.agent_api import ASSETS, MAX_BODY, register_agent_api, write_private_connection
from app.workbench.server import create_app
from app.workbench.store import Store


DESKTOP_HEADERS = {"x-workbench-token": "isolated-agent-desktop-session"}


@asynccontextmanager
async def local_client(tmp_path, monkeypatch, *, registered=True, worker=False):
    monkeypatch.setenv("WORKBENCH_SESSION_TOKEN", DESKTOP_HEADERS["x-workbench-token"])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WORKBENCH_SOCIAL_DIR", str(tmp_path / "isolated-social"))
    app = create_app(Store(tmp_path / "agent.sqlite3"), run_worker=worker)
    if registered and not hasattr(app.state, "agent_access"):
        register_agent_api(app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 48000)),
                                     base_url="http://127.0.0.1:18181") as client:
            yield app, client


async def grant(client, tmp_path, *, scopes=("read", "run", "cleanup"), name="connection"):
    path = tmp_path / (name + ".json")
    result = await client.post("/api/v2/agent/access", headers=DESKTOP_HEADERS,
                               json={"scopes": list(scopes), "credential_path": str(path)})
    assert result.status_code == 201, result.text
    connection = json.loads(path.read_text(encoding="utf-8"))
    assert "agent_token" not in result.json()
    assert connection["agent_token"] != DESKTOP_HEADERS["x-workbench-token"]
    return {"x-peace-atlas-agent": connection["agent_token"]}, connection


def research_params(**changes):
    return {"start_date": "2026-10-01", "end_date": "2026-10-10", "keyword": "反戦", "analysis_mode": "rules",
            "max_records": 5, "max_accounts": 5, "max_edges": 5, "time_budget_seconds": 90, **changes}


async def preview(client, headers, operation="research.start", arguments=None):
    response = await client.post("/agents/v1/preview", headers=headers,
                                json={"operation": operation, "arguments": arguments or {"params": research_params()}})
    assert response.status_code == 200, response.text
    return response.json()


def execution(prior, key="isolated-action-001"):
    return {"operation": prior["operation"], "arguments": prior["arguments"],
            "preview_id": prior["preview_id"], "operation_key": key, "confirmed": True}


def test_agent_support_is_registered_without_exposing_credentials(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch, registered=False) as (_, client):
            result = await client.get("/agents/v1/manifest")
            assert result.status_code == 200, result.text
            assert result.json()["default_access"] == "disabled"
            assert "agent_token" not in result.text
            assert DESKTOP_HEADERS["x-workbench-token"] not in result.text
    asyncio.run(scenario())


def test_public_discovery_authorization_origin_and_original_session_boundary(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (_, client):
            assert (await client.get("/agents/v1/schema")).status_code == 200
            assert (await client.get("/agents/v1/skill")).status_code == 200
            payload = {"operation": "overview.get", "arguments": {}}
            assert (await client.post("/agents/v1/read", json=payload)).status_code == 401
            assert (await client.get("/agents/v1/manifest", headers={"origin": "https://example.org"})).status_code == 403
            headers, _ = await grant(client, tmp_path)
            assert (await client.get("/api/v2/items", headers=headers)).status_code == 401
            assert (await client.post("/agents/v1/read", headers={**headers, "origin": "http://127.0.0.1:18181"}, json=payload)).status_code == 403
            assert (await client.post("/agents/v1/read", headers={**headers, "referer": "http://127.0.0.1:18181/"}, json=payload)).status_code == 403
            result = await client.post("/agents/v1/read", headers=headers, json=payload)
            assert result.status_code == 200
    asyncio.run(scenario())


def test_read_scope_and_catalog_cannot_open_admin_login_or_purge(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            headers, _ = await grant(client, tmp_path, scopes=("read",))
            assert (await client.post("/agents/v1/preview", headers=headers,
                                     json={"operation": "research.start", "arguments": {"params": research_params()}})).status_code == 403
            for operation in ("connection.set", "login.start", "shutdown", "items.purge"):
                assert (await client.post("/agents/v1/preview", headers=headers,
                                         json={"operation": operation, "arguments": {}})).status_code == 404
            login = app.state.store.create_job("login", {"platform": "twitter"})
            response = await client.post("/agents/v1/read", headers=headers,
                                         json={"operation": "job.get", "arguments": {"id": login["id"]}})
            assert response.status_code == 403
            jobs = await client.post("/agents/v1/read", headers=headers, json={"operation": "jobs.list", "arguments": {}})
            assert jobs.status_code == 200 and jobs.json()["items"] == []
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "retry"])
@pytest.mark.parametrize("scopes,allowed", [(('read', 'review'), True), (('read', 'run'), False)])
def test_auto_review_job_actions_require_review_not_run(tmp_path, monkeypatch, action, scopes, allowed):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            job = app.state.store.create_job('collect', {'_evidence_review': True,
                'source': 'evidence_review', 'review_options': {}, 'review_snapshot': [], 'snapshot_digest': 'synthetic'})
            if action == 'retry':
                app.state.store.update_job(job['id'], status='failed', result={'completed': 0}, error='synthetic failure')
            headers, _ = await grant(client, tmp_path, scopes=scopes)
            read = await client.post('/agents/v1/read', headers=headers,
                json={'operation': 'job.get', 'arguments': {'id': job['id']}})
            assert read.status_code == 200
            listing = await client.post('/agents/v1/read', headers=headers,
                json={'operation': 'jobs.list', 'arguments': {}})
            assert [row['id'] for row in listing.json()['items']] == [job['id']]
            response = await client.post('/agents/v1/preview', headers=headers,
                json={'operation': 'job.' + action, 'arguments': {'id': job['id']}})
            assert response.status_code == (200 if allowed else 403), response.text
            if allowed:
                executed = await client.post('/agents/v1/execute', headers=headers, json=execution(response.json()))
                assert executed.status_code == (202 if action == 'retry' else 200), executed.text
                assert app.state.store.get_job(job['id'])['status'] == ('failed' if action == 'retry' else 'cancelled')
            else:
                assert app.state.store.list_jobs()['total'] == 1
    asyncio.run(scenario())


def test_research_preview_binding_confirmation_expiry_and_one_use_idempotency(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            headers, _ = await grant(client, tmp_path)
            prior = await preview(client, headers)
            assert prior["created_jobs"] == 0
            assert prior["preview"]["confidence"] == "low"
            assert app.state.store.list_jobs()["total"] == 0
            changed = execution(prior)
            changed["arguments"] = {"params": {**prior["arguments"]["params"], "max_records": 6}}
            assert (await client.post("/agents/v1/execute", headers=headers, json=changed)).status_code == 409
            missing = execution(prior)
            missing.pop("confirmed")
            assert (await client.post("/agents/v1/execute", headers=headers, json=missing)).status_code == 422
            first = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            second = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            assert first.status_code == second.status_code == 202
            assert first.json()["id"] == second.json()["id"]
            assert second.headers["x-peace-atlas-replayed"] == "true"
            assert app.state.store.list_jobs()["total"] == 1
            assert (await client.post("/agents/v1/execute", headers=headers, json=execution(prior, "another-key-001"))).status_code == 409
            expired = await preview(client, headers)
            now = time.time()
            app.state.agent_access.clock = lambda: now + 301
            assert (await client.post("/agents/v1/execute", headers=headers,
                                     json=execution(expired, "expired-key-001"))).status_code == 409
            assert app.state.store.list_jobs()["total"] == 1
    asyncio.run(scenario())


def test_preview_is_grant_bound_and_revocation_does_not_cancel_existing_jobs(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            first, _ = await grant(client, tmp_path, name="first")
            second, _ = await grant(client, tmp_path, name="second")
            prior = await preview(client, first)
            assert (await client.post("/agents/v1/execute", headers=second, json=execution(prior))).status_code == 409
            created = await client.post("/agents/v1/execute", headers=first, json=execution(prior))
            assert created.status_code == 202
            identifier = created.json()["id"]
            revoked = await client.delete("/api/v2/agent/access", headers=DESKTOP_HEADERS)
            assert revoked.status_code == 200 and revoked.json()["revoked"] == 2
            assert app.state.store.get_job(identifier)["status"] == "queued"
            assert (await client.post("/agents/v1/read", headers=first,
                                     json={"operation": "job.get", "arguments": {"id": identifier}})).status_code == 401
    asyncio.run(scenario())


def test_failed_execution_receipt_prevents_automatic_repeat(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            headers, _ = await grant(client, tmp_path)
            arguments = {"params": {"start_date": "2026-10-01", "end_date": "2026-10-10", "mode": "ai"}}
            prior = await preview(client, headers, "report.start", arguments)
            original = app.state.agent_access.execute
            calls = []
            async def observed(*args):
                calls.append(True)
                return await original(*args)
            monkeypatch.setattr(app.state.agent_access, "execute", observed)
            first = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            second = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            assert first.status_code == second.status_code == 422
            assert first.json() == second.json()
            assert len(calls) == 1
            assert app.state.store.list_jobs()["total"] == 0
    asyncio.run(scenario())


def test_uncertain_mutation_is_retained_and_not_silently_retried(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            headers, _ = await grant(client, tmp_path)
            prior = await preview(client, headers)
            calls = []
            async def uncertain(*args):
                calls.append(True)
                raise OSError("synthetic interrupted result")
            monkeypatch.setattr(app.state.agent_access, "execute", uncertain)
            first = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            second = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            assert first.status_code == second.status_code == 500
            assert first.json()["error"]["code"] == "agent_execution_uncertain"
            assert first.json() == second.json() and len(calls) == 1
    asyncio.run(scenario())


def test_cleanup_is_recoverable_digest_bound_and_never_purges(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            imported = await client.post("/api/v2/items/import", headers=DESKTOP_HEADERS, json={"items": [{
                "title": "公开证据", "url": "https://example.org/agent-fixture", "content": "公开反战资料",
                "type": "news", "published_at": "2026-10-05T12:00:00+09:00"}]})
            assert imported.status_code == 200
            identifier = app.state.store.list_items()["items"][0]["id"]
            headers, _ = await grant(client, tmp_path)
            prior = await preview(client, headers, "items.cleanup", {"ids": [identifier], "action": "trash", "reason": "fixture"})
            assert prior["preview"]["count"] == 1
            trashed = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            assert trashed.status_code == 200
            assert app.state.store.list_items(trash=True)["total"] == 1
            missing_reason = await client.post("/agents/v1/preview", headers=headers, json={"operation": "items.cleanup",
                "arguments": {"ids": [identifier], "action": "restore"}})
            assert missing_reason.status_code == 422
            restore = await preview(client, headers, "items.cleanup", {"ids": [identifier], "action": "restore", "reason": "fixture restored"})
            assert (await client.post("/agents/v1/execute", headers=headers, json=execution(restore, "restore-key-001"))).status_code == 200
            assert app.state.store.list_items()["total"] == 1
            assert (await client.post("/agents/v1/preview", headers=headers, json={"operation": "items.cleanup",
                                     "arguments": {"ids": [identifier], "action": "purge"}})).status_code == 422
    asyncio.run(scenario())


def test_private_connection_writer_preserves_existing_file_and_cli_install_is_explicit(tmp_path):
    path = tmp_path / "existing.json"
    path.write_text("sentinel", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_private_connection(path, {"agent_token": "fixture-not-written"})
    assert path.read_text(encoding="utf-8") == "sentinel"
    spec = importlib.util.spec_from_file_location("peace_atlas_agent_client", ASSETS / "peace-atlas/scripts/peace_atlas.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    destination = tmp_path / "chosen-agent-root/peace-atlas"
    result = cli.install_skill(destination)
    assert result["contains_credentials"] is False
    assert (destination / "SKILL.md").is_file()
    assert (destination / "scripts/peace_atlas.py").is_file()
    assert not list(destination.rglob("*.json"))
    with pytest.raises(cli.ClientError):
        cli.install_skill(destination)
    for origin in ("https://127.0.0.1:1234", "http://example.org:1234", "http://user:password@127.0.0.1:1234", "http://127.0.0.1:1234/path"):
        with pytest.raises(cli.ClientError):
            cli.local_base(origin)


def test_remote_client_browser_admin_expiration_and_rules_only_query(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 50000)),
                                         base_url="http://127.0.0.1:18181") as remote:
                assert (await remote.get("/agents/v1/schema")).status_code == 403
                assert (await remote.get("/api/v2/agent/access", headers=DESKTOP_HEADERS)).status_code == 401
            assert (await client.post("/api/v2/agent/access", headers={"cookie": "wb_session=" + DESKTOP_HEADERS["x-workbench-token"]},
                json={"credential_path": str(tmp_path / "browser.json")})).status_code == 401
            assert not (tmp_path / "browser.json").exists()
            headers, _ = await grant(client, tmp_path)
            plan = {"operation": "query.plan", "arguments": {"keyword": "反戦", "use_ai": True}}
            assert (await client.post("/agents/v1/read", headers=headers, json=plan)).status_code == 422
            plan["arguments"]["use_ai"] = False
            assert (await client.post("/agents/v1/read", headers=headers, json=plan)).status_code == 200
            app.state.agent_access.clock = lambda: time.time() + 28801
            assert (await client.post("/agents/v1/read", headers=headers, json=plan)).status_code == 401
    asyncio.run(scenario())


def test_skill_installer_copies_exact_public_assets_and_rolls_back_owned_partial_target(tmp_path, monkeypatch):
    import shutil
    source = tmp_path / "isolated-skill-clone/peace-atlas"
    shutil.copytree(ASSETS / "peace-atlas", source)
    extras = [".env", "connection.secure", "temporary_installer.py", "install-result.yaml", "connection.json"]
    for name in extras:
        (source / name).write_text("synthetic secret marker - never copy", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("isolated_installer", source / "scripts/peace_atlas.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    destination = tmp_path / "selected-skills/peace-atlas"
    assert cli.install_skill(destination)["contains_credentials"] is False
    assert sorted(str(file.relative_to(destination)).replace("\\", "/") for file in destination.rglob("*") if file.is_file()) == sorted(cli.SKILL_FILES)
    sentinel = destination / "existing-sentinel.txt"
    sentinel.write_text("existing installation", encoding="utf-8")
    with pytest.raises(cli.ClientError):
        cli.install_skill(destination)
    assert sentinel.read_text(encoding="utf-8") == "existing installation"
    failing = tmp_path / "failed-skills/peace-atlas"
    real_copy = cli.shutil.copyfileobj
    copies = []
    def fail_second(original, saved):
        copies.append(True)
        if len(copies) == 2:
            saved.write(b"owned partial output")
            raise OSError("synthetic copy interruption")
        return real_copy(original, saved)
    monkeypatch.setattr(cli.shutil, "copyfileobj", fail_second)
    with pytest.raises(OSError):
        cli.install_skill(failing)
    assert not failing.exists()
    assert sentinel.read_text(encoding="utf-8") == "existing installation"
    (source / "references/api.md").unlink()
    incomplete = tmp_path / "incomplete-skills/peace-atlas"
    with pytest.raises(FileNotFoundError):
        cli.install_skill(incomplete)
    assert not incomplete.exists()


def test_simultaneous_same_key_creates_one_job_and_existing_key_cannot_change_scope(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            headers, _ = await grant(client, tmp_path)
            prior = await preview(client, headers)
            responses = await asyncio.gather(*[client.post("/agents/v1/execute", headers=headers,
                json=execution(prior)) for _ in range(8)])
            assert {response.status_code for response in responses} == {202}
            assert len({response.json()["id"] for response in responses}) == 1
            assert app.state.store.list_jobs()["total"] == 1
            changed = execution(prior)
            changed["arguments"]["params"]["max_records"] += 1
            assert (await client.post("/agents/v1/execute", headers=headers, json=changed)).status_code == 409
    asyncio.run(scenario())


def test_chunked_body_stops_reading_as_soon_as_limit_is_exceeded(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (_, client):
            consumed = []
            async def chunked():
                for index in range(MAX_BODY // 16384 + 20):
                    consumed.append(index)
                    yield b"X" * 16384
            response = await client.post("/agents/v1/read", content=chunked(),
                headers={"content-type": "application/json", "transfer-encoding": "chunked"})
            assert response.status_code == 413
            assert len(consumed) == MAX_BODY // 16384 + 1
    asyncio.run(scenario())


def test_cancelled_mutation_propagates_cancellation_but_retains_same_key_uncertain_receipt(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            headers, _ = await grant(client, tmp_path)
            prior = await preview(client, headers)
            called = []
            async def cancelled(*args):
                called.append(True)
                raise asyncio.CancelledError()
            monkeypatch.setattr(app.state.agent_access, "execute", cancelled)
            with pytest.raises((asyncio.CancelledError, RuntimeError)):
                await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            retried = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            assert retried.status_code == 500
            assert retried.json()["error"]["code"] == "agent_execution_uncertain"
            assert retried.headers["x-peace-atlas-replayed"] == "true"
            assert called == [True]
    asyncio.run(scenario())


def test_private_writer_failure_removes_only_its_new_file(tmp_path, monkeypatch):
    import os
    path = tmp_path / "failed-export.json"
    sentinel = tmp_path / "existing-sentinel.json"
    sentinel.write_text("legacy must remain", encoding="utf-8")
    def fail_sync(_fd):
        raise OSError("synthetic write failure")
    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(OSError):
        write_private_connection(path, {"agent_token": "synthetic-only"})
    assert not path.exists()
    assert sentinel.read_text(encoding="utf-8") == "legacy must remain"


def test_exported_connection_windows_acl_is_protected_and_has_only_owner_system(tmp_path):
    import os
    import subprocess
    if os.name != "nt":
        pytest.skip("Windows ACL acceptance")
    path = tmp_path / "acl-fixture.json"
    write_private_connection(path, {"agent_token": "synthetic-only"})
    result = subprocess.run(["pwsh", "-NoProfile", "-Command",
        "$agentFixtureAcl = Get-Acl -LiteralPath $env:PEACE_ATLAS_ACL_FIXTURE; "
        "$agentFixtureAcl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access)"],
        env={**os.environ, "PEACE_ATLAS_ACL_FIXTURE": str(path)}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    sddl = result.stdout.strip()
    assert sddl.startswith("D:P")
    assert sddl.count("(A;") == 2
    assert ";;;OW)" in sddl and ";;;SY)" in sddl
    assert not any(sid in sddl for sid in (";;;WD)", ";;;BU)", ";;;AU)"))


def test_continuation_uses_current_business_confirmation_and_preserves_omitted_limits(tmp_path, monkeypatch):
    from test_workbench_research_continuation import saved_run
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            original, _ = saved_run(app.state.store)
            headers, _ = await grant(client, tmp_path)
            prior = await preview(client, headers, "research.continue", {"id": original["id"], "limits": {"max_accounts": 2}})
            assert prior["arguments"]["limits"] == {"max_accounts": 2}
            assert app.state.store.list_jobs()["total"] == 1
            queued = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            assert queued.status_code == 202, queued.text
            child = queued.json()
            assert child["params"]["max_accounts"] == 2
            assert child["params"]["max_records"] == original["params"]["max_records"]
            assert child["params"]["resume_run_id"] == original["id"]
            assert app.state.store.get_research_run(original["id"])["current_job_id"] == child["id"]
            graph = await client.post("/agents/v1/read", headers=headers,
                                      json={"operation": "research.export", "arguments": {"id": original["id"]}})
            assert graph.status_code == 200
    asyncio.run(scenario())


def test_report_retry_and_cancel_use_existing_business_validation_and_receipts(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            from app.workbench.pipeline import prepare_report_input
            from app.workbench.report_inputs import capture_report_input_snapshot
            from test_workbench_report_input_snapshot import material
            app.state.store.import_items([material()])
            original = app.state.store.create_job("report", {"start_date": "2026-10-01", "end_date": "2026-10-10",
                "mode": "evidence", "read_original": False})
            snapshot = await prepare_report_input(app.state.store.report_items("2026-10-01", "2026-10-10"), original['params'])
            app.state.store.update_job(original["id"], status="failed", error="isolated failure",
                result={'report_input_snapshot': capture_report_input_snapshot(snapshot, original['params'])})
            headers, _ = await grant(client, tmp_path)
            retry = await preview(client, headers, "job.retry", {"id": original["id"]})
            response = await client.post("/agents/v1/execute", headers=headers, json=execution(retry))
            assert response.status_code == 202, response.text
            child = response.json()
            assert child["id"] != original["id"] and child["params"]["mode"] == "evidence"
            assert app.state.store.get_job(original["id"])["status"] == "failed"
            cancel = await preview(client, headers, "job.cancel", {"id": child["id"]})
            cancelled = await client.post("/agents/v1/execute", headers=headers, json=execution(cancel, "cancel-key-001"))
            assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
            repeated = await client.post("/agents/v1/execute", headers=headers, json=execution(cancel, "cancel-key-001"))
            assert repeated.json() == cancelled.json()
            assert app.state.store.list_jobs()["total"] == 2
    asyncio.run(scenario())


def test_agent_report_latest_preview_is_bound_and_rejection_receipt_is_replayed(tmp_path, monkeypatch):
    from test_workbench_report_input_snapshot import material
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            store = app.state.store
            store.import_items([material()])
            original = store.create_job('report', {'start_date': '2026-10-01', 'end_date': '2026-10-10',
                'mode': 'evidence', 'read_original': False})
            store.update_job(original['id'], status='failed', error='synthetic legacy failure')
            headers, _ = await grant(client, tmp_path)
            failed_original = await preview(client, headers, 'job.retry', {'id': original['id']})
            refused = await client.post('/agents/v1/execute', headers=headers,
                json=execution(failed_original, 'original-rejection-001'))
            assert refused.status_code == 422 and store.list_jobs()['total'] == 1
            latest = await preview(client, headers, 'job.retry', {
                'id': original['id'], 'options': {'input_mode': 'latest'}})
            store.import_items([material('プレビュー後に変更された公開本文。')])
            request = execution(latest, 'latest-conflict-001')
            changed = await client.post('/agents/v1/execute', headers=headers, json=request)
            assert changed.status_code == 409, changed.text
            assert changed.json()['error']['code'] == 'request_error'
            replay = await client.post('/agents/v1/execute', headers=headers, json=request)
            assert replay.status_code == 409 and replay.json() == changed.json()
            assert replay.headers['x-peace-atlas-replayed'] == 'true'
            assert store.list_jobs()['total'] == 1
            fresh = await preview(client, headers, 'job.retry', {
                'id': original['id'], 'options': {'input_mode': 'latest'}})
            resumed = await client.post('/agents/v1/execute', headers=headers,
                json=execution(fresh, 'latest-fresh-001'))
            assert resumed.status_code == 202, resumed.text
            child = store.get_job(resumed.json()['id'])
            assert child['result']['report_input_snapshot']['snapshot']['items'][0]['content'] == 'プレビュー後に変更された公開本文。'
            assert store.get_job(original['id'])['result'] is None
    asyncio.run(scenario())


def test_changed_cleanup_snapshot_is_rejected_and_failure_receipt_is_retained(tmp_path, monkeypatch):
    async def scenario():
        async with local_client(tmp_path, monkeypatch) as (app, client):
            await client.post("/api/v2/items/import", headers=DESKTOP_HEADERS, json={"items": [{
                "title": "原资料", "url": "https://example.org/cleanup-digest", "content": "原正文", "type": "news"}]})
            identifier = app.state.store.list_items()["items"][0]["id"]
            headers, _ = await grant(client, tmp_path)
            prior = await preview(client, headers, "items.cleanup", {"ids": [identifier], "action": "trash", "reason": "fixture"})
            await client.post("/api/v2/items/import", headers=DESKTOP_HEADERS, json={"items": [{
                "title": "更新资料", "url": "https://example.org/cleanup-digest", "content": "更新正文", "type": "news"}]})
            first = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            repeated = await client.post("/agents/v1/execute", headers=headers, json=execution(prior))
            assert first.status_code == repeated.status_code == 422
            assert repeated.headers["x-peace-atlas-replayed"] == "true"
            assert app.state.store.list_items()["total"] == 1
            assert app.state.store.list_items(trash=True)["total"] == 0
    asyncio.run(scenario())


def test_cli_runs_against_real_isolated_loopback_api_and_report_job(tmp_path, monkeypatch):
    import socket
    import subprocess
    import sys
    import threading
    import uvicorn

    monkeypatch.setenv("WORKBENCH_SESSION_TOKEN", DESKTOP_HEADERS["x-workbench-token"])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WORKBENCH_SOCIAL_DIR", str(tmp_path / "isolated-social"))
    app = create_app(Store(tmp_path / "live-fixture.sqlite3"))
    if not hasattr(app.state, "agent_access"):
        register_agent_api(app)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    cli = ASSETS / "peace-atlas/scripts/peace_atlas.py"
    connection = tmp_path / "live-connection.json"
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.02)
        assert server.started
        with httpx.Client(base_url=base, trust_env=False, headers=DESKTOP_HEADERS) as human:
            assert human.post("/api/v2/agent/access", json={"scopes": ["read", "run"], "credential_path": str(connection)}).status_code == 201
            assert human.post("/api/v2/items/import", json={"items": [{"title": "反战资料", "url": "https://example.org/live-agent-fixture",
                "content": "本地隔离的公开资料夹具", "type": "news", "published_at": "2026-10-05T12:00:00+09:00"}]}).status_code == 200
        def run_cli(*args):
            result = subprocess.run([sys.executable, "-B", str(cli), "--connection", str(connection), *args],
                                    capture_output=True, text=True, encoding="utf-8", timeout=20)
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        installed = tmp_path / "explicit-agent-skills/peace-atlas"
        installed_result = run_cli("install", "--destination", str(installed))
        assert installed_result["contains_credentials"] is False
        cli = installed / "scripts/peace_atlas.py"
        assert run_cli("manifest")["protocol"] == 1
        assert "research.start" in run_cli("schema")["operations"]
        sources = run_cli("read", "items.list")
        assert sources["total"] == 1
        source = run_cli("read", "items.get", "--json", json.dumps({"id": sources["items"][0]["id"]}))
        assert source["content"] == "本地隔离的公开资料夹具"
        arguments = tmp_path / "report-arguments.json"
        arguments.write_text(json.dumps({"params": {"start_date": "2026-10-05", "end_date": "2026-10-05", "mode": "evidence", "read_original": False}}), encoding="utf-8")
        saved_preview = tmp_path / "saved-preview.json"
        run_cli("preview", "report.start", "--input", str(arguments), "--output", str(saved_preview))
        submitted = run_cli("execute", "--preview-file", str(saved_preview), "--key", "actual-cli-report-001", "--confirm")
        repeated = run_cli("execute", "--preview-file", str(saved_preview), "--key", "actual-cli-report-001", "--confirm")
        assert submitted["id"] == repeated["id"]
        for _ in range(50):
            job = run_cli("read", "job.get", "--json", json.dumps({"id": submitted["id"]}))
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert job["status"] == "succeeded"
        report = run_cli("read", "report.get", "--json", json.dumps({"id": job["result"]["report_id"]}))
        assert "https://example.org/live-agent-fixture" in report["markdown"]
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()
