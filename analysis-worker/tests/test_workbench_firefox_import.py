"""Synthetic Firefox databases only; never read a real profile or call X."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from app.workbench.server import create_app
from app.workbench.store import Store
from app.workbench.social import firefox_import as f

PLATFORM_PROFILES_INI = f._profiles_ini


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_SOCIAL_DIR", str(tmp_path / "workbench-social"))
    monkeypatch.setattr(f, "_profiles_ini", lambda: tmp_path / "Firefox/profiles.ini")


def database(tmp_path, *, version=17, platform="twitter", changes=None, extra=()):
    profile = tmp_path / "Firefox/Profiles/one"
    profile.mkdir(parents=True, exist_ok=True)
    names = ("name", "value", "host", "path", "expiry", "isSecure", "isHttpOnly", "originAttributes", "sameSite", "isPartitionedAttributeSet")
    expiry = int(time.time() + 3600) * (1000 if version >= 16 else 1)
    rules = f.PLATFORMS[platform]
    rows = [{"name": name, "value": "synthetic-secret-" + name, "host": rules["hosts"][0], "path": "/", "expiry": expiry,
             "isSecure": 1, "isHttpOnly": int(name == rules["authentication"]), "originAttributes": "", "sameSite": 0,
             "isPartitionedAttributeSet": 0} for name in rules["names"]]
    if changes:
        rows[0].update(changes)
    rows.extend(extra)
    with sqlite3.connect(profile / "cookies.sqlite") as con:
        con.execute("CREATE TABLE moz_cookies (name TEXT,value TEXT,host TEXT,path TEXT,expiry INTEGER,isSecure INTEGER,isHttpOnly INTEGER,originAttributes TEXT,sameSite INTEGER,isPartitionedAttributeSet INTEGER)")
        con.execute(f"PRAGMA user_version = {version}")
        for row in rows:
            con.execute("INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?,?,?)", [row.get(name, rows[0][name]) for name in names])
    return profile


def registry(profile):
    ini = profile.parents[1] / "profiles.ini"
    ini.write_text("[Profile0]\nName=Personal test\nIsRelative=1\nPath=Profiles/one\nDefault=1\n"
                   "[Profile1]\nName=Unavailable test\nIsRelative=1\nPath=Profiles/missing\n", encoding="utf-8")
    return ini


@pytest.fixture
def verified(monkeypatch):
    seen = []

    async def verify(cookies, platform="twitter"):
        seen.append(cookies)

    monkeypatch.setattr(f, "_verify_cookie_session", verify)
    return seen


@pytest.mark.parametrize("version", [14, 15, 16, 17])
def test_only_x_essential_cookies_correct_expiry_and_backup(tmp_path, verified, version, capsys):
    profile = database(tmp_path, version=version, extra=[
        {"name": "auth_token", "host": ".x.com.evil.test", "value": "not-readable-evil"},
        {"name": "auth_token", "host": ".twitter.com", "value": "not-readable-legacy"},
        {"name": "unneeded", "host": ".x.com", "value": "not-readable-extra"}])
    source_before = (profile / "cookies.sqlite").read_bytes()
    target = f.state_file("twitter")
    target.parent.mkdir(parents=True)
    original = b'{"cookies":[{"name":"old","value":"old-session"}]}'
    target.write_bytes(original)
    result = asyncio.run(f.import_firefox_x_session(profile, authorized=True))
    assert result["verified"] and result["saved"] and result["backup_created"]
    assert result["cookies_count"] == 3 and len(verified) == 1
    cookies = json.loads(target.read_text(encoding="utf-8"))["cookies"]
    assert {row["name"] for row in cookies} == set(f.NAMES)
    assert all(time.time() < row["expires"] < time.time() + 3700 for row in cookies)
    assert all(row["domain"] == ".x.com" for row in cookies)
    backups = list(target.parent.glob("state.before-firefox-*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert (profile / "cookies.sqlite").read_bytes() == source_before
    assert "secret" not in json.dumps(result) + capsys.readouterr().out


@pytest.mark.parametrize("changes,code", [
    ({"originAttributes": "^userContextId=1"}, "ambiguous_container"),
    ({"originAttributes": "^partitionKey=(https,x.com)"}, "ambiguous_container"),
    ({"isPartitionedAttributeSet": 1}, "ambiguous_container"),
    ({"expiry": 1825784727}, "invalid_expiry"),  # Seconds in a millisecond schema.
    ({"expiry": 1}, "invalid_expiry"),
    ({"expiry": 4102444800000000}, "invalid_expiry"),
    ({"expiry": 1700000000000}, "expired_session"),
    ({"isSecure": 0}, "unsafe_cookie"),
    ({"isHttpOnly": 0}, "unsafe_cookie"),
    ({"path": "/narrow"}, "unsafe_cookie"),
    ({"sameSite": 99}, "unsafe_cookie"),
])
@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_invalid_or_ambiguous_cookie_never_reaches_browser_or_write(tmp_path, verified, changes, code, platform):
    profile = database(tmp_path, platform=platform, changes=changes)
    with pytest.raises(f.SessionImportError) as exc:
        asyncio.run(f.import_firefox_session(profile, platform, authorized=True))
    assert exc.value.code == code and not verified
    assert not f.state_file(platform).exists()


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_duplicate_scope_and_unknown_schema_are_rejected(tmp_path, verified, platform):
    rules = f.PLATFORMS[platform]
    profile = database(tmp_path, platform=platform, extra=[{"name": rules["authentication"], "host": rules["hosts"][1]}])
    with pytest.raises(f.SessionImportError) as exc:
        asyncio.run(f.import_firefox_session(profile, platform, authorized=True))
    assert exc.value.code == "ambiguous_session"
    with sqlite3.connect(profile / "cookies.sqlite") as con:
        con.execute("PRAGMA user_version=18")
    with pytest.raises(f.SessionImportError) as exc:
        asyncio.run(f.import_firefox_session(profile, platform, authorized=True))
    assert exc.value.code == "unsupported_schema" and not verified


def test_authorization_and_explicit_profile_are_required_before_access(monkeypatch, tmp_path):
    monkeypatch.setattr(f, "_read_cookies", lambda *args: pytest.fail("read without explicit selection"))
    with pytest.raises(f.SessionImportError) as exc:
        asyncio.run(f.import_firefox_x_session(tmp_path))
    assert exc.value.code == "authorization_required"
    with pytest.raises(f.SessionImportError) as exc:
        asyncio.run(f.import_firefox_x_session("relative-profile", authorized=True))
    assert exc.value.code == "profile_required"


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_verification_failure_and_atomic_replace_failure_preserve_original(tmp_path, monkeypatch, platform):
    profile = database(tmp_path, platform=platform)
    target = f.state_file(platform)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"unchanged-old-session")

    async def failed(cookies, platform="twitter"):
        raise f.SessionImportError("verification_failed", "Controlled verification failure")

    monkeypatch.setattr(f, "_verify_cookie_session", failed)
    with pytest.raises(f.SessionImportError):
        asyncio.run(f.import_firefox_session(profile, platform, authorized=True))
    assert target.read_bytes() == b"unchanged-old-session"
    assert list(target.parent.glob("state.before-firefox-*.json")) == []

    async def verified(cookies, platform="twitter"):
        pass

    monkeypatch.setattr(f, "_verify_cookie_session", verified)
    monkeypatch.setattr(f.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("synthetic-secret-not-logged")))
    with pytest.raises(f.SessionImportError) as exc:
        asyncio.run(f.import_firefox_session(profile, platform, authorized=True))
    assert exc.value.code == "save_failed" and "synthetic-secret" not in str(exc.value)
    assert target.read_bytes() == b"unchanged-old-session"
    assert not list(target.parent.glob(".firefox-import-*.tmp"))


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_actual_verifier_uses_fresh_home_and_closes_before_save(tmp_path, monkeypatch, platform):
    profile = database(tmp_path, platform=platform)
    rules = f.PLATFORMS[platform]
    visits, closed = [], []

    class Context:
        async def add_cookies(self, cookies):
            assert {cookie["name"] for cookie in cookies} == set(rules["names"])

    @asynccontextmanager
    async def browser(actual, **options):
        assert actual == platform and options == {"visible": False, "require_session": False}
        try:
            yield Context(), object()
        finally:
            closed.append(True)

    async def navigate(page, platform, url):
        visits.append(url)
        return {f"{platform}_auth": True}

    def before_save():
        assert closed == [True]

    monkeypatch.setattr(f, "browser_session", browser)
    monkeypatch.setattr(f, "navigate", navigate)
    result = asyncio.run(f.import_firefox_session(profile, platform, authorized=True, before_save=before_save))
    assert result["verified"] and result["verification"] == rules["verification"]
    assert visits == [rules["home"]]


def test_cli_check_only_has_no_secret_output_or_writes(tmp_path, verified, capsys):
    profile = database(tmp_path, changes={"sameSite": 256})
    assert f.main(["--profile", str(profile), "--confirm-authorized", "--check-only"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["saved"] is False and "synthetic-secret" not in output
    assert "sameSite" not in verified[0][0]
    assert not f.state_file("twitter").exists()


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_cancelled_home_verification_closes_browser_and_never_saves(tmp_path, monkeypatch, platform):
    profile = database(tmp_path, platform=platform)
    target = f.state_file(platform)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original-session")

    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()

        class Context:
            async def add_cookies(self, cookies):
                pass

        @asynccontextmanager
        async def browser(*args, **kwargs):
            try:
                yield Context(), object()
            finally:
                closed.set()

        async def navigate(*args):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(f, "browser_session", browser)
        monkeypatch.setattr(f, "navigate", navigate)
        task = asyncio.create_task(f.import_firefox_session(profile, platform, authorized=True))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()

    asyncio.run(scenario())
    assert target.read_bytes() == b"original-session"
    assert not list(target.parent.glob("state.before-firefox-*.json"))


def test_windows_and_mac_profile_index_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(f.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert PLATFORM_PROFILES_INI() == tmp_path / "Mozilla/Firefox/profiles.ini"
    monkeypatch.setattr(f.sys, "platform", "darwin")
    monkeypatch.setattr(f.Path, "home", lambda: tmp_path)
    assert PLATFORM_PROFILES_INI() == tmp_path / "Library/Application Support/Firefox/profiles.ini"


def test_profile_list_only_reads_index_and_never_exposes_paths(tmp_path, monkeypatch):
    profile = database(tmp_path)
    registry(profile)
    monkeypatch.setattr(f.sqlite3, "connect", lambda *args, **kwargs: pytest.fail("profile listing opened cookies"))
    result = f.list_firefox_profiles()
    assert len(result["items"]) == 2
    assert result["items"][0]["default"] and result["items"][0]["available"]
    assert not result["items"][1]["available"]
    assert set(result["items"][0]) == {"id", "name", "default", "available"}
    assert str(profile) not in json.dumps(result)
    assert f.resolve_firefox_profile(result["items"][0]["id"]) == profile
    with pytest.raises(f.SessionImportError):
        f.resolve_firefox_profile(str(profile))


def test_api_requires_registered_profile_then_runs_verified_import(tmp_path, monkeypatch, verified):
    profile = database(tmp_path)
    registry(profile)
    with TestClient(create_app(Store(tmp_path / "api.sqlite3"))) as client:
        listed = client.get("/api/v2/social/firefox-profiles").json()
        profile_id = listed["items"][0]["id"]
        assert not verified
        invalid = [
            {"platform": "instagram", "method": "firefox", "profile_id": profile_id},
            {"platform": "twitter", "method": "firefox"},
            {"platform": "twitter", "method": "firefox", "profile_id": "unknown"},
            {"platform": "twitter", "method": "firefox", "profile_path": str(profile)},
        ]
        for params in invalid:
            response = client.post("/api/v2/jobs", json={"kind": "login", "params": params})
            assert response.status_code == 422 and "error" in response.json()
        assert not verified
        response = client.post("/api/v2/jobs", json={"kind": "login", "params": {
            "platform": "twitter", "method": "firefox", "profile_id": profile_id}})
        assert response.status_code == 202
        jid = response.json()["id"]
        for _ in range(200):
            job = client.get(f"/api/v2/jobs/{jid}").json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert job["status"] == "succeeded", job
        assert job["result"]["method"] == "firefox" and job["result"]["verified"]
        assert "synthetic-secret" not in json.dumps(job)
        assert str(profile) not in json.dumps(job)


@pytest.mark.parametrize("version", [9, 15, 16, 17])
@pytest.mark.parametrize("host", [".instagram.com", "instagram.com", "www.instagram.com"])
def test_instagram_registered_import_exact_scope_units_backup_and_platform_isolation(tmp_path, verified, version, host):
    profile = database(tmp_path, version=version, platform="instagram", extra=[
        {"name": "sessionid", "host": ".instagram.com.evil.test", "value": "forbidden-other-domain"},
        {"name": "sessionid", "host": "api.instagram.com", "value": "forbidden-subdomain"},
        {"name": "auth_token", "host": ".x.com", "value": "forbidden-other-platform"},
        {"name": "unneeded", "host": ".instagram.com", "value": "forbidden-extra-field"}])
    with sqlite3.connect(profile / "cookies.sqlite") as connection:
        connection.execute("UPDATE moz_cookies SET host=? WHERE host='.instagram.com' AND name IN ('sessionid','csrftoken','ds_user_id')", (host,))
    source_before = (profile / "cookies.sqlite").read_bytes()
    registry(profile)
    profile_id = f.list_firefox_profiles()["items"][0]["id"]
    target = f.state_file("instagram")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old-instagram-state")
    other = f.state_file("twitter")
    other.parent.mkdir(parents=True)
    other.write_bytes(b"unrelated-twitter-state")
    result = asyncio.run(f.import_registered_firefox_session(profile_id, "instagram"))
    assert result["platform"] == "instagram" and result["saved"] and result["verified"]
    assert result["verification"] == "instagram_home_authenticated"
    cookies = json.loads(target.read_text(encoding="utf-8"))["cookies"]
    assert {cookie["name"] for cookie in cookies} == {"sessionid", "csrftoken", "ds_user_id"}
    assert all(cookie["domain"] == host and time.time() < cookie["expires"] < time.time() + 3700 for cookie in cookies)
    assert list(target.parent.glob("state.before-firefox-*.json"))[0].read_bytes() == b"old-instagram-state"
    assert other.read_bytes() == b"unrelated-twitter-state"
    assert (profile / "cookies.sqlite").read_bytes() == source_before
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.parametrize("removed,valid", [("ds_user_id", True), ("sessionid", False), ("csrftoken", False)])
def test_instagram_required_fields_and_check_only(tmp_path, verified, removed, valid):
    profile = database(tmp_path, platform="instagram")
    with sqlite3.connect(profile / "cookies.sqlite") as connection:
        connection.execute("DELETE FROM moz_cookies WHERE name=?", (removed,))
    if valid:
        result = asyncio.run(f.import_firefox_session(profile, "instagram", authorized=True, check_only=True))
        assert result["verified"] and not result["saved"] and result["cookies_count"] == 2
    else:
        with pytest.raises(f.SessionImportError) as error:
            asyncio.run(f.import_firefox_session(profile, "instagram", authorized=True))
        assert error.value.code == "missing_session" and not verified
    assert not f.state_file("instagram").exists()
