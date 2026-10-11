"""Finite public-page collection with source-level diagnostics and no DB writes."""
from __future__ import annotations

import asyncio
import math
import time
import re
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlsplit

from ..limits import RESEARCH_NEIGHBORS_MAX, RESEARCH_SCROLLS_MAX
from . import selectors
from .common import MESSAGES, Params, SocialError, account, count, emit, inspect_page, navigate, normalize_handle, normalize_query_text, now, pause, platform_name, safe_error, search_state_argument, timestamp, transitional_dom_error
from .session import browser_session

COLLECTION_TIMEOUT = 300.0
INITIAL_CONTENT_WAIT = 12.0
SCROLL_CONTENT_WAIT = 3.0
POLL_INTERVAL = 0.5
EMPTY_MARKERS = ("no results for", "no results found", "no posts yet", "hasn’t posted", "hasn't posted", "まだ投稿がありません", "投稿はまだありません", "検索結果はありません", "該当する結果はありません", "没有找到", "暂无帖子")


def _collection_budget(time_budget_seconds):
    if time_budget_seconds is None:
        return COLLECTION_TIMEOUT
    if (isinstance(time_budget_seconds, bool) or not isinstance(time_budget_seconds, (int, float))
            or not math.isfinite(time_budget_seconds) or not 0 < time_budget_seconds <= 86400):
        raise SocialError("invalid_params", "内部采集时间预算必须为大于零且不超过 86400 秒的有限数值。")
    return float(time_budget_seconds)


def _candidate_budget(params: Params) -> int:
    """Bound irrelevant candidates independently of accepted material slots."""
    if params.source == "twitter":
        return max(500, min(30000, params.max_records * 5, params.max_scrolls * 30))
    viewport_budget = 150 + max(0, params.max_scrolls - 20) * 15
    return min(params.max_records * 3, viewport_budget)


def _timeout_diagnostic(diag, items):
    diag.update(ok=False, outcome="timeout", partial=bool(items), message=MESSAGES["timeout"],
                scan_stop_reason="time_budget")


def _post_link(platform: str, value: str) -> tuple[str, str, str] | None:
    host = "x.com" if platform == "twitter" else "www.instagram.com"
    raw = str(value or "")
    parsed = urlsplit(raw if raw.startswith("https://") else "https://" + host + raw)
    allowed = {"x.com", "twitter.com", "www.x.com", "www.twitter.com"} if platform == "twitter" else {"instagram.com", "www.instagram.com"}
    if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password:
        return None
    if platform == "twitter":
        match = re.fullmatch(r"/([A-Za-z0-9_]{1,15})/status/(\d+)(?:/(?:photo|video)/\d+)?/?", parsed.path)
        if match:
            return f"https://x.com/{match[1].lower()}/status/{match[2]}", match[2], match[1].lower()
    else:
        match = re.fullmatch(r"/(?:([A-Za-z0-9_.]{1,30})/)?(p|reel)/([A-Za-z0-9_-]+)/?", parsed.path)
        if match:
            return f"https://www.instagram.com/{match[2]}/{match[3]}/", match[3], (match[1] or "").lower()
    return None


def _query_matches(params: Params, text: str) -> bool:
    if params.mode == "account":
        return True
    material = normalize_query_text(text)
    keyword = normalize_query_text(params.keyword)
    if params.mode == "hashtag":
        tags = re.findall(r"#(\w+)", material, re.UNICODE)
        return keyword in tags
    latin_word = r"A-Za-z0-9_\u00c0-\u024f"
    for term in keyword.split():
        left = f"(?<![{latin_word}])" if re.match(f"[{latin_word}]", term[0]) else ""
        right = f"(?![{latin_word}])" if re.match(f"[{latin_word}]", term[-1]) else ""
        if not re.search(left + re.escape(term) + right, material):
            return False
    return True


def _query(params: Params) -> str:
    term = "#" + params.keyword if params.mode == "hashtag" else params.keyword
    # Platform search boundaries may use another timezone. Keep a margin and
    # apply the requested Japanese calendar range to the original timestamp.
    return f"{term} since:{params.start_date - timedelta(days=1)} until:{params.end_date + timedelta(days=2)}"


def _base_diag(params: Params) -> dict[str, Any]:
    return {"platform": params.source, "mode": params.mode, "ok": False, "outcome": "pending",
            "scanned": 0, "duplicates": 0, "stalls": 0, "date_filtered": 0, "unknown_dates": 0,
            "query_mismatch": 0, "author_mismatch": 0, "reposts_filtered": 0, "invalid_records": 0, "account_limit_skipped": 0,
            "irrelevant": 0, "relevance_examples": [], "skipped_article_cards": 0, "skipped_translated_bodies": 0,
            "max_scrolls": params.max_scrolls, "scrolls_completed": 0, "candidate_scan_limit": _candidate_budget(params),
            "start_date": str(params.start_date), "end_date": str(params.end_date), "timezone": "Asia/Tokyo",
            "coverage": "本次登录会话中有限滚动取得的样本，不代表平台完整历史。"}


