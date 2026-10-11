"""Bounded reuse of existing sessions; never starts a login or visible browser."""
from __future__ import annotations

import asyncio

from .common import SocialError, inspect_page, navigate, pause, platform_name, transitional_dom_error
from .session import browser_session, state_file
from . import firefox_import


async def _verify_saved_session(platform: str) -> None:
    if not state_file(platform).is_file():
        raise SocialError("missing_session", "工作台尚未保存这个平台的会话。")
    async with asyncio.timeout(45):
        async with browser_session(platform, visible=False, require_session=True) as (_, page):
            url = "https://x.com/home" if platform == "twitter" else "https://www.instagram.com/"
            state = await navigate(page, platform, url)
            for attempt in range(25):
                if state.get(f"{platform}_auth"):
                    return
                if attempt == 24:
                    break
                await pause(0.5)
                try:
                    state = await inspect_page(page, platform)
                except Exception as exc:
                    if not transitional_dom_error(exc):
                        raise
    raise SocialError("verification_failed", "平台页面未显示可核验的登录状态。")


def _result(platform: str, *, verified=False, saved=False, source="none", state="requires_login", code=None, message="") -> dict:
    return {"ok": True, "method": "auto", "platform": platform, "verified": verified, "saved": saved,
            "source": source, "state": state, "requires_login": state == "requires_login", "code": code, "message": message}


def _blocked(platform: str, code: str) -> dict:
    if code in {"challenge", "rate_limited", "runtime_unavailable"}:
        state = code
    elif code in {"ambiguous_profile", "ambiguous_container", "ambiguous_session"}:
        state = "ambiguous_profile"
    else:
        state = "verification_failed"
    messages = {
        "challenge": "现有会话被平台要求额外验证；已停止检查，没有发起新的登录。",
        "rate_limited": "平台限制了本次会话检查；已停止请求，没有发起新的登录。",
        "runtime_unavailable": "工作台验证会话所需的运行时不可用，无法确认登录状态。",
        "ambiguous_profile": "Firefox 默认配置或会话身份不明确，请明确选择配置复用；没有自动切换账号。",
        "verification_failed": "未能完成现有会话验证；没有改写会话或发起新的登录，请查看诊断后重试。",
    }
    return _result(platform, state=state, code=code, message=messages[state])


async def resolve_session(platform: str, *, before_save=None) -> dict:
    platform_name(platform)
    label = firefox_import.PLATFORMS[platform]["label"]
    try:
        await _verify_saved_session(platform)
    except SocialError as exc:
        if exc.code not in {"missing_session", "login_required"}:
            return _blocked(platform, exc.code)
    except Exception:
        return _blocked(platform, "verification_failed")
    else:
        return _result(platform, verified=True, source="workbench", state="ready", message="当前工作台会话已通过平台页面验证，可以继续采集。")

    try:
        profiles = firefox_import.list_firefox_profiles()["items"]
        defaults = [profile for profile in profiles if profile["default"]]
        if len(defaults) > 1:
            return _blocked(platform, "ambiguous_profile")
        if not defaults or not defaults[0]["available"]:
            return _result(platform, code="missing_default_profile", message=f"当前工作台没有可用的 {label} 会话，也未找到可复用的默认 Firefox 配置。")
        imported = await firefox_import.import_registered_firefox_session(defaults[0]["id"], platform, before_save=before_save)
    except firefox_import.SessionImportError as exc:
        if exc.code in {"missing_session", "expired_session", "login_required", "profile_unavailable", "unknown_profile"}:
            return _result(platform, code=exc.code, message=f"未找到通过验证的可复用 {label} 会话，需要先在系统默认浏览器完成登录。")
        return _blocked(platform, exc.code)
    # Save callbacks may signal job cancellation or a database error. They must
    # propagate to the runner, rather than becoming a successful check result.
    if not imported.get("verified"):
        return _blocked(platform, "verification_failed")
    return _result(platform, verified=True, saved=bool(imported["saved"]), source="firefox", state="ready",
                   message=f"已验证默认 Firefox 配置中的 {label} 会话，并保存到当前工作台数据目录。")
