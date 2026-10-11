"""Explicit, loopback-only extension pairing; verify before saving minimal sessions."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time
from uuid import uuid4

from .common import SocialError, inspect_page, navigate, pause
from .session import browser_session, state_file

EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}")
COOKIE_RULES = {
    "twitter": ({"x.com", ".x.com"}, {"auth_token", "ct0", "twid"}, {"auth_token", "ct0"}, "auth_token"),
    "instagram": ({"instagram.com", ".instagram.com", "www.instagram.com"},
                  {"sessionid", "csrftoken", "ds_user_id"}, {"sessionid", "csrftoken"}, "sessionid"),
}
COOKIE_FIELDS = {"name", "value", "domain", "path", "secure", "httpOnly", "expires", "sameSite"}
VERIFY_TIMEOUT_SECONDS = 22
log = logging.getLogger(__name__)


class BridgeError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def validate_cookies(platform: str, cookies: object) -> list[dict]:
    if not isinstance(platform, str) or platform not in COOKIE_RULES:
        raise BridgeError("invalid_platform", "平台参数不受支持。")
    domains, names, required, authentication = COOKIE_RULES[platform]
    if not isinstance(cookies, list) or not 2 <= len(cookies) <= 3:
        raise BridgeError("invalid_session", "只接受所选平台的必要会话字段。")
    result, seen = [], set()
    for raw in cookies:
        if not isinstance(raw, dict) or set(raw) != COOKIE_FIELDS:
            raise BridgeError("invalid_session", "会话字段格式不兼容，未保存。")
        name, value = raw["name"], raw["value"]
        expiry = raw["expires"]
        if (not isinstance(name, str) or name not in names or name in seen or not isinstance(value, str)
                or not 1 <= len(value) <= 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise BridgeError("ambiguous_session", "会话字段缺失、重复或范围不明确，未保存。")
        if not isinstance(raw["domain"], str) or raw["domain"] not in domains or raw["path"] != "/" or raw["secure"] is not True:
            raise BridgeError("invalid_scope", "只接受目标平台根域的安全会话，未保存。")
        if not isinstance(raw["httpOnly"], bool) or (name == authentication and not raw["httpOnly"]):
            raise BridgeError("invalid_session", "登录会话属性不兼容，未保存。")
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or (isinstance(expiry, float) and not math.isfinite(expiry)):
            raise BridgeError("invalid_session", "会话有效期无法确认，未保存。")
        if expiry != -1 and not time.time() < expiry <= 4102444800:
            raise BridgeError("expired_session", "浏览器会话已经到期或有效期不兼容，未保存。")
        if not isinstance(raw["sameSite"], str) or raw["sameSite"] not in {"None", "Lax", "Strict"}:
            raise BridgeError("invalid_session", "会话站点属性不兼容，未保存。")
        seen.add(name)
        result.append(dict(raw))
    if not required <= seen:
        raise BridgeError("missing_session", "浏览器尚无完整的已登录会话，请先在平台页面登录。")
    return result


async def verify_cookie_session(platform: str, cookies: list[dict]) -> None:
    cookies = validate_cookies(platform, cookies)
    try:
        async with asyncio.timeout(VERIFY_TIMEOUT_SECONDS):
            async with browser_session(platform, visible=False, require_session=False) as (context, page):
                await context.add_cookies(cookies)
                url = "https://x.com/home" if platform == "twitter" else "https://www.instagram.com/"
                state = await navigate(page, platform, url)
                for attempt in range(20):
                    if state.get(f"{platform}_auth"):
                        return
                    if attempt < 19:
                        await pause(0.5)
                        state = await inspect_page(page, platform)
        raise BridgeError("verification_failed", "现有浏览器会话未通过平台页面验证，原工作台会话保持原样。", 422)
    except BridgeError:
        raise
    except SocialError as exc:
        raise BridgeError(exc.code, "平台会话验证未通过，请查看登录或平台验证状态；未替换原会话。", 422) from None
    except Exception:
        raise BridgeError("verification_failed", "未能完成平台会话验证，原工作台会话保持原样。", 422) from None


def _cleanup_file(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Do not let a cleanup exception expose a path or replace the
            # controlled verification/save error; files were created mode 0600.
            log.warning("Could not remove a temporary browser bridge file")


def save_cookie_session(platform: str, cookies: list[dict]) -> bool:
    cookies = validate_cookies(platform, cookies)
    target = state_file(platform)
    temporary = backup = None
    backup_complete = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise BridgeError("unsafe_destination", "会话目标不是普通文件，未保存。")
        descriptor, filename = tempfile.mkstemp(prefix=".browser-bridge-", suffix=".tmp", dir=target.parent)
        temporary = Path(filename)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"cookies": cookies, "origins": []}, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        if target.exists():
            backup = target.with_name("state.before-browser-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid4().hex[:8] + ".json")
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output, target.open("rb") as source:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            backup_complete = True
        os.replace(temporary, target)
        return backup_complete
    except BridgeError:
        raise
    except Exception:
        raise BridgeError("save_failed", "会话已验证，但保存失败，原工作台会话保持原样。", 500) from None
    finally:
        _cleanup_file(temporary)
        if backup is not None and not backup_complete:
            _cleanup_file(backup)


class BrowserBridge:
    def __init__(self):
        self.codes: dict[str, dict] = {}
        self.bindings: dict[str, dict] = {}
        self._mutex = threading.RLock()

    def _clean(self):
        current = time.time()
        self.codes = {key: value for key, value in self.codes.items() if value["expires"] > current}
        self.bindings = {key: value for key, value in self.bindings.items() if value["expires"] > current}

    def start(self, platform: str, origin: str) -> dict:
        with self._mutex:
            return self._start(platform, origin)

    def _start(self, platform: str, origin: str) -> dict:
        if not isinstance(platform, str) or platform not in COOKIE_RULES:
            raise BridgeError("invalid_platform", "平台参数不受支持。")
        match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", origin) if isinstance(origin, str) else None
        if not match or not 1 <= int(match[1]) <= 65535:
            raise BridgeError("loopback_required", "浏览器连接只支持本机 127.0.0.1 地址。")
        self._clean()
        if len(self.codes) >= 10:
            raise BridgeError("too_many_pairs", "待连接请求过多，请稍后再试。", 429)
        code = secrets.token_hex(32)
        self.codes[hashlib.sha256(code.encode()).hexdigest()] = {"platform": platform, "expires": time.time() + 300}
        return {"platform": platform, "pairing_url": origin + "/api/v2/social/bridge/connect?code=" + code + "&platform=" + platform,
                "expires_in": 300, "verified": False}

    def pair(self, code: str, platform: str, extension_origin: str) -> dict:
        with self._mutex:
            return self._pair(code, platform, extension_origin)

    def _pair(self, code: str, platform: str, extension_origin: str) -> dict:
        self._clean()
        if (not isinstance(extension_origin, str) or not EXTENSION_ORIGIN.fullmatch(extension_origin)
                or not isinstance(code, str) or not re.fullmatch(r"[0-9a-f]{64}", code)
                or not isinstance(platform, str)):
            raise BridgeError("pair_denied", "连接来源或配对码无效。", 403)
        key = hashlib.sha256(code.encode()).hexdigest()
        grant = self.codes.get(key)
        if not grant or grant["platform"] != platform:
            raise BridgeError("pair_denied", "配对码已使用、已到期或平台不匹配。", 403)
        self.codes.pop(key)
        token = secrets.token_hex(32)
        expires = time.time() + 86400
        self.bindings[hashlib.sha256(token.encode()).hexdigest()] = {"platform": platform, "origin": extension_origin,
            "expires": expires, "lock": asyncio.Lock(), "last_sync": 0}
        return {"bridge_token": token, "platform": platform, "expires_at": expires}

    def _binding(self, token: str, platform: str, extension_origin: str) -> dict:
        with self._mutex:
            return self._lookup_binding(token, platform, extension_origin)

    def _lookup_binding(self, token: str, platform: str, extension_origin: str) -> dict:
        self._clean()
        if (not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token)
                or not isinstance(extension_origin, str) or not EXTENSION_ORIGIN.fullmatch(extension_origin)
                or not isinstance(platform, str)):
            raise BridgeError("bridge_denied", "浏览器连接已失效，请重新配对。", 401)
        entry = self.bindings.get(hashlib.sha256(token.encode()).hexdigest())
        if not entry or entry["platform"] != platform or not hmac.compare_digest(entry["origin"], extension_origin or ""):
            raise BridgeError("bridge_denied", "浏览器连接已失效或平台不匹配，请重新配对。", 401)
        return entry

    async def sync(self, token: str, platform: str, extension_origin: str, raw_cookies: object) -> dict:
        entry = self._binding(token, platform, extension_origin)
        if entry["lock"].locked() or time.time() - entry["last_sync"] < 5:
            raise BridgeError("bridge_busy", "会话正在同步或刚完成同步，请稍后再试。", 409)
        cookies = validate_cookies(platform, raw_cookies)
        async with entry["lock"]:
            entry["last_sync"] = time.time()
            await verify_cookie_session(platform, cookies)
            # Repeat scope/expiry validation after the network wait.
            self._binding(token, platform, extension_origin)
            cookies = validate_cookies(platform, cookies)
            backup = save_cookie_session(platform, cookies)
            entry["last_sync"] = time.time()
        return {"platform": platform, "source": "browser_extension", "verified": True, "saved": True,
                "backup_created": backup, "cookies_count": len(cookies), "state": "ready"}