def _empty_state(state: dict) -> bool:
    return any(marker in str(state.get("text") or "").lower() for marker in EMPTY_MARKERS)


def _row_list(value) -> list[dict]:
    if isinstance(value, dict):
        return value.get("users", []) if value.get("valid") else []
    return value if isinstance(value, list) else []


async def _restore_original_posts(page, rows, diag):
    """Use visible per-post controls, once each; never call a hidden API."""
    attempts = getattr(page, "_workbench_original_attempts", set())
    clicked = getattr(page, "_workbench_original_clicked", set())
    for row in [row for row in _row_list(rows) if row.get("translation_visible")][:12]:
        href = row.get("status_link", "")
        if href in attempts or not re.fullmatch(r"/[A-Za-z0-9_]{1,15}/status/\d+", href):
            continue
        attempts.add(href)
        diag["original_text_toggle_attempts"] = diag.get("original_text_toggle_attempts", 0) + 1
        try:
            card = page.locator('[data-testid="primaryColumn"] article[data-testid="tweet"]').filter(has=page.locator(f'a[href="{href}"] time')).first
            controls = card.get_by_text(row["original_control"], exact=True)
            for index in range(min(await controls.count(), 5)):
                control = controls.nth(index)
                own = await control.evaluate("""el => {
                    for (let node=el; node && !node.matches('article[data-testid=tweet]'); node=node.parentElement) {
                        if (node.matches('[data-testid=quoteTweet],[data-testid=tweetText]') ||
                            (node.getAttribute('role')==='link' && node.tagName!=='A')) return false;
                    }
                    return true;
                }""")
                if own and await control.is_visible():
                    await control.click(timeout=1500)
                    clicked.add(href)
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            diag["original_text_toggle_failures"] = diag.get("original_text_toggle_failures", 0) + 1
    page._workbench_original_attempts, page._workbench_original_clicked = attempts, clicked


async def _wait_dom(page, platform, script, ready, diag, *, seconds, poll_key, argument=None, search_query=None):
    """Observe one SPA navigation; wait is finite and never reissues a request."""
    value, state = None, {}
    try:
        async with asyncio.timeout(seconds + 1):
            for attempt in range(int(seconds / POLL_INTERVAL) + 1):
                try:
                    state = await inspect_page(page, platform)
                    diag[poll_key] = diag.get(poll_key, 0) + 1
                    if state.get("loading"):
                        diag["loading_polls"] = diag.get("loading_polls", 0) + 1
                    context_ready = True
                    if search_query is not None:
                        search = await page.evaluate(selectors.TW_SEARCH_STATE, search_state_argument(page, search_query))
                        context_ready = bool(search.get("ready"))
                        diag["search_context_wait_polls"] = diag.get("search_context_wait_polls", 0) + 1
                        diag["search_context_verified"] = context_ready
                        diag["search_context_reason"] = search.get("reason", "search_results_missing")
                        diag["search_document_committed"] = bool(search.get("document_committed"))
                        if search.get("pagination_loading"):
                            diag["search_pagination_pending"] = True
                        state = {**state, "text": search.get("result_text", ""), "loading": bool(search.get("loading"))}
                    value = (await page.evaluate(script, argument) if argument is not None else await page.evaluate(script)) if context_ready else []
                    if context_ready and "social_twitter_posts" in script:
                        if any(row.get("translation_visible") for row in _row_list(value)):
                            await _restore_original_posts(page, value, diag)
                            value = await page.evaluate(script)
                        clicked = getattr(page, "_workbench_original_clicked", set())
                        for row in _row_list(value):
                            row["original_ui_restored"] = row.get("status_link") in clicked and not row.get("translation_visible")
                    if context_ready and (ready(value) or _empty_state(state)):
                        return value, state
                except SocialError as exc:
                    exc.diagnostic.setdefault("stage", "waiting_for_content")
                    raise
                except Exception as exc:
                    if not transitional_dom_error(exc):
                        raise
                    diag["redirect_context_changes"] = diag.get("redirect_context_changes", 0) + 1
                if attempt < int(seconds / POLL_INTERVAL):
                    await pause(POLL_INTERVAL)
    except TimeoutError:
        # A hung DOM read is bounded too. Re-evaluation or network navigation is
        # left to a future explicit task, not an unbounded automatic retry.
        diag["content_wait_timeout"] = True
    return value, state


