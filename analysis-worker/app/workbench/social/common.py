"""Shared validation and browser evidence boundaries; no persistence or AI."""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..limits import RESEARCH_RECORDS_MAX, RESEARCH_SCROLLS_MAX

JST = timezone(timedelta(hours=9), "Asia/Tokyo")
PLATFORMS = ("twitter", "instagram")
RESERVED = {"home", "search", "explore", "i", "intent", "login", "signup", "accounts", "p", "reel", "reels", "stories", "direct", "about", "privacy", "settings", "following", "followers", "web", "api"}
log = logging.getLogger(__name__)


class SocialError(ValueError):
    def __init__(self, outcome: str, message: str, **diagnostic):
        super().__init__(message)
        self.outcome = outcome
        self.code = outcome
        self.message = message
        self.detail = message
        self.diagnostic = diagnostic


def safe_error(exc: Exception, stage: str) -> dict[str, Any]:
    """Record actionable categories, never full exceptions or browser state."""
    network = re.search(r"net::ERR_[A-Z_]+", str(exc))
    result = {"error_type": type(exc).__name__, "stage": stage}
    if network:
        result["network_code"] = network.group(0)
    log.warning("Social operation failed stage=%s error_type=%s network_code=%s",
                stage, result["error_type"], result.get("network_code", "none"))
    return result


def normalize_query_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"憲法\s*(?:第\s*)?[9九]\s*条", "憲法九条", text)
    return re.sub(r"(?<![0-9一二三四五六七八九十百千])(?:第\s*)?[9九]\s*条", "九条", text)


def platform_name(value: Any) -> str:
    if value not in PLATFORMS:
        raise SocialError("invalid_params", "平台只能是 twitter 或 instagram。")
    return str(value)


def normalize_handle(platform: str, value: Any) -> str:
    text = str(value or "").strip()
    if "://" in text:
        parsed = urlsplit(text)
        hosts = {"x.com", "twitter.com", "www.x.com", "www.twitter.com"} if platform == "twitter" else {"instagram.com", "www.instagram.com"}
        if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password:
            raise SocialError("invalid_params", "账号地址必须属于所选平台的 HTTPS 主页。")
        text = parsed.path.strip("/")
    text = text.lstrip("@")
    pattern = r"[A-Za-z0-9_]{1,15}" if platform == "twitter" else r"[A-Za-z0-9_.]{1,30}"
    if not re.fullmatch(pattern, text) or text.lower() in RESERVED:
        raise SocialError("invalid_params", "账号名称无效，请填写公开账号用户名或主页地址。")
    return text.lower()


