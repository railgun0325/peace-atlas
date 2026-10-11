"""Authorized local Firefox platform session transfer; never log secrets.

Run from analysis-worker after ending this workbench's X login/collection jobs:
  python -m app.workbench.social.firefox_import --profile ABSOLUTE_PROFILE --confirm-authorized
WORKBENCH_SOCIAL_DIR / WORKBENCH_DATA_DIR determine the existing V2 destination.
The selected Firefox database is read-only; no passwords or other domains are read.
Only authenticated platform home verification permits an atomic replacement.
The CLI retains its X-only interface; registered-profile reuse supports Instagram.
"""
from __future__ import annotations

import argparse
import asyncio
import configparser
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from uuid import uuid4

from .common import SocialError, inspect_page, navigate, pause, transitional_dom_error
from .session import browser_session, state_file

HOSTS = (".x.com", "x.com")
NAMES = ("auth_token", "ct0", "twid")
REQUIRED = frozenset({"auth_token", "ct0"})
PLATFORMS = {
    "twitter": {"label": "X", "hosts": HOSTS, "names": NAMES, "required": REQUIRED,
                "authentication": "auth_token", "home": "https://x.com/home", "verification": "x_home_authenticated"},
    "instagram": {"label": "Instagram", "hosts": (".instagram.com", "instagram.com", "www.instagram.com"),
                  "names": ("sessionid", "csrftoken", "ds_user_id"), "required": frozenset({"sessionid", "csrftoken"}),
                  "authentication": "sessionid", "home": "https://www.instagram.com/", "verification": "instagram_home_authenticated"},
}
MAX_EPOCH_SECONDS = 4102444800  # 2100-01-01; rejects wrong-unit or corrupt epochs.


class SessionImportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _platform_rules(platform: str) -> dict:
    if not isinstance(platform, str) or platform not in PLATFORMS:
        raise SessionImportError("invalid_platform", "只支持明确选择 X 或 Instagram 会话。")
    return PLATFORMS[platform]


def _profiles_ini() -> Path:
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Mozilla/Firefox"
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/Firefox"
    else:
        base = Path.home() / ".mozilla/firefox"
    return base / "profiles.ini"


def _registered_profiles() -> dict[str, tuple[dict, Path]]:
    """Only profiles.ini and existence metadata; never open a cookie database."""
    ini = _profiles_ini()
    if not ini.is_file():
        return {}
    try:
        if ini.stat().st_size > 256 * 1024:
            raise SessionImportError("profiles_unavailable", "Firefox 配置索引异常，未读取会话。")
        config = configparser.ConfigParser(interpolation=None)
        with ini.open("r", encoding="utf-8-sig") as source:
            config.read_file(source)
        defaults = {str((ini.parent / config[section]["Default"]).resolve())
                    for section in config.sections() if section.startswith("Install") and config[section].get("Default")}
        profiles = {}
        for section in config.sections():
            if not section.startswith("Profile"):
                continue
            fields = config[section]
            raw = fields.get("Path", "")
            if not raw or fields.get("IsRelative", "1") not in {"0", "1"}:
                continue
            path = Path(raw)
            if fields.get("IsRelative", "1") == "1":
                path = ini.parent / path
            elif not path.is_absolute():
                continue
            path = path.resolve()
            identity = "firefox-" + hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:24]
            default = fields.get("Default") == "1" or str(path) in defaults
            available = path.is_dir() and (path / "cookies.sqlite").is_file() and not (path / "cookies.sqlite").is_symlink()
            row = {"id": identity, "name": fields.get("Name", section)[:160], "default": default, "available": available}
            if identity in profiles:
                profiles[identity][0]["default"] |= default
            else:
                profiles[identity] = (row, path)
        return profiles
    except (OSError, ValueError, configparser.Error):
        raise SessionImportError("profiles_unavailable", "无法读取 Firefox 配置索引，未读取会话。") from None


def list_firefox_profiles() -> dict:
    return {"items": [entry[0] for entry in _registered_profiles().values()]}


def resolve_firefox_profile(profile_id: str) -> Path:
    if not isinstance(profile_id, str) or profile_id not in (registered := _registered_profiles()):
        raise SessionImportError("unknown_profile", "Firefox 配置选择无效，请重新选择已注册的配置。")
    row, path = registered[profile_id]
    if not row["available"]:
        raise SessionImportError("profile_unavailable", "所选 Firefox 配置当前不可用，未读取会话。")
    return path


async def import_registered_firefox_x_session(profile_id: str, *, before_save=None) -> dict:
    """Called only after an explicit authenticated API login job selection."""
    return await import_registered_firefox_session(profile_id, before_save=before_save)


async def import_registered_firefox_session(profile_id: str, platform: str = "twitter", *, before_save=None) -> dict:
    """Reuse only an authorized registered profile for the selected platform."""
    _platform_rules(platform)
    profile = resolve_firefox_profile(profile_id)
    return await import_firefox_session(profile, platform, authorized=True, before_save=before_save)