async def _frames(page, platform: str, script: str, max_scrolls: int, diag: dict, key, *, limit: int = 500, skip_row=None):
    """Read each virtualized viewport before scrolling it out of the DOM."""
    seen = set()
    skipped = set()
    stagnant = 0
    search_query = diag.get("query") if platform == "twitter" and diag.get("mode") in {"keyword", "hashtag"} and "social_twitter_posts" in script else None
    def skip_identity(row):
        if skip_row and skip_row(row):
            return (row.get("status_link", ""), row.get("author_link", ""), row.get("skip_reason", ""))
        return None

    def has_new_content(raw):
        for row in _row_list(raw):
            skipped_identity = skip_identity(row)
            if skipped_identity is not None:
                if skipped_identity not in skipped:
                    return True
            elif key(row) and key(row) not in seen:
                return True
        return False

    for index in range(max_scrolls + 1):
        value, state = await _wait_dom(page, platform, script,
            has_new_content, diag,
            seconds=INITIAL_CONTENT_WAIT if index == 0 else SCROLL_CONTENT_WAIT,
            poll_key="initial_wait_polls" if index == 0 else "scroll_wait_polls", search_query=search_query)
        rows = _row_list(value)
        if index == 0 and not rows and not _empty_state(state) and ("social_twitter_posts" in script or "social_instagram_grid" in script):
            # Slow SPA hydration previously looked identical to a malformed
            # search after 12 s. Re-check explicit platform outcomes first,
            # then allow one same-public-page navigation and a second bounded
            # wait. Never repeat on login, rate limits, challenge or real empty.
            state = await inspect_page(page, platform)
            if search_query is not None:
                search = await page.evaluate(selectors.TW_SEARCH_STATE, search_state_argument(page, search_query))
                state = {**state, "text": search.get("result_text", "") if search.get("ready") else ""}
            if not _empty_state(state):
                target = str((diag.get("source_url") if search_query is not None else getattr(page, "url", "")) or "")
                parsed = urlsplit(target)
                allowed = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} if platform == "twitter" else {"instagram.com", "www.instagram.com"}
                if parsed.scheme == "https" and parsed.hostname in allowed and not parsed.username and not parsed.password:
                    diag["initial_navigation_retry"] = 1
                    await navigate(page, platform, target)
                    value, state = await _wait_dom(page, platform, script, has_new_content, diag,
                        seconds=INITIAL_CONTENT_WAIT, poll_key="retry_wait_polls", search_query=search_query)
                    rows = _row_list(value)
        if not rows and _empty_state(state):
            diag["scan_stop_reason"] = "explicit_empty"
            return
        if index == 0 and not any(key(row) or skip_identity(row) is not None for row in rows):
            if search_query is not None and not diag.get("search_context_verified"):
                raise SocialError("parse_error", "X 检索尚未确认目标查询、实时标签和已完成加载的主结果区域；有界等待与一次原检索重载后仍未就绪，未把旧时间线作为检索结果。", stage="waiting_for_search", search_context_reason=diag.get("search_context_reason"))
            raise SocialError("parse_error", "已完成有界页面等待与一次安全重载，仍未出现可核验的帖子或明确空结果；未将未加载页面标为采集成功。", stage="waiting_for_content")
        added = 0
        for row in rows:
            skipped_identity = skip_identity(row)
            if skipped_identity is not None:
                if skipped_identity not in skipped:
                    skipped.add(skipped_identity)
                    counter = "skipped_translated_bodies" if row.get("skip_reason") == "translated_body_not_restored" else "skipped_article_cards"
                    diag[counter] = diag.get(counter, 0) + 1
                    added += 1
                continue
            identity = key(row)
            if not identity:
                diag["invalid_records"] += 1
                continue
            if identity in seen:
                diag["duplicates"] += 1
                continue
            seen.add(identity)
            added += 1
            diag["scanned"] += 1
            yield row
            if len(seen) >= limit:
                diag["scan_stop_reason"] = "candidate_budget"
                return
        stagnant = stagnant + 1 if not added else 0
        if not added:
            diag["stalls"] += 1
        if stagnant >= 3:
            diag["scan_stop_reason"] = "no_new_content"
            return
        if index == max_scrolls:
            diag["scan_stop_reason"] = "scroll_budget"
            return
        await page.evaluate(selectors.SCROLL)
        diag["scrolls_completed"] = diag.get("scrolls_completed", 0) + 1


def _add_account(accounts: dict, params: Params, handle: str, name: str = "", *, following: bool = False) -> dict | None:
    try:
        entry = account(params.source, handle, name)
    except SocialError:
        return None
    key = entry["account_key"]
    # Following relations have their own bounded budget, separate from the
    # number of source accounts visited for posts.
    if key not in accounts and not following and len(accounts) >= params.max_accounts:
        return None
    return accounts.setdefault(key, entry)


def _dated(params: Params, diag: dict, raw: Any) -> str | None:
    value = timestamp(raw)
    if not value:
        diag["unknown_dates"] += 1
        return None
    if not params.contains(value):
        diag["date_filtered"] += 1
        return None
    return value