def account(platform: str, handle: str, name: str = "") -> dict[str, Any]:
    handle = normalize_handle(platform, handle)
    host = "x.com" if platform == "twitter" else "www.instagram.com"
    return {"account_key": f"{platform}:{handle}", "name": name.strip() or handle,
            "handle": handle, "platform": platform, "url": f"https://{host}/{handle}/",
            "organization": "", "tags": [], "status": "candidate"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp(value: Any) -> str | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def count(value: Any) -> int | None:
    """Missing UI counts stay unknown, rather than being invented as zero."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if 0 <= value < 1e12 else None
    match = re.search(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*([kKmM万億]?)", str(value))
    if not match:
        return None
    amount = float(match[1].replace(",", ""))
    amount *= {"k": 1000, "m": 1000000, "万": 10000, "億": 100000000}.get(match[2].lower(), 1)
    return int(amount) if amount < 1e12 else None


@dataclass(frozen=True)
class Params:
    source: str
    mode: str
    keyword: str
    handle: str
    start_date: date
    end_date: date
    max_records: int
    max_accounts: int
    max_scrolls: int
    include_following: bool
    max_following: int

    @classmethod
    def parse(cls, values: dict[str, Any]) -> "Params":
        platform = platform_name(values.get("source"))
        mode = str(values.get("mode") or "keyword")
        if mode not in {"keyword", "account", "hashtag"}:
            raise SocialError("invalid_params", "采集方式只能是 keyword、account 或 hashtag。")
        keyword = unicodedata.normalize("NFKC", str(values.get("keyword") or "")).strip()
        if mode != "account" and (not keyword or len(keyword) > 300):
            raise SocialError("invalid_params", "请填写 1 至 300 字的关键词。")
        if mode == "keyword" and (re.search(r"(?:^|\s)[A-Za-z_][A-Za-z0-9_]*:", keyword)
                                  or re.search(r"(?:^|\s)(?:OR|AND|NOT)(?:\s|$)", keyword)
                                  or re.search(r'(?:^|\s)[+-]\S|["()]', keyword)):
            raise SocialError("invalid_params", "这里只支持普通关键词，不支持 from:、since:、lang:、OR 等检索运算符；请使用账号与日期选项。")
        if mode == "hashtag":
            keyword = keyword.lstrip("#")
            if not re.fullmatch(r"\w{1,100}", keyword, re.UNICODE):
                raise SocialError("invalid_params", "标签只能包含文字、数字及下划线。")
        handle = normalize_handle(platform, values.get("handle")) if mode == "account" else ""
        try:
            start, end = date.fromisoformat(str(values["start_date"])), date.fromisoformat(str(values["end_date"]))
            if end < start or (end - start).days > 3660:
                raise ValueError()
        except (ValueError, KeyError):
            raise SocialError("invalid_params", "请提供有效且不超过十年的开始与结束日期。") from None
        limits = {}
        # Simple collection has its own API limits. This reader is also used by
        # the research engine, whose explicit viewport budget may be larger.
        for field, default, low, high in (("max_records", 20, 1, RESEARCH_RECORDS_MAX), ("max_accounts", 3, 1, 10), ("max_scrolls", 5, 1, RESEARCH_SCROLLS_MAX), ("max_following", 10, 0, 50)):
            value = values.get(field, default)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise SocialError("invalid_params", f"{field} 必须为 {low} 至 {high} 的整数。")
            limits[field] = value
        following = values.get("include_following", False)
        if not isinstance(following, bool):
            raise SocialError("invalid_params", "include_following 必须为布尔值。")
        return cls(platform, mode, keyword, handle, start, end, **limits, include_following=following)

    def contains(self, value: str | None) -> bool:
        if not value:
            return False
        day = datetime.fromisoformat(value).astimezone(JST).date()
        return self.start_date <= day <= self.end_date


async def emit(callback, **event) -> None:
    if callback is not None:
        returned = callback(event)
        if inspect.isawaitable(returned):
            await returned


async def pause(seconds: float = 0.8) -> None:
    await asyncio.sleep(seconds)


PAGE_STATE_JS = """() => { // social_page_state
 const body = document.body;
 const text = (body?.innerText || '').slice(0, 12000);
 const notices = [...document.querySelectorAll('[role="alert"],[data-testid="error-detail"]')].map(el => el.innerText || '').join(' ').slice(0, 4000);
 const content = document.querySelector('article[data-testid="tweet"],article time,a[href*="/p/"],a[href*="/reel/"]');
 return {url: location.href, text, notice: notices || (content ? '' : text),
   loading: !!document.querySelector('[role="progressbar"],[aria-busy="true"]'),
   login_form: [...document.querySelectorAll('input[name="username"],input[name="password"],input[autocomplete="username"],form[action*="login"]')].some(el => el.getClientRects().length > 0),
   twitter_auth: !!document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"],a[data-testid="AppTabBar_Profile_Link"]'),
   instagram_auth: !!document.querySelector('a[href^="/direct/inbox"],a[href="/accounts/edit/"],svg[aria-label="New post"],svg[aria-label="新しい投稿"]')};
}"""


def login_url(platform: str, value: str) -> bool:
    try:
        parsed = urlsplit(str(value or ""))
        if platform == "twitter" and parsed.hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            return parsed.path.rstrip("/") in {"/login", "/i/flow/login"} or (
                parsed.path.rstrip("/") == "/i/jf/onboarding/web" and "login" in parse_qs(parsed.query).get("mode", []))
        return platform == "instagram" and parsed.hostname in {"instagram.com", "www.instagram.com"} and parsed.path.rstrip("/") == "/accounts/login"
    except ValueError:
        return False


def page_outcome(platform: str, state: dict[str, Any], status: int | None = None) -> str:
    parsed = urlsplit(str(state.get("url") or ""))
    host, path = (parsed.hostname or "").lower(), parsed.path.lower()
    allowed = {"x.com", "twitter.com", "www.x.com", "www.twitter.com"} if platform == "twitter" else {"www.instagram.com", "instagram.com"}
    text = str(state.get("notice", state.get("text")) or "").lower()
    if status == 429 or any(word in text for word in ("rate limit exceeded", "try again later", "しばらくしてからもう一度", "しばらくしてから再度", "操作を繰り返す前に", "我们已临时限制你的登录")):
        return "rate_limited"
    if any(part in path for part in ("/challenge", "/checkpoint", "/account/access")) or any(word in text for word in ("verify you are human", "confirm you are human", "enter the code", "認証コードを入力", "captcha")):
        return "challenge"
    if host not in allowed:
        return "unexpected_redirect"
    if status == 401 or state.get("login_form") or login_url(platform, state.get("url", "")):
        return "login_required"
    if status in {403, 503}:
        return "access_denied"
    if status and status >= 400:
        return "http_error"
    if any(word in text for word in ("this account is private", "these posts are protected", "このアカウントは非公開", "ポストは非公開")):
        return "private_account"
    if any(word in text for word in ("account suspended", "account doesn’t exist", "account doesn't exist", "アカウントは凍結", "アカウントは存在しません")):
        return "account_unavailable"
    return "ok"


MESSAGES = {
    "login_required": "平台要求重新登录，请先在登录窗口完成手动登录。",
    "rate_limited": "平台当前限流；已停止请求，请稍后再试。",
    "challenge": "平台要求验证；已停止自动采集，请在登录窗口手动完成。",
    "access_denied": "平台拒绝访问，未将空页面当作成功采集。",
    "unexpected_redirect": "平台页面跳转到预期域名之外，已停止采集。",
    "http_error": "平台页面返回错误状态，未取得有效内容。",
    "private_account": "该账号内容不是当前会话可公开读取的内容，已跳过。",
    "account_unavailable": "账号不存在或已被平台限制。",
    "parse_error": "页面未出现预期帖子结构，无法确认是无结果还是页面结构变化。",
    "runtime_unavailable": "社媒浏览器运行时未就绪，请安装应用提供的 Playwright / Chromium 运行时。",
    "busy": "此平台已有登录或采集任务运行，请等待结束或取消该任务。",
    "network_error": "平台页面加载失败或网络超时。",
    "timeout": "采集达到本次时间上限，已保留截至此时取得的结果。",
}


async def inspect_page(page, platform: str, status: int | None = None) -> dict[str, Any]:
    if login_url(platform, page.url):
        raise SocialError("login_required", MESSAGES["login_required"], observed_redirect_kind="login")
    state = await page.evaluate(PAGE_STATE_JS)
    outcome = page_outcome(platform, state, status)
    if outcome != "ok":
        raise SocialError(outcome, MESSAGES[outcome])
    return state


def transitional_dom_error(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in ("Execution context was destroyed", "Cannot find context with specified id"))


def _http_failure(status: int | None, detail: dict) -> SocialError:
    if status == 401:
        outcome = "login_required"
    elif status == 429:
        outcome = "rate_limited"
    elif status == 403:
        outcome = "access_denied"
    else:
        outcome = "http_error"
    if status is not None:
        detail["http_status"] = status
        message = f"页面导航收到 HTTP {status} 响应。{MESSAGES[outcome]}"
    else:
        message = "浏览器报告 HTTP 响应失败，但未取得可核验的 HTTP 状态码；响应可能来自平台或代理链路，尚不能确定具体原因。"
    log.warning("Social HTTP navigation failed http_status=%s network_code=%s", status, detail.get("network_code", "none"))
    return SocialError(outcome, message, **detail)


async def navigate(page, platform: str, url: str) -> dict[str, Any]:
    http_errors: list[int] = []
    observed_login = False
    # A completed new main document cannot contain cards left over from the
    # previous SPA search. Keep only this in-memory receipt, never cookies or
    # response content. A failed/new navigation invalidates the old receipt.
    page._workbench_search_document = None
    page._workbench_original_attempts = set()
    page._workbench_original_clicked = set()

    def capture_response(response):
        # Navigation errors sometimes prevent goto() from returning a response.
        # Retain only main-document numeric status; never headers/body/URLs.
        try:
            nonlocal observed_login
            request = response.request
            if request.is_navigation_request() and request.frame == page.main_frame:
                observed_login = observed_login or login_url(platform, getattr(response, "url", ""))
                if response.status >= 400:
                    http_errors.append(int(response.status))
        except Exception:
            pass

    page.on("response", capture_response)
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        if observed_login or login_url(platform, page.url):
            raise SocialError("login_required", MESSAGES["login_required"], stage="navigate", observed_redirect_kind="login")
        if response is not None and response.status >= 400:
            raise _http_failure(response.status, {"stage": "navigate"})
        request = getattr(response, "request", None)
        if (platform == "twitter" and response is not None and response.status == 200
                and request is not None and request.is_navigation_request() and request.frame == page.main_frame):
            requested, final = urlsplit(url), urlsplit(page.url)
            query = parse_qs(requested.query).get("q", [""])[0]
            if (requested.hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
                    and final.hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
                    and requested.path.rstrip("/") == final.path.rstrip("/") == "/search"
                    and parse_qs(requested.query).get("f") == parse_qs(final.query).get("f") == ["live"]
                    and query and parse_qs(final.query).get("q") == [query]):
                page._workbench_search_document = query
        await pause()
        # Client-side redirects can replace the JS execution context after the
        # document event. Poll the same navigation briefly; do not request again.
        for attempt in range(5):
            try:
                return await inspect_page(page, platform, response.status if response else None)
            except SocialError:
                raise
            except Exception as exc:
                if not transitional_dom_error(exc) or attempt == 4:
                    raise
                await pause(0.5)
    except SocialError as exc:
        exc.diagnostic.setdefault("stage", "navigate")
        raise
    except Exception as exc:
        detail = safe_error(exc, "navigate")
        if observed_login or login_url(platform, page.url):
            if http_errors:
                detail["http_status"] = http_errors[-1]
            raise SocialError("login_required", MESSAGES["login_required"], observed_redirect_kind="login", **detail) from None
        if http_errors or detail.get("network_code") == "net::ERR_HTTP_RESPONSE_CODE_FAILURE":
            raise _http_failure(http_errors[-1] if http_errors else None, detail) from None
        raise SocialError("network_error", MESSAGES["network_error"], **detail) from None
    finally:
        page.remove_listener("response", capture_response)


def search_state_argument(page, query):
    committed = getattr(page, "_workbench_search_document", None)
    if committed is not None and normalize_query_text(committed) == normalize_query_text(query):
        return {"query": query, "document_committed": True}
    return query
