"""Automatic reuse tests use isolated app data, synthetic cookies and fake pages."""
import asyncio
from contextlib import asynccontextmanager
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from app.workbench.jobs import CancelledByUser
from app.workbench.server import create_app
from app.workbench.store import Store
from app.workbench.social import auto_session as auto, firefox_import as firefox
from app.workbench import social
from app.workbench.social.common import SocialError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "DesktopUserData"))
    monkeypatch.delenv("WORKBENCH_SOCIAL_DIR", raising=False)
    monkeypatch.setattr(firefox, "_profiles_ini", lambda: tmp_path / "Firefox/profiles.ini")


@pytest.fixture
def missing(monkeypatch):
    async def verify(platform):
        raise SocialError("missing_session", "Fixture missing state")
    monkeypatch.setattr(auto, "_verify_saved_session", verify)


def firefox_profile(tmp_path, platform="twitter"):
    profile = tmp_path / "Firefox/Profiles/default"
    profile.mkdir(parents=True)
    (profile.parents[1] / "profiles.ini").write_text("[Profile0]\nName=Test\nIsRelative=1\nPath=Profiles/default\nDefault=1\n", encoding="utf-8")
    with sqlite3.connect(profile / "cookies.sqlite") as con:
        con.execute("PRAGMA user_version=17")
        con.execute("CREATE TABLE moz_cookies(name TEXT,value TEXT,host TEXT,path TEXT,expiry INTEGER,isSecure INTEGER,isHttpOnly INTEGER,originAttributes TEXT,sameSite INTEGER,isPartitionedAttributeSet INTEGER)")
        rules = firefox.PLATFORMS[platform]
        for name in rules["names"]:
            con.execute("INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?,?,?)", (name, "synthetic-secret", rules["hosts"][0], "/", int(time.time() + 3600) * 1000, 1, int(name == rules["authentication"]), "", 0, 0))
    return profile


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_valid_current_session_never_reads_firefox(monkeypatch, platform):
    async def verified(actual):
        assert actual == platform
    monkeypatch.setattr(auto, "_verify_saved_session", verified)
    monkeypatch.setattr(firefox, "list_firefox_profiles", lambda: pytest.fail("valid current state must not search other accounts"))
    result = asyncio.run(auto.resolve_session(platform))
    assert result["verified"] and result["source"] == "workbench" and not result["saved"]
    assert not result["requires_login"]


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_default_firefox_is_verified_then_saved_to_current_desktop_data(tmp_path, monkeypatch, missing, platform):
    firefox_profile(tmp_path, platform)
    unrelated = tmp_path / f"old-QA/social/{platform}/state.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("old-qa-unchanged")
    calls = []

    async def verified(cookies, actual="twitter"):
        assert actual == platform
        calls.append(len(cookies))

    monkeypatch.setattr(firefox, "_verify_cookie_session", verified)
    result = asyncio.run(auto.resolve_session(platform))
    target = tmp_path / f"DesktopUserData/social/{platform}/state.json"
    assert target.is_file() and len(json.loads(target.read_text())["cookies"]) == 3
    assert unrelated.read_text() == "old-qa-unchanged"
    assert result["verified"] and result["saved"] and result["source"] == "firefox"
    assert calls == [3] and "synthetic-secret" not in json.dumps(result)


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_missing_session_requests_login_without_opening_any_browser(monkeypatch, missing, platform):
    monkeypatch.setattr(firefox, "list_firefox_profiles", lambda: {"items": []})
    result = asyncio.run(auto.resolve_session(platform))
    assert result["requires_login"] and not result["verified"] and not result["saved"]
    assert result["source"] == "none" and result["state"] == "requires_login"


@pytest.mark.parametrize("code", ["challenge", "rate_limited", "network_error", "runtime_unavailable"])
@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_blocked_verification_never_retries_other_accounts_or_requests_login(monkeypatch, code, platform):
    async def blocked(platform):
        raise SocialError(code, "Fixture blocked")
    monkeypatch.setattr(auto, "_verify_saved_session", blocked)
    monkeypatch.setattr(firefox, "list_firefox_profiles", lambda: pytest.fail("do not retry after a platform block"))
    result = asyncio.run(auto.resolve_session(platform))
    assert not result["verified"] and not result["requires_login"]
    assert result["code"] == code


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_multiple_defaults_never_choose_an_account(monkeypatch, missing, platform):
    monkeypatch.setattr(firefox, "list_firefox_profiles", lambda: {"items": [
        {"id": "one", "default": True, "available": True}, {"id": "two", "default": True, "available": True}]})
    result = asyncio.run(auto.resolve_session(platform))
    assert result["state"] == "ambiguous_profile" and not result["requires_login"]


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_save_callback_cancellation_is_not_reported_as_completed_check(tmp_path, monkeypatch, missing, platform):
    firefox_profile(tmp_path, platform)

    async def verified(cookies, platform="twitter"):
        pass

    def cancelled():
        raise CancelledByUser()

    monkeypatch.setattr(firefox, "_verify_cookie_session", verified)
    with pytest.raises(CancelledByUser):
        asyncio.run(auto.resolve_session(platform, before_save=cancelled))
    assert not firefox.state_file(platform).exists()