def _accepted(candidate: dict, accept, diag: dict) -> bool:
    if accept is None:
        return True
    try:
        decision = accept(candidate)
        matched = decision.get("matched", False) if isinstance(decision, dict) else bool(decision)
    except Exception:
        raise SocialError("relevance_error", "素材相关性筛选未能完成，已停止本次采集。") from None
    if matched:
        return True
    diag["irrelevant"] += 1
    if len(diag["relevance_examples"]) < 10:
        diag["relevance_examples"].append({"url": candidate["url"], "reasons": decision.get("reasons", []) if isinstance(decision, dict) else []})
    return False


async def _twitter(page, params: Params, items: list, accounts: dict, diag: dict, progress, accept):
    # A named account has a public timeline independent of the search index.
    # Do not silently fall back to search; retain the same bounded viewport,
    # primary-author, original-date, and project-relevance checks below.
    query = None if params.mode == "account" else _query(params)
    source_url = (f"https://x.com/{params.handle}" if params.mode == "account" else
                  "https://x.com/search?q=" + quote(query) + "&src=typed_query&f=live")
    collection_method = "x_browser_profile_timeline" if params.mode == "account" else "x_browser_live_search"
    diag.update(query=query, source_url=source_url, collection_method=collection_method)
    state = await navigate(page, "twitter", source_url)
    if params.mode == "account":
        _add_account(accounts, params, params.handle)
    async for row in _frames(page, "twitter", selectors.TW_POSTS, params.max_scrolls, diag,
                             lambda row: (_post_link("twitter", row.get("status_link", "")) or (None, None, None))[1],
                             limit=diag["candidate_scan_limit"],
                             skip_row=lambda row: row.get("skip_reason") in {"article_card_without_body", "translated_body_not_restored"}):
        parsed = _post_link("twitter", row.get("status_link", ""))
        url, post_id, author = parsed
        try:
            visible_author = normalize_handle("twitter", row.get("author_link", "").strip("/"))
        except SocialError:
            diag["invalid_records"] += 1
            continue
        if author != visible_author or (params.mode == "account" and author != params.handle):
            diag["author_mismatch"] += 1
            continue
        repost = bool(re.search(r"repost|retweet|リポスト|リツイート|转发|转帖|转推|轉發|轉帖|轉貼|轉推", str(row.get("social_context") or ""), re.I))
        if params.mode == "account" and repost:
            diag["reposts_filtered"] += 1
            continue
        text = str(row.get("text") or "").strip()
        if not _query_matches(params, text):
            diag["query_mismatch"] += 1
            continue
        published = _dated(params, diag, row.get("published_at"))
        if not published:
            continue
        alt = row.get("image_alt") or []
        if not text and not alt:
            diag["invalid_records"] += 1
            continue
        cited = []
        for link in row.get("cited_links", []):
            candidate = _post_link("twitter", link)
            if candidate and candidate[1] != post_id and candidate[0] not in cited:
                cited.append(candidate[0])
        candidate = {"platform": "twitter", "type": "social", "title": text[:160] or f"@{author} 的图片线索（无正文）",
            "source_name": f"X / @{author}", "content": text, "url": url, "published_at": published, "account_key": f"twitter:{author}",
            "metadata": {"content_kind": "body" if text else "image_alt", "image_alt": alt,
                "engagement": {"likes": count(row.get("likes")), "replies": count(row.get("replies")), "reposts": count(row.get("reposts"))},
                "collection_method": collection_method, "source_url": source_url, "collected_at": now(), "post_id": post_id,
                "post_kind": "repost" if repost else ("quote" if cited else "post"), "cited_urls": cited,
                "query": query, "author_verified_in_dom": True, "body_language": row.get("body_language"),
                "text_representation": "original_after_platform_toggle" if row.get("original_ui_restored") else "public_post_dom"}}
        if not _accepted(candidate, accept, diag):
            continue
        acc = _add_account(accounts, params, author, str(row.get("name") or ""))
        if not acc:
            diag["account_limit_skipped"] += 1
            continue
        items.append(candidate)
        await emit(progress, stage="collecting", platform="twitter", current=len(items), total=params.max_records, message=f"已取得 {len(items)} 条日期范围内的 X 素材。")
        if len(items) >= params.max_records:
            diag["scan_stop_reason"] = "record_budget"
            break
    if not diag["scanned"]:
        if diag["skipped_translated_bodies"]:
            raise SocialError("parse_error", "当前帖子仅显示平台译文，原文切换未能确认；未将译文当作原帖正文保存，可稍后重试。", stage="original_text_unavailable")
        if diag["skipped_article_cards"]:
            raise SocialError("parse_error", "本次有限滚动只遇到无可提取正文的 X 文章卡，已跳过；未将文章摘要或图片说明作为原帖正文，也未判定平台没有相关帖子。", stage="article_cards_only")
        state = await inspect_page(page, "twitter")
        if not any(marker in str(state.get("text") or "").lower() for marker in EMPTY_MARKERS):
            raise SocialError("parse_error", MESSAGES["parse_error"])


