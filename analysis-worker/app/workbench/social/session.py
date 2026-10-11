"""Platform-isolated, user-driven login and bounded browser lifetime."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import weakref
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import MESSAGES, PAGE_STATE_JS, PLATFORMS, SocialError, now, page_outcome, pause, platform_name, safe_error

LOGIN_TIMEOUT = 600.0
_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def social_dir() -> Path:
    if os.getenv("WORKBENCH_SOCIAL_DIR"):
        return Path(os.environ["WORKBENCH_SOCIAL_DIR"]).expanduser().resolve()
    if os.getenv("WORKBENCH_DATA_DIR"):
        return (Path(os.environ["WORKBENCH_DATA_DIR"]) / "social").expanduser().resolve()
    if os.getenv("WORKBENCH_DB_PATH"):
        return (Path(os.environ["WORKBENCH_DB_PATH"]).parent / "social").expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "data" / "social"


def state_file(platform: str) -> Path:
    return social_dir() / platform_name(platform) / "state.json"


def _browser_path() -> Path | None:
    """Inspect installed browser files without starting a driver or reading state."""
    configured = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")
    if configured and configured != "0":
        base = Path(configured)
    elif configured == "0":
        spec = importlib.util.find_spec("playwright")
        if not spec or not spec.submodule_search_locations:
            return None
        base = Path(next(iter(spec.submodule_search_locations))) / "driver" / "package" / ".local-browsers"
    elif sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "ms-playwright"
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Caches/ms-playwright"
    else:
        base = Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "ms-playwright"
    spec = importlib.util.find_spec("playwright")
    try:
        metadata = Path(next(iter(spec.submodule_search_locations))) / "driver/package/browsers.json"
        revision = next(row["revision"] for row in json.loads(metadata.read_text(encoding="utf-8"))["browsers"] if row["name"] == "chromium")
        directory = f"chromium-{revision}"
    except (AttributeError, StopIteration, OSError, ValueError, KeyError, TypeError):
        return None
    patterns = (f"{directory}/chrome-win64/chrome.exe", f"{directory}/chrome-win/chrome.exe") if sys.platform == "win32" else (
        (f"{directory}/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", f"{directory}/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", f"{directory}/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium", f"{directory}/chrome-mac-x64/Chromium.app/Contents/MacOS/Chromium", f"{directory}/chrome-mac/Chromium.app/Contents/MacOS/Chromium") if sys.platform == "darwin" else (f"{directory}/chrome-linux64/chrome", f"{directory}/chrome-linux/chrome"))
    paths = [path for pattern in patterns for path in base.glob(pattern) if path.is_file()]
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None


def _runtime() -> dict[str, bool]:
    installed = importlib.util.find_spec("playwright") is not None
    browser = _browser_path() is not None if installed else False
    return {"playwright_installed": installed, "browser_installed": browser, "ready": installed and browser}


def capabilities() -> dict[str, Any]:
    runtime = _runtime()
    rows = []
    for platform, name in (("twitter", "X / Twitter"), ("instagram", "Instagram")):
        path = state_file(platform)
        present = path.is_file()
        rows.append({"id": platform, "name": name,
            "state": "runtime_unavailable" if not runtime["ready"] else ("session_unverified" if present else "login_required"),
            "session_available": present,
            "session_updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if present else None,
            "modes": ["keyword", "account", "hashtag"],
            "detail": "已保存本工作台的独立会话；有效性将在平台页面验证。" if present else (
                "请在系统默认浏览器登录 X；若使用 Firefox，可在工作台选择配置并验证导入会话。" if platform == "twitter"
                else "请在系统默认浏览器完成 Instagram 登录；浏览器会话不会自动同步到工作台。")})
    return {"runtime": runtime, "platforms": rows}


def _lock(platform: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    return _LOCKS.setdefault(loop, {}).setdefault(platform, asyncio.Lock())


async def _close(pw, browser, context) -> None:
    for obj, method in ((context, "close"), (browser, "close"), (pw, "stop")):
        if obj is not None:
            try:
                await asyncio.wait_for(getattr(obj, method)(), timeout=8)
            except Exception:
                pass


@asynccontextmanager
async def browser_session(platform: str, *, visible: bool, require_session: bool):
    platform_name(platform)
    lock = _lock(platform)
    if lock.locked():
        raise SocialError("busy", MESSAGES["busy"])
    async with lock:
        path = state_file(platform)
        if require_session and not path.is_file():
            raise SocialError("login_required", MESSAGES["login_required"])
        if not _runtime()["ready"]:
            raise SocialError("runtime_unavailable", MESSAGES["runtime_unavailable"])
        pw = browser = context = None
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            # The regular bundled Chromium supports both manual login and headless
            # collection. No sandbox/site-isolation changes or detection patches.
            browser = await pw.chromium.launch(headless=not visible, executable_path=str(_browser_path()))
            options = {"viewport": {"width": 1280, "height": 900}, "locale": "zh-CN" if visible else "ja-JP", "timezone_id": "Asia/Tokyo"}
            # Manual login starts clean. An expired state can otherwise restore
            # an old onboarding/challenge flow. Keep it untouched until a new
            # authenticated session is verified and atomically saved.
            if require_session:
                options["storage_state"] = str(path)
            try:
                context = await browser.new_context(**options)
            except Exception:
                if require_session:
                    raise SocialError("login_required", "本工作台保存的会话无法载入，请重新登录。") from None
                raise
            page = await context.new_page()
            yield context, page
        finally:
            cleanup = asyncio.create_task(_close(pw, browser, context))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


async def _save_state(context, platform: str) -> None:
    path = state_file(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        await context.storage_state(path=str(temporary))
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def login(platform: str) -> dict[str, Any]:
    platform_name(platform)
    url = "https://x.com/i/flow/login" if platform == "twitter" else "https://www.instagram.com/accounts/login/"
    try:
        async with asyncio.timeout(LOGIN_TIMEOUT):
            async with browser_session(platform, visible=True, require_session=False) as (context, page):
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                while not page.is_closed():
                    state = await page.evaluate(PAGE_STATE_JS)
                    outcome = page_outcome(platform, state)
                    if outcome == "rate_limited":
                        message = "平台已临时限制本次登录尝试，已停止等待；原有会话未更新。请稍后再试，避免反复提交。"
                        if platform == "twitter":
                            message += "若 Firefox 中已登录 X，可返回工作台使用“复用 Firefox 会话”。"
                        raise SocialError("rate_limited", message)
                    if outcome == "ok":
                        # Read only within the browser runtime; never return or log
                        # cookie names, values, credentials, or session paths.
                        cookies = await context.cookies(url)
                        required = "auth_token" if platform == "twitter" else "sessionid"
                        authenticated = any(c.get("name") == required and bool(c.get("value")) for c in cookies)
                        if authenticated and state.get(f"{platform}_auth"):
                            await _save_state(context, platform)
                            return {"platform": platform, "ok": True, "state": "ready", "outcome": "ok", "message": "已完成手动登录并保存本平台独立会话。", "checked_at": now()}
                    await pause(1.5)
        raise SocialError("login_cancelled", "登录窗口已关闭，未更新会话。")
    except TimeoutError:
        raise SocialError("login_timeout", "登录等待已超过 10 分钟，窗口已关闭；可重新打开继续。") from None
    except SocialError:
        raise
    except Exception as exc:
        raise SocialError("login_failed", "登录窗口未能完成验证；原有会话未被覆盖，请检查浏览器运行时与网络。", **safe_error(exc, "login")) from None