def test_current_session_probe_uses_saved_context_and_cancels_cleanly(tmp_path, monkeypatch):
    target = firefox.state_file("twitter")
    target.parent.mkdir(parents=True)
    target.write_text("synthetic-existing-state")

    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()

        @asynccontextmanager
        async def browser(platform, **options):
            assert platform == "instagram" and options == {"visible": False, "require_session": True}
            try:
                yield None, object()
            finally:
                closed.set()

        async def navigate(page, platform, url):
            assert url == "https://www.instagram.com/"
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(auto, "state_file", lambda platform: target)
        monkeypatch.setattr(auto, "browser_session", browser)
        monkeypatch.setattr(auto, "navigate", navigate)
        task = asyncio.create_task(auto.resolve_session("instagram"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()

    asyncio.run(scenario())
    assert target.read_text() == "synthetic-existing-state"


def test_auto_job_can_complete_a_check_without_claiming_login(tmp_path, monkeypatch):
    async def check(platform, **kwargs):
        return auto._result(platform, code="missing_session", message="Fixture requires explicit browser login")
    monkeypatch.setattr(auto, "resolve_session", check)
    with TestClient(create_app(Store(tmp_path / "auto.db"))) as client:
        bad = client.post("/api/v2/jobs", json={"kind": "login", "params": {"platform": "twitter", "method": "auto", "profile_id": "one"}})
        assert bad.status_code == 422
        response = client.post("/api/v2/jobs", json={"kind": "login", "params": {"platform": "twitter", "method": "auto"}})
        assert response.status_code == 202
        for _ in range(150):
            job = client.get("/api/v2/jobs/" + response.json()["id"]).json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert job["status"] == "succeeded"
        assert job["result"]["requires_login"] and not job["result"]["verified"]
        assert job["result"]["method"] == "auto" and job["result"]["source"] == "none"


@pytest.mark.parametrize("code,requires_login", [("missing_session", True), ("expired_session", True),
    ("login_required", True), ("challenge", False), ("rate_limited", False), ("verification_failed", False)])
def test_instagram_firefox_absent_vs_blocked_are_distinct(monkeypatch, missing, code, requires_login):
    monkeypatch.setattr(firefox, "list_firefox_profiles", lambda: {"items": [
        {"id": "default", "default": True, "available": True}]})

    async def imported(profile_id, platform="twitter", **kwargs):
        assert profile_id == "default" and platform == "instagram"
        raise firefox.SessionImportError(code, "Synthetic controlled outcome")

    monkeypatch.setattr(firefox, "import_registered_firefox_session", imported)
    result = asyncio.run(auto.resolve_session("instagram"))
    assert result["requires_login"] is requires_login and not result["verified"]
    assert result["code"] == code and not result["saved"]


def test_instagram_expired_saved_session_can_reuse_verified_firefox(tmp_path, monkeypatch):
    firefox_profile(tmp_path, "instagram")
    target = firefox.state_file("instagram")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"expired-workbench-session")

    async def expired(platform):
        assert platform == "instagram"
        raise SocialError("login_required", "Platform confirms old session is signed out")

    async def verified(cookies, platform="twitter"):
        assert platform == "instagram"

    monkeypatch.setattr(auto, "_verify_saved_session", expired)
    monkeypatch.setattr(firefox, "_verify_cookie_session", verified)
    result = asyncio.run(auto.resolve_session("instagram"))
    assert result["verified"] and result["source"] == "firefox" and result["saved"]
    assert list(target.parent.glob("state.before-firefox-*.json"))[0].read_bytes() == b"expired-workbench-session"


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_api_login_without_method_defaults_to_auto_never_manual_browser(tmp_path, monkeypatch, platform):
    calls = []

    async def check(actual, **kwargs):
        calls.append(actual)
        return auto._result(actual, verified=True, source="workbench", state="ready")

    async def forbidden_manual(*args, **kwargs):
        pytest.fail("An omitted method must never start a visible automated login")

    monkeypatch.setattr(auto, "resolve_session", check)
    monkeypatch.setattr(social, "login", forbidden_manual)
    with TestClient(create_app(Store(tmp_path / "default-auto.db"))) as client:
        response = client.post("/api/v2/jobs", json={"kind": "login", "params": {"platform": platform}})
        assert response.status_code == 202
        for _ in range(150):
            job = client.get("/api/v2/jobs/" + response.json()["id"]).json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert job["status"] == "succeeded" and job["params"]["method"] == "auto"
        assert job["result"]["verified"] and job["result"]["method"] == "auto"
    assert calls == [platform]