async def _instagram_targets(page, params: Params, accounts: dict) -> list[tuple[str, str]]:
    if params.mode == "hashtag":
        return [("https://www.instagram.com/explore/tags/" + quote(params.keyword) + "/", "")]
    if params.mode == "account":
        acc = _add_account(accounts, params, params.handle)
        return [(acc["url"], params.handle)]
    await navigate(page, "instagram", "https://www.instagram.com/web/search/topsearch/?query=" + quote(params.keyword))
    result = await page.evaluate(selectors.IG_SEARCH)
    if not result.get("valid"):
        raise SocialError("parse_error", "Instagram 用户搜索页没有返回可识别的公开账号结果。")
    targets = []
    for row in result.get("users", []):
        acc = _add_account(accounts, params, row.get("handle", ""), row.get("name", ""))
        if acc:
            targets.append((acc["url"], acc["handle"]))
        if len(targets) >= params.max_accounts:
            break
    return targets


async def _instagram(page, params: Params, items: list, accounts: dict, diagnostics: list, diag: dict, progress, accept):
    targets = await _instagram_targets(page, params, accounts)
    pending = {}
    candidate_budget = diag["candidate_scan_limit"]
    for target, profile in targets:
        state = await navigate(page, "instagram", target)
        before = diag["scanned"]
        async for row in _frames(page, "instagram", selectors.IG_GRID, params.max_scrolls, diag,
                                 lambda row: (_post_link("instagram", row.get("href", "")) or (None, None, None))[1], limit=candidate_budget):
            canonical = _post_link("instagram", row["href"])[0]
            if canonical in pending:
                diag["duplicates"] += 1
            else:
                pending[canonical] = {**row, "profile": profile}
            if len(pending) >= candidate_budget:
                diag["scan_stop_reason"] = "candidate_budget"
                break
        if before == diag["scanned"] and not any(marker in str(state.get("text") or "").lower() for marker in EMPTY_MARKERS):
            raise SocialError("parse_error", MESSAGES["parse_error"])
        if len(pending) >= candidate_budget:
            break
    for url, grid in pending.items():
        try:
            await navigate(page, "instagram", url)
            detail, _ = await _wait_dom(page, "instagram", selectors.IG_DETAIL,
                lambda row: bool(row and row.get("detail_present") and row.get("published_at") and (row.get("handle") or grid.get("profile"))),
                diag, seconds=INITIAL_CONTENT_WAIT, poll_key="detail_wait_polls", argument=url)
            detail = detail or {}
            if not detail.get("detail_present"):
                raise SocialError("parse_error", "Instagram 帖子详情未出现正文或发布时间；未将图片 alt 当作正文。")
            try:
                author = normalize_handle("instagram", detail.get("handle") or grid.get("profile"))
            except SocialError:
                raise SocialError("parse_error", "Instagram 原帖作者未能确认；这属于页面解析问题，并非输入参数错误。", field="author") from None
            if params.mode == "account" and author != params.handle:
                diag["author_mismatch"] += 1
                continue
            published = _dated(params, diag, detail.get("published_at"))
            if not published:
                continue
            caption = str(detail.get("caption") or "").strip()
            # Keyword mode discovers accounts, then reads their posts. Relevance
            # is evaluated by the workbench gate, never inferred from image alt.
            if params.mode == "hashtag" and not _query_matches(params, caption):
                diag["query_mismatch"] += 1
                continue
            alt = detail.get("image_alt") or ([grid["image_alt"]] if grid.get("image_alt") else [])
            if not caption and not alt:
                raise SocialError("parse_error", "Instagram 帖子详情没有可保存的正文或图片线索。")
            candidate = {"platform": "instagram", "type": "social", "title": caption[:160] or f"@{author} 的图片线索（无正文）",
                "source_name": f"Instagram / @{author}", "content": caption, "url": url, "published_at": published, "account_key": f"instagram:{author}",
                "metadata": {"content_kind": "body" if caption else "image_alt", "image_alt": alt,
                    "engagement": {"likes": count(detail.get("likes")), "replies": None, "reposts": None},
                    "collection_method": "instagram_browser_post_detail", "collected_at": now(),
                    "discovery_method": params.mode, "discovery_keyword": params.keyword, "collected_from": grid.get("profile") or None}}
            if not _accepted(candidate, accept, diag):
                continue
            acc = _add_account(accounts, params, author, str(detail.get("name") or ""))
            if not acc:
                diag["account_limit_skipped"] += 1
                continue
            items.append(candidate)
            await emit(progress, stage="collecting", platform="instagram", current=len(items), total=params.max_records, message=f"已读取 {len(items)} 篇日期范围内的 Instagram 帖子详情。")
            if len(items) >= params.max_records:
                diag["scan_stop_reason"] = "record_budget"
                break
        except SocialError as exc:
            diagnostics.append({"platform": "instagram", "url": url, "ok": False, "outcome": exc.code, "message": exc.message, **exc.diagnostic})
            if exc.code in {"login_required", "rate_limited", "challenge", "access_denied"}:
                raise
        except Exception as exc:
            diagnostics.append({"platform": "instagram", "url": url, "ok": False, "outcome": "network_error", "message": MESSAGES["network_error"], **safe_error(exc, "post_detail")})