def _profile_path(value: str | Path) -> Path:
    try:
        profile = Path(value).expanduser()
        if not profile.is_absolute() or not profile.is_dir():
            raise SessionImportError("profile_required", "必须明确指定一个 Firefox 配置目录的绝对路径，不能指定 Profiles 总目录。")
        profile = profile.resolve(strict=True)
        database = profile / "cookies.sqlite"
        if not database.is_file() or database.is_symlink():
            raise SessionImportError("profile_required", "指定目录没有独立的 cookies.sqlite；不会自动寻找或猜测其他配置。")
        return profile
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, SessionImportError):
            raise
        raise SessionImportError("profile_required", "无法读取明确指定的 Firefox 配置目录。") from None


def _read_cookies(profile: Path, platform: str = "twitter") -> list[dict]:
    rules = _platform_rules(platform)
    hosts, names, label = rules["hosts"], rules["names"], rules["label"]
    connection = None
    try:
        # mode=ro reads Firefox's committed WAL snapshot rather than ignoring it
        # with immutable=1. There are no UPDATEs, checkpoints or database copies.
        connection = sqlite3.connect((profile / "cookies.sqlite").as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        # Firefox schema 15 -> 16 multiplies expiry by 1000; schema 17 retains ms.
        # https://searchfox.org/firefox-main/source/netwerk/cookie/CookiePersistentStorage.cpp
        # SameSite values 0/1/2/256: None/Lax/Strict/Unset. Unset retains browser default.
        # https://searchfox.org/firefox-main/source/netwerk/cookie/nsICookie.idl
        if not isinstance(version, int) or not 9 <= version <= 17:
            raise SessionImportError("unsupported_schema", "Firefox Cookie 数据库版本尚未验证，已停止导入。")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(moz_cookies)")}
        required_columns = {"name", "value", "host", "path", "expiry", "isSecure", "isHttpOnly", "originAttributes", "sameSite"}
        if not required_columns.issubset(columns):
            raise SessionImportError("unsupported_schema", "Firefox Cookie 数据库缺少可验证的必要字段。")
        partition = "isPartitionedAttributeSet" if "isPartitionedAttributeSet" in columns else "0"
        if version >= 13 and partition == "0":
            raise SessionImportError("unsupported_schema", "Firefox 分区标记字段缺失，已停止导入。")
        host_placeholders = ",".join("?" for _ in hosts)
        rows = connection.execute(
            "SELECT name,value,host,path,expiry,isSecure,isHttpOnly,originAttributes,sameSite,"
            + partition + f" AS partitioned FROM moz_cookies WHERE host IN ({host_placeholders}) AND name IN (?,?,?) LIMIT 7",
            hosts + names,
        ).fetchall()
    except sqlite3.Error:
        raise SessionImportError("database_unavailable", "无法只读访问所选 Firefox Cookie 数据库；请稍后重试，未修改工作台会话。") from None
    finally:
        if connection is not None:
            connection.close()
    if not rows:
        raise SessionImportError("missing_session", f"指定配置中没有所需的 {label} 会话；不会读取其他域名或配置。")
    if any(row["originAttributes"] != "" or row["partitioned"] != 0 for row in rows):
        raise SessionImportError("ambiguous_container", "检测到容器或分区会话，无法确定对应身份；仅支持明确的默认容器会话。")
    if len(rows) != len({row["name"] for row in rows}):
        raise SessionImportError("ambiguous_session", f"存在重复名称或不同作用域的 {label} 会话，无法安全选择。")
    if not rules["required"].issubset({row["name"] for row in rows}):
        raise SessionImportError("missing_session", f"所选配置缺少完整的 {label} 登录会话，未更新工作台。")
    current = time.time()
    cookies = []
    for row in rows:
        if (row["host"] not in hosts or row["name"] not in names or row["path"] != "/"
                or row["isSecure"] != 1 or row["isHttpOnly"] not in (0, 1)
                or (row["name"] == rules["authentication"] and row["isHttpOnly"] != 1)):
            raise SessionImportError("unsafe_cookie", f"{label} 会话的域、路径或安全属性不符合导入要求。")
        value = row["value"]
        if not isinstance(value, str) or not value or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise SessionImportError("invalid_cookie", f"{label} 会话字段无效；未更新工作台。")
        expiry = row["expiry"]
        if not isinstance(expiry, int) or expiry <= 0:
            raise SessionImportError("invalid_expiry", f"无法验证 {label} 会话的到期时间，已停止导入。")
        expires = expiry / 1000 if version >= 16 else expiry
        if not 946684800 <= expires <= MAX_EPOCH_SECONDS:
            raise SessionImportError("invalid_expiry", f"{label} 会话到期时间与数据库版本不符，已停止导入。")
        if expires <= current:
            raise SessionImportError("expired_session", f"指定 Firefox 配置中的 {label} 会话已过期，未更新工作台。")
        cookie = {"name": row["name"], "value": value, "domain": row["host"], "path": "/",
                  "expires": expires, "secure": True, "httpOnly": bool(row["isHttpOnly"])}
        if row["sameSite"] in (0, 1, 2):
            cookie["sameSite"] = {0: "None", 1: "Lax", 2: "Strict"}[row["sameSite"]]
        elif row["sameSite"] != 256:
            raise SessionImportError("unsafe_cookie", f"{label} 会话的 SameSite 属性无法安全转换。")
        cookies.append(cookie)
    return cookies


async def _verify_cookie_session(cookies: list[dict], platform: str = "twitter") -> None:
    """One public home navigation in a fresh context; never call a login flow."""
    rules = _platform_rules(platform)
    label = rules["label"]
    try:
        async with asyncio.timeout(45):
            async with browser_session(platform, visible=False, require_session=False) as (context, page):
                await context.add_cookies(cookies)
                state = await navigate(page, platform, rules["home"])
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
        raise SessionImportError("verification_failed", f"未能在 {label} 页面确认已登录状态，未更新工作台；没有发起新的登录。")
    except SessionImportError:
        raise
    except SocialError as exc:
        code = exc.code if exc.code in {"login_required", "rate_limited", "challenge", "busy", "runtime_unavailable"} else "verification_failed"
        raise SessionImportError(code, f"现有会话未通过 {label} 页面验证，或平台要求额外验证；未更新工作台，也未发起新的登录。") from None
    except Exception:
        raise SessionImportError("verification_failed", f"无法完成现有 {label} 会话验证；未更新工作台，也未发起新的登录。") from None


def _write_state(cookies: list[dict], platform: str = "twitter") -> bool:
    _platform_rules(platform)
    target = state_file(platform)
    temporary = None
    backup = None
    backup_complete = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise SessionImportError("unsafe_destination", "工作台会话目标不是独立的普通文件，已停止写入。")
        descriptor, name = tempfile.mkstemp(prefix=".firefox-import-", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"cookies": cookies, "origins": []}, output, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        if target.exists():
            backup = target.with_name("state.before-firefox-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid4().hex[:8] + ".json")
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as saved, target.open("rb") as source:
                shutil.copyfileobj(source, saved)
                saved.flush()
                os.fsync(saved.fileno())
            backup_complete = True
        os.replace(temporary, target)
        return backup_complete
    except SessionImportError:
        raise
    except Exception:
        raise SessionImportError("save_failed", "已验证会话，但备份或保存失败；原工作台会话未被替换。") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if backup is not None and not backup_complete:
            backup.unlink(missing_ok=True)


async def import_firefox_x_session(profile_dir: str | Path, *, authorized: bool = False, check_only: bool = False, before_save=None) -> dict:
    """Backward-compatible X-only API used by the existing CLI and job method."""
    return await import_firefox_session(profile_dir, authorized=authorized, check_only=check_only, before_save=before_save)


async def import_firefox_session(profile_dir: str | Path, platform: str = "twitter", *, authorized: bool = False,
                                 check_only: bool = False, before_save=None) -> dict:
    """Require direct user authorization and one explicit profile; return no secrets."""
    rules = _platform_rules(platform)
    if authorized is not True:
        raise SessionImportError("authorization_required", f"导入需要用户明确授权读取指定 Firefox 配置中的 {rules['label']} 会话。")
    profile = _profile_path(profile_dir)
    if state_file(platform).resolve().is_relative_to(profile):
        raise SessionImportError("unsafe_destination", "工作台会话目录不得位于 Firefox 配置内部。")
    cookies = await asyncio.to_thread(_read_cookies, profile, platform)
    await _verify_cookie_session(cookies, platform)
    # Verification may wait on the platform; never write a now-expired cookie.
    if any(cookie["expires"] <= time.time() for cookie in cookies):
        raise SessionImportError("expired_session", "会话在验证期间已到期，未更新工作台。")
    if not check_only and before_save is not None:
        before_save()
    backup_created = False if check_only else _write_state(cookies, platform)
    return {"ok": True, "platform": platform, "verified": True, "verification": rules["verification"],
            "saved": not check_only, "cookies_count": len(cookies), "backup_created": backup_created}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="仅经本人明确授权导入指定 Firefox 默认容器的 X 会话。请先结束工作台的 X 登录/采集任务。")
    parser.add_argument("--profile", required=True, help="单一 Firefox 配置目录的绝对路径；不会自动发现配置")
    parser.add_argument("--confirm-authorized", action="store_true", help="确认已获本人授权读取这个配置的 X 会话")
    parser.add_argument("--check-only", action="store_true", help="验证会话但不保存；仍只访问 X 首页，不发起登录")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(import_firefox_x_session(args.profile, authorized=args.confirm_authorized, check_only=args.check_only))
    except SessionImportError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": str(exc)}, ensure_ascii=True))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "code": "cancelled", "message": "已取消；未发起新的登录。"}, ensure_ascii=True))
        return 130
    except Exception:
        print(json.dumps({"ok": False, "code": "import_failed", "message": "导入未完成；请检查本地运行环境。"}, ensure_ascii=True))
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
