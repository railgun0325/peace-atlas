from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
from urllib.parse import quote
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator, Callable

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


def _login_helper_html(message: str = "") -> str:
    escaped = (message or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Instagram login helper</title>
      <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.5; padding: 24px; max-width: 720px; }}
        a.button {{ display: inline-block; margin-top: 12px; padding: 10px 14px; background: #155eef; color: #fff; text-decoration: none; border-radius: 6px; }}
        .hint {{ color: #555; }}
        code {{ background: #f2f4f7; padding: 2px 4px; border-radius: 4px; }}
      </style>
    </head>
    <body>
      <h2>Instagram 登录助手</h2>
      <p>自动打开 Instagram 登录页失败，但浏览器窗口会保持打开。</p>
      <p><a class="button" href="https://www.instagram.com/accounts/login/">打开 Instagram 登录页</a></p>
      <p class="hint">如果按钮没有反应，请在地址栏手动输入 <code>https://www.instagram.com/accounts/login/</code>。</p>
      <p class="hint">登录完成后不要关闭这个窗口，系统会自动检测并保存登录状态。</p>
      {f"<p class='hint'>导航错误：{escaped}</p>" if escaped else ""}
    </body>
    </html>
    """


def _login_helper_data_url(message: str = "") -> str:
    html = _login_helper_html(message)
    return "data:text/html;charset=utf-8," + quote(html)


def _candidate_firefox_paths() -> list[str]:
    candidates: list[str] = []
    env_path = os.environ.get("IG_FIREFOX_PATH")
    if env_path:
        candidates.append(env_path)
    resolved = shutil.which("firefox")
    if resolved:
        candidates.append(resolved)
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Mozilla Firefox\firefox.exe",
                r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            ]
        )
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def _find_native_firefox() -> str | None:
    for candidate in _candidate_firefox_paths():
        if Path(candidate).exists():
            return candidate
    return None


def _firefox_same_site(value: Any) -> str:
    # Firefox stores SameSite as numeric constants; Playwright wants text.
    try:
        code = int(value)
    except Exception:
        code = 0
    return {1: "Lax", 2: "Strict", 5: "None"}.get(code, "None")


def _playwright_cookie_expires(value: Any) -> int:
    try:
        expires = int(value)
    except Exception:
        return -1
    if expires <= 0:
        return -1
    # Firefox profiles seen in the wild may expose millisecond timestamps,
    # while Playwright storage_state requires Unix seconds.
    if expires > 10_000_000_000:
        expires = expires // 1000
    return expires


def _copy_firefox_cookie_db(cookie_db: Path, target_dir: Path) -> Path:
    copied = target_dir / "cookies.sqlite"
    shutil.copy2(cookie_db, copied)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(cookie_db) + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, target_dir / f"cookies.sqlite{suffix}")
    return copied


def _read_firefox_instagram_cookies(profile_dir: Path) -> list[dict[str, Any]]:
    cookie_db = profile_dir / "cookies.sqlite"
    if not cookie_db.exists():
        return []

    def read_from(db_path: Path) -> list[dict[str, Any]]:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(moz_cookies)").fetchall()}
            same_site_column = "sameSite" if "sameSite" in columns else "0 AS sameSite"
            rows = conn.execute(
                f"""
                SELECT host, name, value, path, expiry, isSecure, isHttpOnly, {same_site_column}
                FROM moz_cookies
                WHERE host LIKE '%instagram.com'
                """
            ).fetchall()
        cookies: list[dict[str, Any]] = []
        for host, name, value, path, expiry, secure, http_only, same_site in rows:
            if not name or value is None:
                continue
            cookies.append(
                {
                    "name": str(name),
                    "value": str(value),
                    "domain": str(host),
                    "path": str(path or "/"),
                    "expires": _playwright_cookie_expires(expiry),
                    "httpOnly": bool(http_only),
                    "secure": bool(secure),
                    "sameSite": _firefox_same_site(same_site),
                }
            )
        return cookies

    try:
        return read_from(cookie_db)
    except Exception:
        try:
            with tempfile.TemporaryDirectory(prefix="ig_firefox_cookies_") as tmp:
                copied = _copy_firefox_cookie_db(cookie_db, Path(tmp))
                return read_from(copied)
        except Exception as exc:
            logger.debug("Could not read Firefox Instagram cookies: %s", exc)
            return []


def _save_playwright_storage_state_from_cookies(cookies: list[dict[str, Any]]) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"cookies": cookies, "origins": []}
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    READY_FILE.touch()


def _focus_window_for_pid(pid: int) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnds: list[int] = []

        enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def enum_proc(hwnd: int, _lparam: int) -> bool:
            window_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid and user32.IsWindowVisible(hwnd):
                hwnds.append(hwnd)
            return True

        user32.EnumWindows(enum_proc_type(enum_proc), 0)
        if not hwnds:
            return False
        hwnd = hwnds[0]
        user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_TOPMOST, keep size/pos
        focused = bool(user32.SetForegroundWindow(hwnd))
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_NOTOPMOST
        return focused
    except Exception as exc:
        logger.debug("Could not focus Firefox login window: %s", exc)
        return False


def _is_process_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        if os.name == "nt":
            output = subprocess.check_output(
                ["powershell.exe", "-NoProfile", "-Command", f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ '1' }}"],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "1" in output
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _find_firefox_pids_for_profile(profile_dir: Path) -> list[int]:
    if os.name != "nt":
        return []
    try:
        token = profile_dir.name.replace("'", "''")
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -like 'firefox*' -and $_.CommandLine -like '*"
            + token
            + "*' } | ForEach-Object { $_.ProcessId }"
        )
        output = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command", script],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [int(value) for value in re.findall(r"\d+", output or "")]
    except Exception as exc:
        logger.debug("Could not find existing Firefox profile process: %s", exc)
        return []


def _find_firefox_pid_for_profile(profile_dir: Path) -> int | None:
    pids = _find_firefox_pids_for_profile(profile_dir)
    return pids[0] if pids else None


def _stop_firefox_profile_processes(profile_dir: Path) -> list[int]:
    pids = _find_firefox_pids_for_profile(profile_dir)
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            logger.debug("Could not stop stale Firefox profile process %s: %s", pid, exc)
    return pids


def _focus_firefox_profile_window(profile_dir: Path) -> tuple[int | None, bool]:
    pids = _find_firefox_pids_for_profile(profile_dir)
    for pid in pids:
        if _focus_window_for_pid(pid):
            return pid, True
    return (pids[0], False) if pids else (None, False)


def validate_login_helper_status(status: dict[str, Any]) -> dict[str, Any]:
    if status.get("status") != "login_window_open" or status.get("browser") != "system_firefox":
        return status
    pid = status.get("firefox_pid")
    profile = Path(str(status.get("firefox_profile") or ""))
    try:
        pid_int = int(pid) if pid is not None else None
    except Exception:
        pid_int = None
    if pid_int and _is_process_running(pid_int):
        return {**status, "window_present": True}
    profile_pid, _focused = _focus_firefox_profile_window(profile) if profile else (None, False)
    if profile_pid:
        return {
            **status,
            "ok": True,
            "status": "login_window_open",
            "message": "Firefox 登录窗口已打开；如果不在最前，请看任务栏里的 Mozilla Firefox。",
            "firefox_pid": profile_pid,
            "window_present": True,
        }
    return {
        **status,
        "ok": False,
        "status": "login_window_closed",
        "message": "登录窗口已经关闭或没有成功显示。请重新点击“登录/续登”，系统会重新打开 Firefox。",
        "window_present": False,
    }


async def _prepare_native_firefox_login_session(
    timeout_seconds: int,
    status_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    firefox_path = _find_native_firefox()
    if not firefox_path:
        return {
            "ok": False,
            "status": "firefox_not_found",
            "message": "未找到本机 Firefox，无法用 Firefox 打开 Instagram 登录页。",
        }

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    firefox_profile = PROFILE_DIR / "native_firefox_profile"
    firefox_profile.mkdir(parents=True, exist_ok=True)
    login_url = "https://www.instagram.com/accounts/login/"

    existing_pid, existing_focused = _focus_firefox_profile_window(firefox_profile)
    if existing_pid and not existing_focused:
        stale_pids = _stop_firefox_profile_processes(firefox_profile)
        if stale_pids and status_callback:
            status_callback(
                {
                    "ok": True,
                    "status": "closing_stale_login_window",
                    "message": "正在关闭未能置前的旧 Firefox 登录窗口，然后重新打开。",
                    "browser": "system_firefox",
                    "stale_firefox_pids": stale_pids,
                    "firefox_profile": str(firefox_profile),
                }
            )
        await asyncio.sleep(1.0)
        existing_pid = None

    process = None
    if existing_pid:
        if status_callback:
            status_callback(
                {
                    "ok": True,
                    "status": "login_window_open",
                    "message": "检测到已有 Firefox 登录窗口，并已尝试置前。",
                    "browser": "system_firefox",
                    "firefox_pid": existing_pid,
                    "firefox_profile": str(firefox_profile),
                    "reused_existing_window": True,
                    "window_focused": existing_focused,
                }
            )
    else:
        try:
            process = subprocess.Popen(
                [
                    firefox_path,
                    "-no-remote",
                    "-new-instance",
                    "-profile",
                    str(firefox_profile),
                    "-new-window",
                    login_url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "firefox_launch_failed",
                "message": f"Firefox 启动失败：{exc}",
                "browser": "system_firefox",
                "firefox_path": firefox_path,
            }

        if status_callback:
            status_callback(
                {
                    "ok": True,
                    "status": "login_window_open",
                    "message": "Firefox 登录进程已启动；如果没有看到窗口，请查看任务栏或用 Alt+Tab 切到 Mozilla Firefox。",
                    "browser": "system_firefox",
                    "firefox_pid": process.pid,
                    "firefox_profile": str(firefox_profile),
                }
        )

    await asyncio.sleep(0.8)
    firefox_pid = _find_firefox_pid_for_profile(firefox_profile) or existing_pid or (process.pid if process else 0)
    focused = False
    for _ in range(16):
        firefox_pid = _find_firefox_pid_for_profile(firefox_profile) or firefox_pid
        if not firefox_pid:
            break
        focused = _focus_window_for_pid(firefox_pid)
        if focused:
            break
        await asyncio.sleep(0.5)
    if status_callback:
        status_callback(
            {
                "ok": True,
                "status": "login_window_open",
                "message": (
                    "Firefox 登录窗口已打开，并已尝试置前；如果仍不明显，请看任务栏里的 Mozilla Firefox。"
                    if focused
                    else "Firefox 登录进程已启动；如果没有看到窗口，请查看任务栏或用 Alt+Tab 切到 Mozilla Firefox。"
                ),
                "browser": "system_firefox",
                "firefox_pid": firefox_pid,
                "firefox_profile": str(firefox_profile),
                "window_focused": focused,
                "reused_existing_window": bool(existing_pid),
            }
        )

    deadline = _now() + timedelta(seconds=max(30, timeout_seconds))
    launcher_exited = False
    while _now() < deadline:
        cookies = _read_firefox_instagram_cookies(firefox_profile)
        if any(cookie.get("name") == "sessionid" and cookie.get("value") for cookie in cookies):
            _save_playwright_storage_state_from_cookies(cookies)
            return {
                "ok": True,
                "status": "login_saved",
                "message": "已从 Firefox 保存 Instagram 登录状态。",
                "state_file": str(STATE_FILE),
                "profile_dir": str(PROFILE_DIR),
                "browser": "system_firefox",
            }
        if process and process.poll() is not None:
            launcher_exited = True
            firefox_pid = _find_firefox_pid_for_profile(firefox_profile)
            if not firefox_pid:
                return {
                    "ok": False,
                    "status": "login_window_closed",
                    "message": "Firefox 登录窗口已经关闭或没有成功显示。请重新点击“登录/续登”。",
                    "last_url": login_url,
                    "browser": "system_firefox",
                    "firefox_profile": str(firefox_profile),
                    "firefox_pid": firefox_pid,
                    "launcher_exited": launcher_exited,
                }
        await asyncio.sleep(2)

    return {
        "ok": False,
        "status": "login_not_completed",
        "message": "Firefox 登录窗口已打开，但尚未检测到 Instagram 登录状态。请在 Firefox 中完成登录，遇到验证码或风险提示就暂停并告知我。",
        "last_url": login_url,
        "browser": "system_firefox",
        "firefox_profile": str(firefox_profile),
        "launcher_exited": launcher_exited,
    }


def _account_delay_wait_seconds(reason: str) -> int | None:
    match = re.fullmatch(r"account_delay_not_elapsed:(\d+)s", reason or "")
    if not match:
        return None
    return max(1, int(match.group(1)))


def _login_cooldown_payload() -> dict[str, Any] | None:
    state = _load_safe_state()
    cooldown_until = _parse_ts(state.get("login_cooldown_until") or state.get("cooldown_until"))
    if not cooldown_until or cooldown_until <= _now():
        return None
    wait_seconds = max(1, int((cooldown_until - _now()).total_seconds()))
    return {
        "ok": False,
        "status": "login_cooldown_active",
        "message": f"Instagram 已触发限流/风险冷却，暂停登录和采集；约 {wait_seconds // 60 + 1} 分钟后再尝试。",
        "cooldown_until": cooldown_until.isoformat(),
        "wait_seconds": wait_seconds,
        "state_file": str(STATE_FILE),
        "profile_dir": str(PROFILE_DIR),
    }


def mark_login_rate_limited(reason: str = "instagram_login_429", seconds: int | None = None) -> dict[str, Any]:
    wait_seconds = max(300, int(seconds or os.environ.get("IG_LOGIN_COOLDOWN_SECONDS", "3600")))
    cooldown_until = (_now() + timedelta(seconds=wait_seconds)).isoformat()
    state = _load_safe_state()
    state["cooldown_until"] = cooldown_until
    state["login_cooldown_until"] = cooldown_until
    state["stop_reason"] = reason
    state["last_status"] = reason
    _save_safe_state(state)
    return {
        "ok": False,
        "status": "login_cooldown_active",
        "message": f"Instagram 返回 429/限流，已暂停登录和采集 {wait_seconds // 60} 分钟。",
        "cooldown_until": cooldown_until,
        "wait_seconds": wait_seconds,
    }


def evaluate_login_state(profile_url: str, html: str) -> str:
    url = (profile_url or "").lower()
    text = (html or "").lower()
    verification_tokens = (
        "challenge",
        "checkpoint",
        "challenge_required",
        "captcha",
        "security code",
        "verification code",
        "confirm it's you",
        "help us confirm",
    )
    if any(token in url or token in text for token in verification_tokens):
        return "challenge_or_rate_limited"
    if "/accounts/login" in url or "log in" in text or "login" in text[:300]:
        return "session_expired"
    if "sorry, this page isn't available" in text or "user not found" in text:
        return "account_not_found_or_private"
    if "this account is private" in text:
        return "account_not_found_or_private"
    return "ready"


def assess_login_readiness() -> dict[str, Any]:
    cooldown = _login_cooldown_payload()
    if cooldown:
        return cooldown
    if not READY_FILE.exists() or not STATE_FILE.exists():
        return {
            "ok": False,
            "status": "no_login_state",
            "message": "未找到已保存的 Instagram 登录状态。请先在前端点击“登录/续登”并完成一次人工登录。",
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
    wait_seconds = _account_delay_wait_seconds(reason)
    if not allowed and wait_seconds is not None:
        jitter = random.uniform(3.0, 9.0)
        total_wait = wait_seconds + jitter
        yield {
            "type": "ig_progress",
            "platform": "instagram",
            "handle": handle,
            "source_method": "playwright_safe",
            "diagnostic": {
                "status": "rate_guard_waiting",
                "reason": reason,
                "wait_seconds": round(total_wait, 1),
            },
            "message": f"Instagram 安全等待 {round(total_wait)} 秒后继续，避免连续访问同一账号。",
        }
        await asyncio.sleep(total_wait)
        state = _load_safe_state()
        allowed, reason = _check_rate_limits(config, state)

    if not allowed:
        yield {
            "type": "ig_error",
            "platform": "instagram",
            "handle": handle,
            "source_method": "playwright_safe",
            "diagnostic": {"status": "rate_guard_blocked", "reason": reason},
            "message": f"Instagram 安全保护已暂停本轮采集：{reason}",
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

    # ── Firefox cookie 同步：登录用 Firefox，采集也要用 Firefox 的 cookies ──
    # 这样 Instagram 看到的始终是同一个"设备"，不会触发跨浏览器安全验证。
    native_firefox = _find_native_firefox()
    if native_firefox:
        firefox_profile = PROFILE_DIR / "native_firefox_profile"
        if firefox_profile.exists():
            try:
                fresh_cookies = _read_firefox_instagram_cookies(firefox_profile)
                if any(c.get("name") == "sessionid" and c.get("value") for c in fresh_cookies):
                    _save_playwright_storage_state_from_cookies(fresh_cookies)
            except Exception:
                pass  # Firefox cookie 读取失败不影响继续（使用已有的 state.json）

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
        "message": f"正在打开 Instagram 账号页：{profile_url}",
    }

    pw = None
    browser = None
    context = None
    page = None
    yielded = 0
    try:
        pw = await async_playwright().start()
        use_headless = os.environ.get("INSTAGRAM_PLAYWRIGHT_HEADLESS", "true").strip().lower() not in {"false", "0", "no", "off"}
        browser = await pw.chromium.launch(
            headless=use_headless,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            storage_state=str(STATE_FILE),
            viewport={"width": 390, "height": 844},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 "
                "Mobile/15E148 Safari/604.1"
            ),
        )
        page = await context.new_page()
        # 注入反检测脚本：隐藏 webdriver 标记、伪造 chrome 对象、伪造 plugins/permissions
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP', 'ja', 'en-US', 'en']});
            window.chrome = {runtime: {}, loadTimes: function() {}, csi: function() {}};
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                Promise.resolve({state: Notification.permission}) :
                originalQuery(parameters)
            );
        """)
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
            status_messages = {
                "challenge_or_rate_limited": "Instagram 要求验证码、安全确认或触发风控；已停止自动采集，请人工确认后再继续。",
                "session_expired": "Instagram 登录态已失效；请重新登录后再采集。",
                "account_not_found_or_private": "Instagram 账号不存在或为私密账号，无法公开采集。",
            }
            yield {
                "type": "ig_error",
                "platform": "instagram",
                "handle": handle,
                "source_method": "playwright_safe",
                "diagnostic": {"status": status, "url": page.url},
                "message": status_messages.get(status, f"Instagram 已停止：{status}"),
            }
            return

        links_by_shortcode: dict[str, dict[str, str]] = {}
        scroll_passes = max(1, min(4, int(os.environ.get("INSTAGRAM_SCROLL_PASSES", "2") or "2")))
        for pass_index in range(scroll_passes):
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
            for entry in links:
                sc = entry.get("shortcode", "")
                if sc and sc not in links_by_shortcode:
                    links_by_shortcode[sc] = entry
            if len(links_by_shortcode) >= max_posts or pass_index >= scroll_passes - 1:
                break
            yield {
                "type": "ig_progress",
                "platform": "instagram",
                "handle": handle,
                "source_method": "playwright_safe",
                "diagnostic": {
                    "status": "scrolling",
                    "pass": pass_index + 1,
                    "found_links": len(links_by_shortcode),
                },
                "message": f"Instagram 页面已发现 {len(links_by_shortcode)} 个帖子链接，低频滚动加载更多。",
            }
            await page.evaluate("window.scrollBy(0, Math.max(document.body.scrollHeight, 900))")
            await asyncio.sleep(random.uniform(2.8, 5.5))

        seen: set[str] = set()
        for entry in links_by_shortcode.values():
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


async def prepare_login_session(
    force_reset: bool = False,
    timeout_seconds: int = 600,
    status_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if force_reset:
        clear_login_session()
    if os.environ.get("DOCKER_ENV") or not os.environ.get("DISPLAY", "") and os.name != "nt":
        return {
            "ok": False,
            "status": "interactive_login_unavailable",
            "message": "Interactive browser login is unavailable in this environment.",
        }

    cooldown = _login_cooldown_payload()
    if cooldown:
        return cooldown

    login_browser = os.environ.get("IG_LOGIN_BROWSER", "playwright").strip().lower()
    # 强制使用 Playwright 登录以保持 cookie 与采集浏览器一致。
    # Firefox 路径会导致跨浏览器 cookie 不匹配，触发 Instagram 验证。
    if login_browser == "firefox" and _find_native_firefox():
        return await _prepare_native_firefox_login_session(timeout_seconds, status_callback=status_callback)

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
        if status_callback:
            status_callback(
                {
                    "ok": True,
                    "status": "login_window_open",
                    "message": "登录浏览器已启动，请在窗口中完成人工登录。",
                    "browser": "playwright_chromium",
                }
            )
        navigation_error = None
        try:
            await page.goto("https://www.instagram.com/accounts/login/", wait_until="commit", timeout=30000)
        except Exception as exc:
            navigation_error = str(exc).splitlines()[0]
            try:
                await page.close()
            except Exception:
                pass
            page = await context.new_page()
            try:
                await page.goto(_login_helper_data_url(navigation_error), wait_until="domcontentloaded", timeout=10000)
            except Exception as fallback_exc:
                navigation_error = f"{navigation_error}; helper page: {str(fallback_exc).splitlines()[0]}"
                try:
                    await page.set_content(_login_helper_html(navigation_error), wait_until="domcontentloaded")
                except Exception as prompt_exc:
                    navigation_error = f"{navigation_error}; prompt: {str(prompt_exc).splitlines()[0]}"
                    return {
                        "ok": False,
                        "status": "login_page_unavailable",
                        "message": "无法打开 Instagram 登录页，也无法显示登录说明页。",
                        "navigation_error": navigation_error,
                    }
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
            "navigation_error": navigation_error,
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
