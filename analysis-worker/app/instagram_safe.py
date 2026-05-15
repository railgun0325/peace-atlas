from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator

logger = logging.getLogger("instagram_safe")

PROJECT_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = PROJECT_DIR / "data" / "ig_browser_profile"
STATE_FILE = PROFILE_DIR / "state.json"
READY_FILE = PROFILE_DIR / ".ready"
SAFE_STATE_FILE = PROJECT_DIR / "data" / "instagram_safe_state.json"


@dataclass
class InstagramRateConfig:
    min_account_delay_seconds: int = 120
    max_requests_per_hour: int = 20
    max_requests_per_day: int = 80
    failure_cooldown_seconds: int = 1800


DEFAULT_RATE_CONFIG = InstagramRateConfig()


def _now() -> datetime:
    return datetime.now(UTC)


def _load_safe_state() -> dict[str, Any]:
    if not SAFE_STATE_FILE.exists():
        return {
            "requests": [],
            "last_account_at": None,
            "cooldown_until": None,
            "stop_reason": None,
            "last_status": "unknown",
        }
    try:
        return json.loads(SAFE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "requests": [],
            "last_account_at": None,
            "cooldown_until": None,
            "stop_reason": "state_file_corrupted",
            "last_status": "unknown",
        }


def _save_safe_state(state: dict[str, Any]) -> None:
    SAFE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAFE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _sanitize_handle(handle: str) -> str:
    return handle.strip().lstrip("@")


def evaluate_login_state(profile_url: str, html: str) -> str:
    url = (profile_url or "").lower()
    text = (html or "").lower()
    if "challenge" in url or "checkpoint" in url or "challenge_required" in text:
        return "challenge_or_rate_limited"
    if "/accounts/login" in url or "log in" in text or "login" in text[:300]:
        return "session_expired"
    if "sorry, this page isn't available" in text or "user not found" in text:
        return "account_not_found_or_private"
    if "this account is private" in text:
        return "account_not_found_or_private"
    return "ready"


def assess_login_readiness() -> dict[str, Any]:
    if not READY_FILE.exists() or not STATE_FILE.exists():
        return {
            "ok": False,
            "status": "no_login_state",
            "message": "No saved Instagram login state found.",
            "state_file": str(STATE_FILE),
            "profile_dir": str(PROFILE_DIR),
        }
    return {
        "ok": True,
        "status": "state_present",
        "message": "Saved Instagram login state is present.",
        "state_file": str(STATE_FILE),
        "profile_dir": str(PROFILE_DIR),
        "updated_at": datetime.fromtimestamp(STATE_FILE.stat().st_mtime, UTC).isoformat(),
    }


def clear_login_session() -> dict[str, Any]:
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
    SAFE_STATE_FILE.unlink(missing_ok=True)
    return {
        "ok": True,
        "status": "cleared",
        "message": "Instagram login state was cleared.",
        "state_file": str(STATE_FILE),
        "profile_dir": str(PROFILE_DIR),
    }


def _check_rate_limits(config: InstagramRateConfig, state: dict[str, Any]) -> tuple[bool, str]:
    now = _now()
    cooldown_until = _parse_ts(state.get("cooldown_until"))
    if cooldown_until and cooldown_until > now:
        return False, f"cooldown_active_until:{cooldown_until.isoformat()}"

    last_account_at = _parse_ts(state.get("last_account_at"))
    if last_account_at:
        delta = (now - last_account_at).total_seconds()
        if delta < config.min_account_delay_seconds:
            return False, f"account_delay_not_elapsed:{int(config.min_account_delay_seconds - delta)}s"

    requests = []
    for ts in state.get("requests", []):
        parsed = _parse_ts(ts)
        if parsed:
            requests.append(parsed)
    one_hour_ago = now - timedelta(hours=1)
    one_day_ago = now - timedelta(days=1)
    hourly = [r for r in requests if r >= one_hour_ago]
    daily = [r for r in requests if r >= one_day_ago]
    if len(hourly) >= config.max_requests_per_hour:
        return False, "hourly_limit_reached"
    if len(daily) >= config.max_requests_per_day:
        return False, "daily_limit_reached"
    return True, "ok"


def _record_request(state: dict[str, Any]) -> None:
    now = _now()
    reqs = state.get("requests", [])
    reqs.append(now.isoformat())
    cutoff = now - timedelta(days=2)
    compact = []
    for ts in reqs:
        parsed = _parse_ts(ts)
        if parsed and parsed >= cutoff:
            compact.append(parsed.isoformat())
    state["requests"] = compact
    state["last_account_at"] = now.isoformat()


