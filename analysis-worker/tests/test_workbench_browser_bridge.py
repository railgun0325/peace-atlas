"""Pairing and persistence boundaries with fake extension values and no network."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from app.workbench.social import browser_bridge as bridge
from app.workbench.social.common import SocialError


ORIGIN = "chrome-extension://" + "a" * 32
OTHER_ORIGIN = "chrome-extension://" + "b" * 32


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_SOCIAL_DIR", str(tmp_path / "social"))


def cookies(platform="twitter"):
    names = ("auth_token", "ct0", "twid") if platform == "twitter" else ("sessionid", "csrftoken", "ds_user_id")
    domain = ".x.com" if platform == "twitter" else ".instagram.com"
    return [{"name": name, "value": "synthetic-private-" + name, "domain": domain, "path": "/",
             "expires": bridge.time.time() + 3600, "secure": True, "httpOnly": i == 0, "sameSite": "Lax"}
            for i, name in enumerate(names)]


def grant(instance, platform="twitter"):
    started = instance.start(platform, "http://127.0.0.1:18090")
    code = parse_qs(urlsplit(started["pairing_url"]).query)["code"][0]
    return code


def paired(instance, platform="twitter"):
    return instance.pair(grant(instance, platform), platform, ORIGIN)["bridge_token"]


def old_state(platform="twitter"):
    target = bridge.state_file(platform)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original-test-session")
    return target


def test_codes_expire_are_one_use_and_bind_to_platform_origin(monkeypatch):
    clock = [1800000000.0]
    monkeypatch.setattr(bridge.time, "time", lambda: clock[0])
    instance = bridge.BrowserBridge()
    code = grant(instance)
    assert code not in instance.codes
    assert hashlib.sha256(code.encode()).hexdigest() in instance.codes
    for platform, origin in (("instagram", ORIGIN), ("twitter", "https://example.org")):
        with pytest.raises(bridge.BridgeError) as exc:
            instance.pair(code, platform, origin)
        assert exc.value.code == "pair_denied"
    token = instance.pair(code, "twitter", ORIGIN)["bridge_token"]
    with pytest.raises(bridge.BridgeError):
        instance.pair(code, "twitter", ORIGIN)
    for platform, origin in (("instagram", ORIGIN), ("twitter", OTHER_ORIGIN)):
        with pytest.raises(bridge.BridgeError) as exc:
            instance._binding(token, platform, origin)
        assert exc.value.code == "bridge_denied"
    expired = grant(instance)
    clock[0] += 300
    with pytest.raises(bridge.BridgeError):
        instance.pair(expired, "twitter", ORIGIN)
    clock[0] += 86400
    with pytest.raises(bridge.BridgeError):
        instance._binding(token, "twitter", ORIGIN)


def test_pairing_code_cannot_be_consumed_twice_by_concurrent_callers():
    instance = bridge.BrowserBridge()
    code = grant(instance)
    start = threading.Barrier(8)

    def attempt(_):
        start.wait(timeout=3)
        try:
            instance.pair(code, "twitter", ORIGIN)
            return True
        except bridge.BridgeError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(True) == 1 and len(instance.bindings) == 1


@pytest.mark.parametrize("origin", ["http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:99999",
    "http://localhost:18090", "https://127.0.0.1:18090", "http://127.0.0.1:18090/", {}, []])
def test_start_requires_real_loopback_port(origin):
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.BrowserBridge().start("twitter", origin)
    assert exc.value.code == "loopback_required"


def test_start_accepts_port_bounds_and_limits_pending_codes(monkeypatch):
    instance = bridge.BrowserBridge()
    for port in (1, 65535):
        assert instance.start("instagram", f"http://127.0.0.1:{port}")["expires_in"] == 300
    for _ in range(8):
        grant(instance)
    with pytest.raises(bridge.BridgeError) as exc:
        grant(instance)
    assert exc.value.code == "too_many_pairs" and exc.value.status == 429


@pytest.mark.parametrize("field,value", [("name", {}), ("name", []), ("domain", {}), ("domain", []),
    ("sameSite", {}), ("sameSite", []), ("value", {}), ("value", "bad\nvalue"), ("expires", True),
    ("expires", 10 ** 1000), ("expires", float("nan")), ("expires", float("inf")),
    ("domain", ".x.com.evil.test"), ("path", "/narrow"), ("secure", 1), ("httpOnly", False)])
def test_malformed_cookie_fields_are_controlled_errors(field, value):
    payload = cookies()
    payload[0][field] = value
    with pytest.raises(bridge.BridgeError):
        bridge.validate_cookies("twitter", payload)


@pytest.mark.parametrize("value", [{}, [], 3, "é"])
def test_pairing_fields_cannot_raise_type_errors(value):
    instance = bridge.BrowserBridge()
    code = grant(instance)
    for args in ((value, "twitter", ORIGIN), (code, "twitter", value), (code, value, ORIGIN)):
        with pytest.raises(bridge.BridgeError):
            instance.pair(*args)
    token = instance.pair(code, "twitter", ORIGIN)["bridge_token"]
    for args in ((value, "twitter", ORIGIN), (token, "twitter", value), (token, value, ORIGIN)):
        with pytest.raises(bridge.BridgeError):
            instance._binding(*args)


def test_whitelist_duplicate_missing_and_session_cookie_validation():
    payload = cookies()
    payload[0]["unapproved"] = "private"
    with pytest.raises(bridge.BridgeError):
        bridge.validate_cookies("twitter", payload)
    for bad in ([cookies()[0], cookies()[0]], cookies()[1:]):
        with pytest.raises(bridge.BridgeError):
            bridge.validate_cookies("twitter", bad)
    payload = cookies("instagram")
    payload[0]["expires"] = -1
    assert bridge.validate_cookies("instagram", payload)[0]["expires"] == -1
    with pytest.raises(bridge.BridgeError):
        bridge.validate_cookies({}, payload)


def test_sync_verifies_before_atomic_save_and_returns_no_cookie_values(monkeypatch):
    target = old_state()
    instance = bridge.BrowserBridge()
    token = paired(instance)
    calls = []

    async def verified(platform, values):
        assert target.read_bytes() == b"original-test-session"
        calls.append(platform)

    monkeypatch.setattr(bridge, "verify_cookie_session", verified)
    result = asyncio.run(instance.sync(token, "twitter", ORIGIN, cookies()))
    assert result["verified"] and result["saved"] and result["backup_created"]
    assert calls == ["twitter"] and len(json.loads(target.read_text())["cookies"]) == 3
    backup = list(target.parent.glob("state.before-browser-*.json"))
    assert len(backup) == 1 and backup[0].read_bytes() == b"original-test-session"
    assert "synthetic-private" not in json.dumps(result)
    with pytest.raises(bridge.BridgeError) as exc:
        asyncio.run(instance.sync(token, "twitter", ORIGIN, cookies()))
    assert exc.value.code == "bridge_busy"


@pytest.mark.parametrize("expired_kind", ["binding", "cookie"])
def test_expiry_during_verification_prevents_save(monkeypatch, expired_kind):
    clock = [1800000000.0]
    monkeypatch.setattr(bridge.time, "time", lambda: clock[0])
    target = old_state()
    instance = bridge.BrowserBridge()
    token = paired(instance)
    payload = cookies()

    async def verified(*args):
        clock[0] += 86401 if expired_kind == "binding" else 3601

    monkeypatch.setattr(bridge, "verify_cookie_session", verified)
    with pytest.raises(bridge.BridgeError):
        asyncio.run(instance.sync(token, "twitter", ORIGIN, payload))
    assert target.read_bytes() == b"original-test-session"
    assert not list(target.parent.glob("state.before-browser-*.json"))


def test_verification_error_preserves_state_and_throttles_retries(monkeypatch):
    target = old_state()
    instance = bridge.BrowserBridge()
    token = paired(instance)

    async def failed(*args):
        raise bridge.BridgeError("challenge", "Controlled challenge", 422)

    monkeypatch.setattr(bridge, "verify_cookie_session", failed)
    with pytest.raises(bridge.BridgeError) as exc:
        asyncio.run(instance.sync(token, "twitter", ORIGIN, cookies()))
    assert exc.value.code == "challenge"
    assert target.read_bytes() == b"original-test-session"
    with pytest.raises(bridge.BridgeError) as exc:
        asyncio.run(instance.sync(token, "twitter", ORIGIN, cookies()))
    assert exc.value.code == "bridge_busy"


@pytest.mark.parametrize("cancel", [False, True])
def test_verification_timeout_or_cancel_closes_context_without_saving(monkeypatch, cancel):
    assert bridge.VERIFY_TIMEOUT_SECONDS == 22
    if not cancel:
        monkeypatch.setattr(bridge, "VERIFY_TIMEOUT_SECONDS", 0.03)
    target = old_state()
    instance = bridge.BrowserBridge()
    token = paired(instance)

    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()

        class Context:
            async def add_cookies(self, values):
                assert len(values) == 3

        @asynccontextmanager
        async def browser(platform, **kwargs):
            assert kwargs == {"visible": False, "require_session": False}
            try:
                yield Context(), object()
            finally:
                closed.set()

        async def navigate(*args):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(bridge, "browser_session", browser)
        monkeypatch.setattr(bridge, "navigate", navigate)
        task = asyncio.create_task(instance.sync(token, "twitter", ORIGIN, cookies()))
        await asyncio.wait_for(entered.wait(), timeout=2)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(bridge.BridgeError) as exc:
                await asyncio.wait_for(task, timeout=1)
            assert exc.value.code == "verification_failed"
        assert closed.is_set()
        assert not instance._binding(token, "twitter", ORIGIN)["lock"].locked()

    asyncio.run(scenario())
    assert target.read_bytes() == b"original-test-session"
    assert not list(target.parent.glob("state.before-browser-*.json"))


def test_verifier_failure_never_exposes_browser_exception(monkeypatch, caplog):
    @asynccontextmanager
    async def browser(*args, **kwargs):
        raise RuntimeError("SENSITIVE_COOKIE_VALUE")
        yield

    monkeypatch.setattr(bridge, "browser_session", browser)
    with pytest.raises(bridge.BridgeError) as exc:
        asyncio.run(bridge.verify_cookie_session("twitter", cookies()))
    assert "SENSITIVE_COOKIE_VALUE" not in str(exc.value) + caplog.text


@pytest.mark.parametrize("failure", ["backup", "replace", "cleanup"])
def test_atomic_save_failures_preserve_original_and_do_not_leak(monkeypatch, caplog, failure):
    target = old_state()

    def fail(*args, **kwargs):
        raise OSError("SENSITIVE_FAILURE_DETAIL")

    if failure == "backup":
        monkeypatch.setattr(bridge.shutil, "copyfileobj", fail)
    else:
        monkeypatch.setattr(bridge.os, "replace", fail)
        if failure == "cleanup":
            monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.save_cookie_session("twitter", cookies())
    assert exc.value.code == "save_failed"
    assert "SENSITIVE_FAILURE_DETAIL" not in str(exc.value) + caplog.text
    assert target.read_bytes() == b"original-test-session"
    if failure != "cleanup":
        assert not list(target.parent.glob(".browser-bridge-*.tmp"))