async def _following(page, params: Params, accounts: dict, relations: list, diagnostics: list):
    sources = list(accounts.values())[:params.max_accounts]
    for source in sources:
        if len(relations) >= params.max_following:
            break
        platform, handle = params.source, source["handle"]
        evidence = source["url"].rstrip("/") + "/following/"
        diag = {"platform": platform, "stage": "following", "account_key": source["account_key"], "ok": False,
                "scanned": 0, "duplicates": 0, "stalls": 0, "invalid_records": 0}
        try:
            if platform == "twitter":
                await navigate(page, platform, evidence)
                script = selectors.TW_ACCOUNTS
            else:
                await navigate(page, platform, source["url"])
                link = page.locator(f'a[href="/{handle}/following/"]').first
                await link.click(timeout=5000)
                await pause()
                await inspect_page(page, platform)
                script = selectors.IG_FOLLOWING
            async for row in _frames(page, platform, script, params.max_scrolls, diag, lambda row: row.get("handle", "").lower(), limit=params.max_following):
                target = _add_account(accounts, params, row.get("handle", ""), row.get("name", ""), following=True)
                if not target or target["account_key"] == source["account_key"]:
                    continue
                relation = {"source_key": source["account_key"], "target_key": target["account_key"], "relation": "follows", "evidence_url": evidence, "observed_at": now()}
                if not any(r["source_key"] == relation["source_key"] and r["target_key"] == relation["target_key"] for r in relations):
                    relations.append(relation)
                if len(relations) >= params.max_following:
                    break
            diag.update(ok=bool(diag["scanned"]), outcome="ok" if diag["scanned"] else "no_visible_following", message="仅记录实际关注列表中可见的账号；不推断完整关系网。")
        except SocialError as exc:
            diag.update(outcome=exc.code, message=exc.message, **exc.diagnostic)
            if exc.code in {"login_required", "rate_limited", "challenge"}:
                diagnostics.append(diag)
                raise
        except Exception as exc:
            diag.update(outcome="parse_error", message="未能打开或解析关注列表；未推断关注关系。", **safe_error(exc, "following"))
        diagnostics.append(diag)


async def collect_on_page(page, params: dict[str, Any], progress=None, accept=None, *, time_budget_seconds=None) -> dict[str, Any]:
    """Reuse a single task-owned context for bounded search/profile branches.

    The caller owns browser lifetime and the overall traversal deadline. A
    branch error retains only actually read records and source diagnostics.
    """
    options = Params.parse(params)
    budget = _collection_budget(time_budget_seconds)
    items, accounts, diagnostics = [], {}, []
    diag = _base_diag(options)
    diag["time_budget_seconds"] = budget
    diagnostics.append(diag)
    try:
        async with asyncio.timeout(budget):
            if options.source == "twitter":
                await _twitter(page, options, items, accounts, diag, progress, accept)
            else:
                await _instagram(page, options, items, accounts, diagnostics, diag, progress, accept)
        errors = any(not row.get("ok") for row in diagnostics[1:])
        diag.update(ok=not errors, outcome="partial" if errors and items else ("parse_error" if errors else ("ok" if items else "no_matches")),
                    message="已读取本分支可追溯样本。" if items else "本分支没有符合条件的可读帖子，不代表账号没有相关历史。")
    except SocialError as exc:
        diag.update(ok=False, outcome=exc.code, message=exc.message, **exc.diagnostic)
    except TimeoutError:
        _timeout_diagnostic(diag, items)
    except Exception as exc:
        diag.update(ok=False, outcome="network_error", message=MESSAGES["network_error"], **safe_error(exc, "research_branch"))
    diag.update(post_count=len(items), account_count=len(accounts), relation_count=0)
    return {"items": items, "accounts": list(accounts.values()), "relations": [], "diagnostics": diagnostics}


async def read_profile(page, platform: str, handle: str) -> dict[str, Any]:
    """Capture visible profile text, never credential or hidden API content."""
    platform_name(platform)
    entry = account(platform, handle)
    await navigate(page, platform, entry["url"])
    diag = {"platform": platform, "stage": "profile", "account_key": entry["account_key"]}
    raw, _ = await _wait_dom(page, platform, selectors.TW_PROFILE if platform == "twitter" else selectors.IG_PROFILE,
                            lambda value: bool(value and value.get("verified_in_dom") and str(value.get("name") or "").strip()), diag,
                            seconds=INITIAL_CONTENT_WAIT, poll_key="profile_wait_polls", argument=entry["handle"])
    if not isinstance(raw, dict) or not raw.get("verified_in_dom") or raw.get("handle") != entry["handle"]:
        raise SocialError("profile_unavailable", "公开账号资料没有出现可确认的主页标识；未推断简介或隶属。", **diag)
    entry["name"] = str(raw.get("name") or entry["name"])[:200]
    entry["profile"] = {"handle": entry["handle"], "url": entry["url"], "bio": str(raw.get("bio") or "")[:3000],
                        "links": [row for row in (raw.get("links") or [])[:10] if isinstance(row, dict) and isinstance(row.get("url"), str)],
                        "followers_count": count(raw.get("followers_count")), "following_count": count(raw.get("following_count")),
                        "private": bool(raw.get("private")), "verified_in_dom": True, "observed_at": now(),
                        "collection_method": "visible_profile_dom"}
    return entry


