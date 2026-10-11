"""OS launch requests are entirely mocked; no real browser is opened."""
import subprocess

from fastapi.testclient import TestClient
import pytest

from app.workbench.server import create_app
from app.workbench.store import Store
from app.workbench.social import external_browser as browser


@pytest.mark.parametrize("platform,url", [("twitter", "https://x.com/home"), ("instagram", "https://www.instagram.com/")])
def test_windows_uses_only_system_url_handler(monkeypatch, platform, url):
    calls = []
    monkeypatch.setattr(browser, "_launcher", lambda: ("windows", None))
    monkeypatch.setattr(browser.os, "startfile", lambda value: calls.append(value), raising=False)
    result = browser.open_platform(platform)
    assert calls == [url]
    assert result["opened"] and not result["login_verified"] and not result["session_imported"]
    assert result["mode"] == "system_default"


@pytest.mark.parametrize("opener", ["/usr/bin/open", "/usr/bin/xdg-open"])
@pytest.mark.parametrize("still_running", [False, True])
def test_posix_launcher_has_fixed_argv_no_shell_or_profile(monkeypatch, opener, still_running):
    calls = []

    class Process:
        def wait(self, timeout):
            assert timeout == 0.5
            if still_running:
                raise subprocess.TimeoutExpired([opener], timeout)
            return 0

    def popen(argv, **options):
        calls.append((argv, options))
        return Process()

    monkeypatch.setattr(browser, "_launcher", lambda: ("posix", opener))
    monkeypatch.setattr(browser.subprocess, "Popen", popen)
    assert browser.open_platform("twitter")["opened"]
    argv, options = calls[0]
    assert argv == [opener, "https://x.com/home"]
    assert options["shell"] is False and options["start_new_session"] is True
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL


def test_readonly_availability_does_not_launch_or_load_sessions(monkeypatch):
    monkeypatch.setattr(browser, "_launcher", lambda: None)
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("availability opened a browser"))
    monkeypatch.setattr(browser.os, "startfile", lambda *args: pytest.fail("availability opened a browser"), raising=False)
    assert browser.capabilities()["available"] is False
    with pytest.raises(browser.BrowserOpenError) as exc:
        browser.open_platform("twitter")
    assert exc.value.code == "browser_unavailable"


@pytest.mark.parametrize("failure", ["exception", "nonzero"])
def test_launcher_failure_is_explicit_and_safe(monkeypatch, failure):
    class Process:
        def wait(self, timeout):
            return 7

    def popen(*args, **kwargs):
        if failure == "exception":
            raise OSError("private local error must not appear")
        return Process()

    monkeypatch.setattr(browser, "_launcher", lambda: ("posix", "/usr/bin/xdg-open"))
    monkeypatch.setattr(browser.subprocess, "Popen", popen)
    with pytest.raises(browser.BrowserOpenError) as exc:
        browser.open_platform("instagram")
    assert exc.value.code == "browser_open_failed"
    assert "private local" not in str(exc.value)


def test_api_auth_origin_fixed_platform_and_no_job_created(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("WORKBENCH_SESSION_TOKEN", "test-token")
    monkeypatch.setattr(browser, "_launcher", lambda: ("windows", None))
    monkeypatch.setattr(browser.os, "startfile", lambda url: calls.append(url), raising=False)
    with TestClient(create_app(Store(tmp_path / "open.db"), run_worker=False)) as client:
        assert client.post("/api/v2/social/browser/open", json={"platform": "twitter"}).status_code == 401
        client.cookies.set("wb_session", "test-token")
        assert client.get("/api/v2/social/browser").json()["available"] is True
        assert calls == []
        assert client.post("/api/v2/social/browser/open", json={"platform": "twitter"}, headers={"origin": "https://unrelated.example"}).status_code == 403
        for payload in ({"platform": "other"}, {"platform": "twitter", "url": "https://unrelated.example"}, {"platform": "twitter", "profile_id": "private"}):
            assert client.post("/api/v2/social/browser/open", json=payload).status_code == 422
        assert calls == []
        response = client.post("/api/v2/social/browser/open", json={"platform": "twitter"})
        assert response.status_code == 200 and response.json()["opened"] is True
        assert calls == ["https://x.com/home"]
        assert client.get("/api/v2/jobs").json()["total"] == 0
        monkeypatch.setattr(browser, "_launcher", lambda: None)
        response = client.post("/api/v2/social/browser/open", json={"platform": "twitter"})
        assert response.status_code == 503 and response.json()["error"]["code"] == "browser_unavailable"
