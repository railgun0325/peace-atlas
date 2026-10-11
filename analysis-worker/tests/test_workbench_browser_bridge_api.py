"""HTTP boundary checks with synthetic cookies; no browser or real profile access."""
import json
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

from app.workbench.server import create_app
from app.workbench.store import Store
from app.workbench.social import browser_bridge as bridge


BASE_URL = "http://127.0.0.1:18769"
APP_TOKEN = "synthetic-application-token"
EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32
OTHER_EXTENSION_ORIGIN = "chrome-extension://" + "b" * 32
JSON_HEADERS = {"origin": EXTENSION_ORIGIN, "content-type": "application/json"}
APP_HEADERS = {"origin": BASE_URL, "x-workbench-token": APP_TOKEN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_SESSION_TOKEN", APP_TOKEN)
    monkeypatch.setenv("WORKBENCH_SOCIAL_DIR", str(tmp_path / "social"))

    async def reject_browser(*args, **kwargs):
        pytest.fail("An API boundary test attempted unmocked browser access")

    def reject_save(*args, **kwargs):
        pytest.fail("An API boundary test attempted unmocked session persistence")

    monkeypatch.setattr(bridge, "verify_cookie_session", reject_browser)
    monkeypatch.setattr(bridge, "save_cookie_session", reject_save)
    with TestClient(create_app(Store(tmp_path / "bridge-api.sqlite3"), run_worker=False), base_url=BASE_URL) as test_client:
        yield test_client


def start(client, platform="twitter"):
    response = client.post("/api/v2/social/bridge/start", json={"platform": platform}, headers=APP_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    parsed = urlsplit(payload["pairing_url"])
    assert parsed.scheme == "http" and parsed.netloc == "127.0.0.1:18769"
    assert parsed.path == "/api/v2/social/bridge/connect"
    assert payload["expires_in"] == 300 and payload["verified"] is False
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    return parse_qs(parsed.query)["code"][0]


def pair(client, platform="twitter", *, origin=EXTENSION_ORIGIN):
    code = start(client, platform)
    response = client.post("/api/v2/social/bridge/pair", json={"platform": platform, "code": code}, headers={"origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers
    assert code not in response.text
    return response.json()["bridge_token"], code


def cookies(platform="twitter"):
    names, domain = (("auth_token", "ct0"), ".x.com") if platform == "twitter" else (("sessionid", "csrftoken"), ".instagram.com")
    return [{"name": name, "value": "synthetic-private-cookie-" + name, "domain": domain,
             "path": "/", "secure": True, "httpOnly": index == 0,
             "expires": -1, "sameSite": "None"} for index, name in enumerate(names)]


def no_echo(response, *secrets):
    rendered = response.text + str(dict(response.headers))
    assert all(value not in rendered for value in secrets)
    assert "set-cookie" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("supplied", [None, "wrong-token"])
def test_start_keeps_application_authentication(client, supplied):
    headers = {"origin": BASE_URL}
    if supplied:
        headers["x-workbench-token"] = supplied
    response = client.post("/api/v2/social/bridge/start", json={"platform": "twitter"}, headers=headers)
    assert response.status_code == 401 and response.json()["error"]["code"] == "session_required"
    assert client.app.state.browser_bridge.codes == {}


@pytest.mark.parametrize("origin", ["https://example.org", "http://127.0.0.1:18770", "http://localhost:18769", EXTENSION_ORIGIN])
def test_start_keeps_same_origin_even_with_valid_application_token(client, origin):
    response = client.post("/api/v2/social/bridge/start", json={"platform": "twitter"}, headers={**APP_HEADERS, "origin": origin})
    assert response.status_code == 403 and response.json()["error"]["code"] == "origin_denied"
    assert client.app.state.browser_bridge.codes == {}


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_authenticated_same_origin_start_and_extension_pair_without_app_token(client, platform):
    token, code = pair(client, platform)
    assert len(token) == 64 and token != code
    assert len(client.app.state.browser_bridge.bindings) == 1
    reused = client.post("/api/v2/social/bridge/pair", json={"platform": platform, "code": code}, headers=JSON_HEADERS)
    assert reused.status_code == 403 and reused.json()["error"]["code"] == "pair_denied"
    no_echo(reused, code, token)


@pytest.mark.parametrize("path", ["pair", "sync"])
@pytest.mark.parametrize("origin", [None, BASE_URL, "https://example.org", "chrome-extension://" + "q" * 32, "chrome-extension://" + "a" * 31])
def test_pair_and_sync_reject_missing_or_non_extension_origins_even_with_app_auth(client, path, origin):
    headers = {"x-workbench-token": APP_TOKEN}
    if origin:
        headers["origin"] = origin
    response = client.post(f"/api/v2/social/bridge/{path}", json={"platform": "twitter", "code": "a" * 64, "cookies": cookies()}, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "bridge_origin_denied"
    assert client.app.state.browser_bridge.bindings == {}


@pytest.mark.parametrize("path,method,expected", [
    ("/api/v2/social/bridge/pair", "GET", 401),
    ("/api/v2/social/bridge/pair", "HEAD", 401),
    ("/api/v2/social/bridge/pair", "PUT", 403),
    ("/api/v2/social/bridge/sync", "GET", 401),
    ("/api/v2/social/bridge/sync", "PATCH", 403),
    ("/api/v2/social/bridge/sync", "DELETE", 403),
    ("/api/v2/social/bridge/start", "OPTIONS", 401),
    ("/api/v2/social/bridge/start", "POST", 403),
    ("/api/v2/social/bridge/pair/", "POST", 403),
    ("/api/v2/social/bridge/sync/", "OPTIONS", 401),
    ("/api/v2/accounts", "POST", 403),
    ("/api/v2/health", "GET", 401),
])
def test_extension_exemption_does_not_escape_two_routes_or_post_options_methods(client, path, method, expected):
    response = client.request(method, path, headers=JSON_HEADERS, content=b"{}")
    assert response.status_code == expected
    assert "access-control-allow-origin" not in response.headers
    assert not client.app.state.browser_bridge.codes and not client.app.state.browser_bridge.bindings


@pytest.mark.parametrize("path", ["pair", "sync"])
def test_preflight_allows_only_two_headers_without_application_token_or_credentials(client, path):
    response = client.options(f"/api/v2/social/bridge/{path}", headers={"origin": EXTENSION_ORIGIN,
        "access-control-request-method": "POST", "access-control-request-headers": "Content-Type, X-Workbench-Bridge"})
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert response.headers["access-control-allow-methods"] == "POST"
    assert {part.strip().lower() for part in response.headers["access-control-allow-headers"].split(",")} == {"content-type", "x-workbench-bridge"}
    assert response.headers["vary"] == "Origin" and response.headers["cache-control"] == "no-store"
    assert "access-control-allow-credentials" not in response.headers and "set-cookie" not in response.headers


@pytest.mark.parametrize("method,headers", [("GET", "content-type"), ("PUT", "content-type"), ("POST", "x-workbench-token"), ("POST", "cookie"), ("POST", "content-type, x-extra")])
def test_preflight_rejects_other_methods_and_headers(client, method, headers):
    response = client.options("/api/v2/social/bridge/sync", headers={"origin": EXTENSION_ORIGIN,
        "access-control-request-method": method, "access-control-request-headers": headers})
    assert response.status_code == 403 and response.json()["error"]["code"] == "bridge_preflight_denied"


def test_pair_code_and_sync_token_are_bound_to_platform_and_extension_origin(client):
    code = start(client)
    wrong_pair = client.post("/api/v2/social/bridge/pair", json={"platform": "instagram", "code": code}, headers=JSON_HEADERS)
    assert wrong_pair.status_code == 403
    no_echo(wrong_pair, code)
    paired = client.post("/api/v2/social/bridge/pair", json={"platform": "twitter", "code": code}, headers=JSON_HEADERS)
    assert paired.status_code == 200  # Rejected platform mismatch did not consume the grant.
    token = paired.json()["bridge_token"]
    for platform, origin in [("instagram", EXTENSION_ORIGIN), ("twitter", OTHER_EXTENSION_ORIGIN)]:
        response = client.post("/api/v2/social/bridge/sync", json={"platform": platform, "cookies": cookies(platform)},
            headers={"origin": origin, "x-workbench-bridge": token})
        assert response.status_code == 401 and response.json()["error"]["code"] == "bridge_denied"
        no_echo(response, code, token, *[cookie["value"] for cookie in cookies(platform)])


@pytest.mark.parametrize("declared", ["32769", "-1", "+1", "nonnumeric", "1.0", " 10"])
@pytest.mark.parametrize("path", ["pair", "sync"])
def test_declared_length_is_bounded_and_strict_before_parsing(client, path, declared):
    response = client.post(f"/api/v2/social/bridge/{path}", content=b"{}", headers={**JSON_HEADERS, "content-length": declared})
    assert response.status_code == 413 and response.json()["error"]["code"] == "bridge_body_limit"


def test_exact_body_limit_is_accepted_and_actual_bytes_override_false_small_length(client):
    code = start(client)
    body = json.dumps({"platform": "twitter", "code": code}).encode()
    exact = body + b" " * (32768 - len(body))
    accepted = client.post("/api/v2/social/bridge/pair", content=exact, headers={**JSON_HEADERS, "content-length": "32768"})
    assert accepted.status_code == 200
    oversized = exact + b" "
    response = client.post("/api/v2/social/bridge/pair", content=oversized, headers={**JSON_HEADERS, "content-length": "1"})
    assert response.status_code == 413 and response.json()["error"]["code"] == "bridge_body_limit"
    no_echo(response, code)


def test_extremely_long_decimal_content_length_is_rejected_as_size_limit_not_conversion_error(client):
    response = client.post("/api/v2/social/bridge/sync", content=b"{}", headers={**JSON_HEADERS, "content-length": "9" * 5000})
    assert response.status_code == 413 and response.json()["error"]["code"] == "bridge_body_limit"


@pytest.mark.parametrize("path", ["pair", "sync"])
def test_stream_without_content_length_is_limited_before_downstream_models(client, path):
    def chunks():
        yield b"{" + b" " * 16000
        yield b" " * 16768 + b"}"

    response = client.post(f"/api/v2/social/bridge/{path}", content=chunks(), headers=JSON_HEADERS)
    assert "content-length" not in response.request.headers
    assert response.status_code == 413 and response.json()["error"]["code"] == "bridge_body_limit"


def test_stream_without_content_length_within_bound_is_consumed_once_and_parsed(client):
    code = start(client)
    body = json.dumps({"platform": "twitter", "code": code}).encode()

    def chunks():
        yield body[:25]
        yield body[25:]

    response = client.post("/api/v2/social/bridge/pair", content=chunks(), headers=JSON_HEADERS)
    assert "content-length" not in response.request.headers and response.status_code == 200
    assert response.json()["platform"] == "twitter"


@pytest.mark.parametrize("content_type", ["text/plain", "application/jsonp", "application/x-www-form-urlencoded"])
def test_bridge_rejects_non_json_content_type(client, content_type):
    response = client.post("/api/v2/social/bridge/pair", content=b"{}", headers={**JSON_HEADERS, "content-type": content_type})
    assert response.status_code == 415 and response.json()["error"]["code"] == "bridge_content_type"


def test_sync_verification_failure_never_saves_or_echoes_cookies_pairing_code_or_token(client, monkeypatch, capsys, caplog):
    token, code = pair(client)
    seen = []

    async def failed(platform, supplied):
        seen.append(platform)
        raise bridge.BridgeError("verification_failed", "合成会话未通过验证，未保存。", 422)

    monkeypatch.setattr(bridge, "verify_cookie_session", failed)
    response = client.post("/api/v2/social/bridge/sync", json={"platform": "twitter", "cookies": cookies()},
        headers={**JSON_HEADERS, "x-workbench-bridge": token})
    assert response.status_code == 422 and response.json()["error"]["code"] == "verification_failed"
    assert seen == ["twitter"]
    no_echo(response, token, code, *[cookie["value"] for cookie in cookies()])
    captured = capsys.readouterr()
    emitted = captured.out + captured.err + caplog.text
    assert all(secret not in emitted for secret in [token, code, *[cookie["value"] for cookie in cookies()]])


@pytest.mark.parametrize("platform", ["twitter", "instagram"])
def test_sync_verified_session_saves_once_and_returns_metadata_without_secret_values(client, monkeypatch, platform):
    token, code = pair(client, platform)
    order = []

    async def verified(selected, supplied):
        assert selected == platform and supplied == cookies(platform)
        order.append("verified")

    def saved(selected, supplied):
        assert selected == platform and supplied == cookies(platform)
        assert order == ["verified"]
        order.append("saved")
        return True

    monkeypatch.setattr(bridge, "verify_cookie_session", verified)
    monkeypatch.setattr(bridge, "save_cookie_session", saved)
    response = client.post("/api/v2/social/bridge/sync", json={"platform": platform, "cookies": cookies(platform)},
        headers={**JSON_HEADERS, "x-workbench-bridge": token})
    assert response.status_code == 200 and order == ["verified", "saved"]
    assert response.json() == {"platform": platform, "source": "browser_extension", "verified": True,
        "saved": True, "backup_created": True, "cookies_count": 2, "state": "ready"}
    no_echo(response, token, code, *[cookie["value"] for cookie in cookies(platform)])
    assert response.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("case", ["missing-token", "malformed-code", "invalid-cookie", "invalid-platform", "extra-field"])
def test_invalid_bridge_payloads_never_reach_verification_or_echo_secrets(client, case):
    token, code = pair(client)
    supplied = cookies()
    payload = {"platform": "twitter", "cookies": supplied}
    headers = {**JSON_HEADERS, "x-workbench-bridge": token}
    path = "/api/v2/social/bridge/sync"
    if case == "missing-token":
        del headers["x-workbench-bridge"]
    elif case == "malformed-code":
        path = "/api/v2/social/bridge/pair"
        payload = {"platform": "twitter", "code": "synthetic-private-invalid-code"}
    elif case == "invalid-cookie":
        supplied[0]["domain"] = ".other.example"
    elif case == "invalid-platform":
        payload["platform"] = "synthetic-private-platform"
    else:
        payload["unexpected"] = "synthetic-private-extra"
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code in {400, 401, 403, 422}
    no_echo(response, token, code, "synthetic-private-invalid-code", "synthetic-private-platform", "synthetic-private-extra",
            *[cookie["value"] for cookie in supplied])
