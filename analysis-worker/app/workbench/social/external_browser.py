"""Open fixed official pages through the OS default browser, without session access."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


URLS = {"twitter": "https://x.com/home", "instagram": "https://www.instagram.com/"}


class BrowserOpenError(ValueError):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code = code
        self.status = status


def _launcher() -> tuple[str, str | None] | None:
    """Inspect only the OS URL-opening mechanism, never browser/profile state."""
    if sys.platform == "win32":
        return ("windows", None) if callable(getattr(os, "startfile", None)) else None
    if sys.platform == "darwin":
        opener = Path("/usr/bin/open")
        return ("posix", str(opener)) if opener.is_file() and os.access(opener, os.X_OK) else None
    if sys.platform.startswith("linux"):
        opener = shutil.which("xdg-open")
        return ("posix", str(Path(opener).resolve())) if opener else None
    return None


def capabilities() -> dict:
    available = _launcher() is not None
    return {"available": available, "name": "系统默认浏览器", "mode": "system_default",
            "detail": "由操作系统选择默认浏览器；打开页面不会确认登录或同步工作台会话。" if available
            else "未找到系统的网页打开方式，请检查默认浏览器设置。"}


def open_platform(platform: str) -> dict:
    if platform not in URLS:
        raise BrowserOpenError("invalid_platform", "仅支持打开 X 或 Instagram 的官方页面。", 422)
    launcher = _launcher()
    if launcher is None:
        raise BrowserOpenError("browser_unavailable", "系统的默认浏览器打开方式不可用，请检查系统设置；没有打开其他浏览器。")
    url = URLS[platform]
    try:
        if launcher[0] == "windows":
            # ShellExecute uses the user's configured URL handler. Never read or
            # concatenate a registry command, executable argument or profile path.
            os.startfile(url)
        else:
            process = subprocess.Popen([launcher[1], url], shell=False, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       close_fds=True, start_new_session=True)
            try:
                code = process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                # Some desktop launchers remain alive with the browser. The
                # request was dispatched; do not kill the user's browser.
                code = None
            if code not in (None, 0):
                raise BrowserOpenError("browser_open_failed", "系统未能打开默认浏览器，请检查默认应用设置后重试。")
    except BrowserOpenError:
        raise
    except Exception:
        # OS exceptions may include local paths; never expose/log their raw text.
        raise BrowserOpenError("browser_open_failed", "打开系统默认浏览器失败，请检查默认应用设置后重试。") from None
    name = "X" if platform == "twitter" else "Instagram"
    return {"opened": True, "platform": platform, "mode": "system_default", "login_verified": False,
            "session_imported": False,
            "message": f"已请求系统默认浏览器打开 {name} 官方页面；此操作不会确认登录或自动同步工作台会话。"}