async def collect_profile_streaming(
    handle: str,
    max_posts: int = 10,
    rate_config: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    username = _sanitize_handle(handle)
    config = InstagramRateConfig(**(rate_config or {}))
    state = _load_safe_state()

    allowed, reason = _check_rate_limits(config, state)
    if not allowed:
        yield {
            "type": "ig_error",
            "platform": "instagram",
            "handle": handle,
            "source_method": "playwright_safe",
            "diagnostic": {"status": "rate_guard_blocked", "reason": reason},
            "message": f"Instagram safe guard blocked this run: {reason}",
        }
        return

    readiness = assess_login_readiness()
    if not readiness["ok"]:
        state["last_status"] = readiness["status"]
        state["stop_reason"] = readiness["status"]
        _save_safe_state(state)
        yield {
            "type": "ig_error",
            "platform": "instagram",
            "handle": handle,
            "source_method": "playwright_safe",
            "diagnostic": readiness,
            "message": readiness["message"],
        }
        return

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        yield {
            "type": "ig_error",
            "platform": "instagram",
            "handle": handle,
            "source_method": "playwright_safe",
            "diagnostic": {"status": "playwright_missing"},
            "message": "Playwright is not installed.",
        }
        return

    _record_request(state)
    _save_safe_state(state)

    profile_url = f"https://www.instagram.com/{username}/"
    yield {
        "type": "ig_progress",
        "platform": "instagram",
        "handle": handle,
        "source_method": "playwright_safe",
        "diagnostic": {"status": "starting", "rate_config": asdict(config)},
        "message": f"Opening {profile_url}",
    }

    pw = None
    browser = None
    context = None
    page = None
    yielded = 0
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            storage_state=str(STATE_FILE),
            viewport={"width": 390, "height": 844},
        )
        page = await context.new_page()
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1.2, 2.4))
        html = await page.content()
        status = evaluate_login_state(page.url, html)

        if status != "ready":
            state["last_status"] = status
            state["stop_reason"] = status
            if status in {"challenge_or_rate_limited", "session_expired"}:
                state["cooldown_until"] = (_now() + timedelta(seconds=config.failure_cooldown_seconds)).isoformat()
            _save_safe_state(state)
            yield {
                "type": "ig_error",
                "platform": "instagram",
                "handle": handle,
                "source_method": "playwright_safe",
                "diagnostic": {"status": status, "url": page.url},
                "message": f"Instagram stopped: {status}",
            }
            return

        links = await page.evaluate(
            """() => {
                const out = [];
                const nodes = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                for (const a of nodes) {
                    const href = a.getAttribute('href') || '';
                    const m = href.match(/\\/(p|reel)\\/([^/?#&]+)/);
                    if (!m) continue;
                    const shortcode = m[2];
                    const url = `https://www.instagram.com/${m[1]}/${shortcode}/`;
                    out.push({ shortcode, url });
                }
                return out;
            }"""
        )
        seen: set[str] = set()
        for entry in links:
            sc = entry.get("shortcode", "")
            if not sc or sc in seen:
                continue
            seen.add(sc)
            yielded += 1
            yield {
                "type": "ig_post",
                "platform": "instagram",
                "handle": handle,
                "url": entry.get("url", ""),
                "raw_text": "",
                "caption": "",
                "published_at": None,
                "taken_at": None,
                "source_method": "playwright_safe",
                "diagnostic": {"status": "ok", "shortcode": sc},
            }
            if yielded >= max_posts:
                break
            await asyncio.sleep(random.uniform(0.8, 1.5))

        state["last_status"] = "ready"
        state["stop_reason"] = None
        _save_safe_state(state)
        yield {
            "type": "ig_progress",
            "platform": "instagram",
            "handle": handle,
            "source_method": "playwright_safe",
            "diagnostic": {"status": "done", "count": yielded},
            "message": f"Collected {yielded} posts.",
        }
    except Exception as exc:
        state["last_status"] = "collector_error"
        state["stop_reason"] = "collector_error"
        state["cooldown_until"] = (_now() + timedelta(seconds=config.failure_cooldown_seconds)).isoformat()
        _save_safe_state(state)
        yield {
            "type": "ig_error",
            "platform": "instagram",
            "handle": handle,
            "source_method": "playwright_safe",
            "diagnostic": {"status": "collector_error", "error": str(exc)[:200]},
            "message": f"Instagram collector failed: {exc}",
        }
    finally:
        if page:
            await page.close()
        if context:
            await context.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


async def prepare_login_session(force_reset: bool = False, timeout_seconds: int = 600) -> dict[str, Any]:
    if force_reset:
        clear_login_session()
    if os.environ.get("DOCKER_ENV") or not os.environ.get("DISPLAY", "") and os.name != "nt":
        return {
            "ok": False,
            "status": "interactive_login_unavailable",
            "message": "Interactive browser login is unavailable in this environment.",
        }
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"ok": False, "status": "playwright_missing", "message": "Playwright is not installed."}

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = None
    browser = None
    context = None
    page = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=False)
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 420, "height": 880},
        }
        if STATE_FILE.exists():
            context_kwargs["storage_state"] = str(STATE_FILE)
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=30000)
        deadline = _now() + timedelta(seconds=max(30, timeout_seconds))
        last_url = page.url
        while _now() < deadline:
            if page.is_closed():
                break
            last_url = page.url
            cookies = await context.cookies("https://www.instagram.com")
            if any(cookie.get("name") == "sessionid" and cookie.get("value") for cookie in cookies):
                await context.storage_state(path=str(STATE_FILE))
                READY_FILE.touch()
                return {
                    "ok": True,
                    "status": "login_saved",
                    "message": "Instagram login state was saved.",
                    "state_file": str(STATE_FILE),
                    "profile_dir": str(PROFILE_DIR),
                }
            await asyncio.sleep(2)
        return {
            "ok": False,
            "status": "login_not_completed",
            "message": "Instagram login was not completed before the login window closed or timed out.",
            "last_url": last_url,
        }
    finally:
        if page:
            await page.close()
        if context:
            await context.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