async def read_neighbors(page, platform: str, handle: str, direction: str, *, limit: int = 12, max_scrolls: int = 3,
                         known_count: int | None = None, time_budget_seconds=None) -> dict[str, Any]:
    """Read one public following/follower list, with correct directed edges."""
    platform_name(platform)
    if direction not in {"following", "followers"} or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= RESEARCH_NEIGHBORS_MAX or isinstance(max_scrolls, bool) or not isinstance(max_scrolls, int) or not 1 <= max_scrolls <= RESEARCH_SCROLLS_MAX:
        raise SocialError("invalid_params", "关注列表方向或预算无效。")
    budget = _collection_budget(time_budget_seconds)
    source = account(platform, handle)
    evidence = source["url"].rstrip("/") + "/" + direction + "/"
    diag = {"platform": platform, "stage": direction, "account_key": source["account_key"], "source_url": evidence,
            "ok": False, "scanned": 0, "duplicates": 0, "stalls": 0, "invalid_records": 0, "skipped_article_cards": 0,
            "max_scrolls": max_scrolls, "scrolls_completed": 0, "candidate_scan_limit": limit, "time_budget_seconds": budget,
            "coverage": "仅限本次可见列表及有限滚动，不代表完整关注网络。"}
    accounts, relations, seen = [], [], set()
    try:
        async with asyncio.timeout(budget):
            if known_count == 0:
                diag.update(ok=True, outcome="visible_zero_count", message="已核对主页显示该方向数量为零，没有创建关系。")
                return {"accounts": [], "relations": [], "diagnostics": [diag]}
            if platform == "twitter":
                await navigate(page, platform, evidence)
                script = selectors.TW_ACCOUNTS
            else:
                await navigate(page, platform, source["url"])
                path = f'/{source["handle"]}/{direction}/'
                locator = page.locator(f'a[href="{path}"],a[href="https://www.instagram.com{path}"]').first
                await locator.click(timeout=5000)
                await pause()
                await inspect_page(page, platform)
                script = selectors.IG_FOLLOWING
            async for row in _frames(page, platform, script, max_scrolls, diag, lambda value: value.get("handle", "").lower(), limit=limit):
                try:
                    neighbor = account(platform, row.get("handle", ""), row.get("name", ""))
                except SocialError:
                    diag["invalid_records"] += 1
                    continue
                if neighbor["account_key"] == source["account_key"] or neighbor["account_key"] in seen:
                    continue
                seen.add(neighbor["account_key"])
                accounts.append(neighbor)
                origin, target = (source["account_key"], neighbor["account_key"]) if direction == "following" else (neighbor["account_key"], source["account_key"])
                relations.append({"source_key": origin, "target_key": target, "relation": "follows", "evidence_url": evidence,
                                  "observed_at": now(), "discovery_direction": direction,
                                  "metadata": {"collection_method": "visible_follow_list", "list_owner_key": source["account_key"], "direction": direction}})
        diag.update(ok=True, outcome="ok" if relations else "no_visible_neighbors", message="只记录可见列表中的账号；未对不可见关系作推断。")
    except SocialError as exc:
        diag.update(ok=False, outcome=exc.code, message=exc.message, **exc.diagnostic)
    except TimeoutError:
        _timeout_diagnostic(diag, relations)
    except Exception as exc:
        diag.update(ok=False, outcome="list_unavailable", message="该方向列表无法公开读取或页面结构未能确认；没有推断关系。", **safe_error(exc, direction))
    return {"accounts": accounts, "relations": relations, "diagnostics": [diag]}


async def collect_queries(params: dict[str, Any], progress=None, accept=None, *, time_budget_seconds=None) -> dict[str, Any]:
    """Execute explicit keyword groups, preserving the validated single-query path."""
    from ..query_plan import build_query_plan, query_matches
    plan = build_query_plan(params)
    if len(plan["groups"]) == 1:
        return await collect({**params, "keyword": plan["queries"][0]["keyword"]}, progress, accept, time_budget_seconds=time_budget_seconds)
    budget = _collection_budget(time_budget_seconds)
    first = plan["queries"][0]["keyword"]
    options = Params.parse({**params, "keyword": first})
    items, accounts, relations, diagnostics = [], {}, [], []
    seen = set()
    deadline = time.monotonic() + budget
    def gate(item):
        if not query_matches(plan, item.get("content", "")):
            return {"matched": False, "reasons": ["原帖正文不满足所选关键词组组合。"]}
        return accept(item) if accept else {"matched": True}
    try:
        async with asyncio.timeout(budget):
            async with browser_session(options.source, visible=False, require_session=True) as (_, page):
                for index, query in enumerate(plan["queries"]):
                    if len(items) >= options.max_records:
                        break
                    await emit(progress, stage="query", platform=options.source, current=index + 1, total=len(plan["queries"]), message=f"正在执行关键词组 {index + 1}/{len(plan['queries'])}：{query['keyword']}")
                    # Finish the active branch inside this task's deadline, so
                    # its already read rows can join the accumulated result.
                    branch_budget = max(0.001, deadline - time.monotonic() - min(3.0, budget / 2))
                    result = await collect_on_page(page, {**params, "keyword": query["keyword"], "max_records": options.max_records - len(items), "include_following": False}, progress, gate,
                                                   time_budget_seconds=branch_budget)
                    for diag in result["diagnostics"]:
                        diagnostics.append({**diag, "query_id": query["id"], "keyword_group": query["keyword"]})
                    for acc in result["accounts"]:
                        if acc["account_key"] in accounts or len(accounts) < options.max_accounts:
                            accounts.setdefault(acc["account_key"], acc)
                    for item in result["items"]:
                        if item["url"] not in seen and item.get("account_key") in accounts:
                            seen.add(item["url"])
                            item["metadata"]["query_group"] = query["keyword"]
                            item["metadata"]["keyword_mode"] = plan["combination"]
                            items.append(item)
                    if any(row.get("outcome") in {"login_required", "rate_limited", "challenge", "access_denied", "runtime_unavailable", "timeout"} for row in result["diagnostics"]):
                        break
                if options.include_following and options.max_following and not any(row.get("outcome") in {"login_required", "rate_limited", "challenge", "access_denied"} for row in diagnostics):
                    await _following(page, options, accounts, relations, diagnostics)
    except SocialError as exc:
        diagnostics.append({"platform": options.source, "ok": False, "outcome": exc.code, "message": exc.message, **exc.diagnostic})
    except TimeoutError:
        diagnostics.append({"platform": options.source, "ok": False, "outcome": "timeout", "message": MESSAGES["timeout"]})
    except Exception as exc:
        diagnostics.append({"platform": options.source, "ok": False, "outcome": "network_error", "message": MESSAGES["network_error"], **safe_error(exc, "keyword_groups")})
    return {"items": items, "accounts": list(accounts.values()), "relations": relations, "diagnostics": diagnostics, "query_plan": plan}


async def collect(params: dict[str, Any], progress=None, accept=None, *, time_budget_seconds=None) -> dict[str, Any]:
    options = Params.parse(params)
    budget = _collection_budget(time_budget_seconds)
    items, relations, diagnostics = [], [], []
    accounts = {}
    diag = _base_diag(options)
    diag["time_budget_seconds"] = budget
    diagnostics.append(diag)
    await emit(progress, stage="starting", platform=options.source, message="正在验证平台页面与本工作台独立会话。")
    try:
        diag["stage"] = "opening_browser"
        async with asyncio.timeout(budget):
            async with browser_session(options.source, visible=False, require_session=True) as (_, page):
                diag["stage"] = "collecting"
                if options.source == "twitter":
                    await _twitter(page, options, items, accounts, diag, progress, accept)
                else:
                    await _instagram(page, options, items, accounts, diagnostics, diag, progress, accept)
                if options.include_following and options.max_following:
                    await _following(page, options, accounts, relations, diagnostics)
        errors = any(not row.get("ok") for row in diagnostics[1:])
        outcome = "partial" if errors and items else ("parse_error" if errors else ("ok" if items else ("date_filtered_out" if diag["date_filtered"] or diag["unknown_dates"] else "no_matches")))
        diag.update(ok=not errors, outcome=outcome,
                    message="已取得可追溯样本。" if items else "本次可读页面内没有符合日期及查询条件的帖子；不代表平台上不存在相关内容。")
    except SocialError as exc:
        diag.update(ok=False, outcome=exc.code, message=exc.message, **exc.diagnostic)
    except TimeoutError:
        _timeout_diagnostic(diag, items)
    except Exception as exc:
        diag.update(ok=False, outcome="network_error", message=MESSAGES["network_error"], **safe_error(exc, diag.get("stage", "collect")))
    diag.update(post_count=len(items), account_count=len(accounts), relation_count=len(relations))
    await emit(progress, stage="finished", platform=options.source, current=len(items), message=diag["message"], outcome=diag["outcome"])
    return {"items": items, "accounts": list(accounts.values()), "relations": relations, "diagnostics": diagnostics}
