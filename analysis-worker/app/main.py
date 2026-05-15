from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from .db import fetch_one
from .discussions import (
    DEFAULT_DISCUSSION_KEYWORDS,
    discussion_detail,
    fetch_discussions_by_platform,
    finish_fetch_run,
    list_discussions,
    reddit_query_terms,
    reddit_subreddits,
    start_fetch_run,
    store_discussion_post,
)
from .gdelt_fetcher import (
    DEFAULT_RSS_FEEDS,
    fetch_gdelt_articles_detailed,
    fetch_gdelt_query,
    fetch_google_news_query,
    fetch_rss_feed,
    get_gdelt_queries,
    get_news_search_tasks,
)
from .ingest import ingest_articles
from .models import (
    AnalyzeWeeklyRequest,
    AnalyzeWeeklyResponse,
    ArticleInput,
    DiscussionFetchRequest,
    GdeltFetchRequest,
    GdeltFetchResponse,
    IngestRequest,
    IngestResponse,
    JapanAnalyzeRequest,
    JapanNewsFetchRequest,
    JapanResearchTaskCreate,
    JapanResearchTaskResponse,
    JapanSocialAccountCreate,
    JapanSocialAccountDiscover,
    JapanSocialAccountVerify,
    JapanSocialFetchRequest,
    JapanSocialSearchRequest,
)
from .qdrant_store import health as qdrant_health
from .report_writer import JAPAN_REPORT_PREFIX, reports_dir
from .sentiment import classify_sentiment, summarize_sentiments
from .weekly_analysis import (
    analyze_japan_weekly,
    analyze_weekly,
    build_japan_structured_input,
    build_structured_input,
    generate_weekly_markdown,
    load_articles,
    save_weekly_report_record,
)


app = FastAPI(
    title="Japan Anti-War Intel Analysis Worker",
    version="0.2.0",
    description="Public-source Japan anti-war peace intelligence analysis worker.",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/health")
async def health() -> dict[str, object]:
    db_ok = False
    try:
        db_ok = bool(fetch_one("SELECT 1 AS ok"))
    except Exception:
        db_ok = False
    return {"ok": db_ok, "postgres": db_ok, "qdrant": await qdrant_health()}


@app.post("/admin/shutdown")
async def admin_shutdown() -> dict[str, object]:
    """Stop the analysis-worker process after returning a response."""
    def stop_process() -> None:
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=stop_process, daemon=True).start()
    return {
        "ok": True,
        "message": "analysis-worker shutdown requested; /health should stop responding shortly.",
    }


@app.get("/admin/instagram/session")
async def admin_instagram_session() -> dict[str, object]:
    from .instagram_safe import assess_login_readiness

    return assess_login_readiness()


@app.post("/admin/instagram/session/clear")
async def admin_instagram_session_clear() -> dict[str, object]:
    from .instagram_safe import clear_login_session

    return clear_login_session()


@app.post("/admin/instagram/login/start")
async def admin_instagram_login_start(
    force_reset: bool = Query(default=False),
    timeout_seconds: int = Query(default=600, ge=30, le=1800),
) -> dict[str, object]:
    if os.environ.get("DOCKER_ENV"):
        return {
            "ok": False,
            "status": "interactive_login_unavailable",
            "message": "Docker backend cannot open a Windows login window. Use the local login helper.",
            "helper_url": "http://127.0.0.1:18190/instagram/login/start",
            "helper_status_url": "http://127.0.0.1:18190/instagram/login/status",
        }

    from .instagram_safe import prepare_login_session

    asyncio.create_task(
        prepare_login_session(force_reset=force_reset, timeout_seconds=timeout_seconds)
    )
    return {
        "ok": True,
        "status": "login_window_opening",
        "message": "Opening Instagram login window.",
    }



@app.get("/")
async def index() -> FileResponse:
    """Default landing page now routes to Japan anti-war intelligence view."""
    japan_index = STATIC_DIR / "japan-index.html"
    if japan_index.exists():
        return FileResponse(japan_index)
    # Fallback: serve index page
    fallback_index = STATIC_DIR / "index.html"
    if fallback_index.exists():
        return FileResponse(fallback_index)
    return PlainTextResponse("Japan Anti-War Intel Analysis Worker is running.", status_code=200)


@app.post("/ingest", response_model=IngestResponse)
async def ingest(payload: IngestRequest) -> IngestResponse:
    result = await ingest_articles(payload.articles)
    return IngestResponse(ok=True, **result)


@app.post("/gdelt/fetch", response_model=GdeltFetchResponse)
async def gdelt_fetch(payload: GdeltFetchRequest) -> GdeltFetchResponse:
    rows, diagnostics = await fetch_gdelt_articles_detailed(
        start_date=payload.start_date,
        end_date=payload.end_date,
        queries=payload.queries,
        max_records_per_query=payload.max_records_per_query,
    )
    articles = [ArticleInput(**row) for row in rows]
    inserted = 0
    skipped = 0
    if payload.ingest and articles:
        result = await ingest_articles(articles)
        inserted = result["inserted"]
        skipped = result["skipped_duplicates"]
    return GdeltFetchResponse(
        ok=True,
        fetched=len(articles),
        inserted=inserted,
        skipped_duplicates=skipped,
        articles=articles[:20],
        diagnostics=diagnostics,
    )


@app.post("/gdelt/fetch/stream")
async def gdelt_fetch_stream(payload: GdeltFetchRequest) -> StreamingResponse:
    async def events():
        def line(event: dict[str, object]) -> str:
            return json.dumps(event, ensure_ascii=False) + "\n"

        queries = get_gdelt_queries(payload.queries)
        fallback_tasks = get_news_search_tasks(payload.queries)
        rows_by_url: dict[str, dict[str, object]] = {}
        diagnostics: list[dict[str, object]] = []
        yield line(
            {
                "type": "start",
                "total_queries": len(queries),
                "max_records_per_query": payload.max_records_per_query,
                "message": "开始深度检索：GDELT、多语种公开新闻搜索、固定 RSS 源会依次执行。",
            }
        )
        yield line({"type": "layer_start", "source": "GDELT", "message": "第一层：按主题查询 GDELT。"})
        gdelt_rate_limited = 0
        for index, query in enumerate(queries, start=1):
            if gdelt_rate_limited >= 2:
                yield line(
                    {
                        "type": "layer_skip",
                        "source": "GDELT",
                        "message": "GDELT 连续限流，停止 GDELT 层，继续执行其他检索层。",
                    }
                )
                break
            if index > 1:
                yield line({"type": "wait", "seconds": 5.5, "message": "等待 GDELT 公共接口限流窗口。"})
                await asyncio.sleep(5.5)
            yield line({"type": "query_start", "index": index, "total": len(queries), "query": query})
            batch, diagnostic = await fetch_gdelt_query(
                query=query,
                start_date=payload.start_date,
                end_date=payload.end_date,
                max_records=payload.max_records_per_query,
            )
            diagnostics.append(diagnostic)
            status = diagnostic.get("retry_status_code") or diagnostic.get("status_code")
            if status == 429:
                gdelt_rate_limited += 1
            else:
                gdelt_rate_limited = 0
            for article in batch:
                url = str(article["url"])
                if url in rows_by_url:
                    continue
                rows_by_url[url] = article
                yield line(
                    {
                        "type": "article",
                        "title": article.get("title"),
                        "url": url,
                        "source_name": article.get("source_name"),
                        "published_at": article.get("published_at"),
                        "query": query,
                    }
                )
            yield line({"type": "query_done", "diagnostic": diagnostic, "fetched_so_far": len(rows_by_url)})

        yield line(
            {
                "type": "fallback_start",
                "source": "Google News RSS",
                "total_queries": len(fallback_tasks),
                "languages": ["繁体中文", "简体中文", "日文", "英文"],
                "message": "第二层：执行多语种公开新闻搜索，覆盖繁体中文、简体中文、日文和英文。",
            }
        )
        for index, task in enumerate(fallback_tasks, start=1):
            query = task["query"]
            yield line(
                {
                    "type": "fallback_query_start",
                    "index": index,
                    "total": len(fallback_tasks),
                    "query": query,
                    "language": task["language"],
                }
            )
        news_semaphore = asyncio.Semaphore(8)

        async def run_news_task(index: int, task: dict[str, str]):
            async with news_semaphore:
                batch, diagnostic = await fetch_google_news_query(
                    query=task["query"],
                    start_date=payload.start_date,
                    end_date=payload.end_date,
                    max_records=payload.max_records_per_query,
                    locale=task,
                )
                return index, task, batch, diagnostic

        news_jobs = [run_news_task(index, task) for index, task in enumerate(fallback_tasks, start=1)]
        for job in asyncio.as_completed(news_jobs):
            index, task, batch, diagnostic = await job
            query = task["query"]
            diagnostics.append(diagnostic)
            for article in batch:
                url = str(article["url"])
                if url in rows_by_url:
                    continue
                rows_by_url[url] = article
                yield line(
                    {
                        "type": "article",
                        "title": article.get("title"),
                        "url": url,
                        "source_name": article.get("source_name"),
                        "published_at": article.get("published_at"),
                        "query": query,
                        "search_source": "Google News RSS",
                        "language": task["language"],
                        }
                    )
            yield line({"type": "fallback_query_done", "diagnostic": diagnostic, "fetched_so_far": len(rows_by_url)})

        yield line(
            {
                "type": "rss_start",
                "source": "RSS",
                "total_feeds": len(DEFAULT_RSS_FEEDS),
                "message": "第三层：读取固定新闻 RSS 源，补充通讯社、本地媒体和英文媒体。",
            }
        )
        for index, feed in enumerate(DEFAULT_RSS_FEEDS, start=1):
            yield line({"type": "rss_feed_start", "index": index, "total": len(DEFAULT_RSS_FEEDS), "feed_name": feed["name"]})
        rss_semaphore = asyncio.Semaphore(6)

        async def run_rss_task(index: int, feed: dict[str, str]):
            async with rss_semaphore:
                batch, diagnostic = await fetch_rss_feed(
                    feed=feed,
                    start_date=payload.start_date,
                    end_date=payload.end_date,
                    max_records=payload.max_records_per_query,
                )
                return index, feed, batch, diagnostic

        rss_jobs = [run_rss_task(index, feed) for index, feed in enumerate(DEFAULT_RSS_FEEDS, start=1)]
        for job in asyncio.as_completed(rss_jobs):
            index, feed, batch, diagnostic = await job
            diagnostics.append(diagnostic)
            for article in batch:
                url = str(article["url"])
                if url in rows_by_url:
                    continue
                rows_by_url[url] = article
                yield line(
                    {
                        "type": "article",
                        "title": article.get("title"),
                        "url": url,
                        "source_name": article.get("source_name"),
                        "published_at": article.get("published_at"),
                        "search_source": "RSS",
                    }
                )
            yield line({"type": "rss_feed_done", "diagnostic": diagnostic, "fetched_so_far": len(rows_by_url)})

        articles = [ArticleInput(**row) for row in rows_by_url.values()]
        inserted = 0
        skipped = 0
        if payload.ingest and articles:
            yield line({"type": "ingest_start", "count": len(articles), "message": "开始写入数据库并去重。"})
            result = await ingest_articles(articles)
            inserted = result["inserted"]
            skipped = result["skipped_duplicates"]
        yield line(
            {
                "type": "done",
                "ok": True,
                "fetched": len(articles),
                "inserted": inserted,
                "skipped_duplicates": skipped,
                "diagnostics": diagnostics,
            }
        )

    return StreamingResponse(events(), media_type="application/x-ndjson; charset=utf-8")


@app.post("/discussions/fetch/stream")
async def discussions_fetch_stream(payload: DiscussionFetchRequest) -> StreamingResponse:
    async def events():
        def line(event: dict[str, object]) -> str:
            return json.dumps(event, ensure_ascii=False, default=str) + "\n"

        allowed = {"reddit", "discord"}
        platforms = [platform.lower() for platform in payload.platforms if platform.lower() in allowed]
        if not platforms:
            platforms = ["reddit"]
        keywords = [item.strip() for item in payload.keywords or [] if item and item.strip()]
        display_keywords = keywords or DEFAULT_DISCUSSION_KEYWORDS
        run_id = start_fetch_run(
            platforms=platforms,
            start_date=payload.start_date.isoformat(),
            end_date=payload.end_date.isoformat(),
            keywords=display_keywords,
        )
        diagnostics: list[dict[str, object]] = []
        totals = {
            "posts": 0,
            "comments": 0,
            "inserted_posts": 0,
            "updated_posts": 0,
        }
        yield line(
            {
                "type": "start",
                "run_id": run_id,
                "platforms": platforms,
                "keywords": display_keywords,
                "message": "开始抓取公开公众讨论。只读取公开页面/API，不登录、不绕过验证码、不抓私密内容。",
            }
        )
        try:
            for platform in platforms:
                # Emit total_queries so the frontend can show a progress bar
                if platform == "reddit":
                    reddit_kw = keywords if keywords else None
                    rq = reddit_query_terms(reddit_kw)
                    rs = reddit_subreddits(reddit_kw)
                    yield line({"type": "platform_start", "platform": platform, "total_queries": len(rq) * len(rs)})
                else:
                    yield line({"type": "platform_start", "platform": platform})
                progress_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

                async def emit_progress(event: dict[str, object]) -> None:
                    await progress_queue.put(event)

                fetch_task = asyncio.create_task(
                    fetch_discussions_by_platform(
                        platform=platform,
                        start_date=payload.start_date,
                        end_date=payload.end_date,
                        keywords=keywords if keywords else None,
                        max_posts=payload.max_posts_per_platform,
                        max_comments=payload.max_comments_per_post,
                        progress=emit_progress,
                    )
                )
                while not fetch_task.done():
                    try:
                        progress_event = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                        yield line(progress_event)
                    except asyncio.TimeoutError:
                        continue
                while not progress_queue.empty():
                    yield line(progress_queue.get_nowait())
                items, platform_diagnostics = await fetch_task
                diagnostics.extend(platform_diagnostics)
                for diagnostic in platform_diagnostics:
                    yield line({
                        "type": "diagnostic",
                        "platform": platform,
                        "diagnostic": diagnostic,
                        "total_queries": diagnostic.get("total_queries"),
                        "current_query_index": diagnostic.get("current_query_index"),
                    })

                platform_posts = 0
                platform_comments = 0
                for item in items:
                    post = item["post"]
                    comments = item.get("comments", [])
                    platform_posts += 1
                    platform_comments += len(comments)
                    totals["posts"] += 1
                    totals["comments"] += len(comments)

                    post_sentiment = classify_sentiment(
                        f"{post.get('title', '')}\n{post.get('raw_text', '')}"
                    )
                    comment_summary = summarize_sentiments(
                        [
                            classify_sentiment(str(comment.get("comment_text") or comment.get("text") or ""))
                            for comment in comments
                        ]
                    )
                    post_id = None
                    inserted = False
                    if payload.ingest:
                        stored = store_discussion_post(post, comments)
                        post_id = stored["post_id"]
                        inserted = bool(stored["inserted_post"])
                        if inserted:
                            totals["inserted_posts"] += 1
                        else:
                            totals["updated_posts"] += 1

                    yield line(
                        {
                            "type": "post",
                            "post_id": post_id,
                            "inserted": inserted,
                            "platform": post.get("platform"),
                            "community": post.get("board_name") or post.get("community"),
                            "title": post.get("title"),
                            "url": post.get("url"),
                            "comment_count": len(comments),
                            "score": post.get("score"),
                            "sentiment": post_sentiment,
                            "comment_sentiments": comment_summary,
                        }
                    )
                yield line(
                    {
                        "type": "platform_done",
                        "platform": platform,
                        "posts": platform_posts,
                        "comments": platform_comments,
                    }
                )

            finish_fetch_run(
                run_id,
                status="completed",
                fetched_posts=totals["posts"],
                fetched_comments=totals["comments"],
                diagnostics=diagnostics,
            )
            yield line({"type": "done", "ok": True, "run_id": run_id, **totals, "diagnostics": diagnostics})
        except Exception as exc:
            diagnostics.append({"level": "error", "message": str(exc)})
            finish_fetch_run(
                run_id,
                status="failed",
                fetched_posts=totals["posts"],
                fetched_comments=totals["comments"],
                diagnostics=diagnostics,
            )
            yield line({"type": "error", "ok": False, "run_id": run_id, "message": str(exc), **totals})

    return StreamingResponse(events(), media_type="application/x-ndjson; charset=utf-8")


@app.get("/api/discussions")
async def api_discussions(
    start_date: date | None = None,
    end_date: date | None = None,
    platform: str | None = None,
    keyword: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    return {
        "ok": True,
        "posts": list_discussions(
            start_date=start_date,
            end_date=end_date,
            platform=platform,
            keyword=keyword,
            limit=limit,
        ),
    }


@app.get("/api/discussions/{post_id}")
async def api_discussion_detail(post_id: int) -> dict[str, object]:
    detail = discussion_detail(post_id)
    if not detail:
        raise HTTPException(status_code=404, detail="discussion not found")
    return {"ok": True, "discussion": detail}


@app.post("/analyze/weekly", response_model=AnalyzeWeeklyResponse)
async def weekly(payload: AnalyzeWeeklyRequest) -> AnalyzeWeeklyResponse:
    result = await analyze_weekly(payload.start_date, payload.end_date)
    return AnalyzeWeeklyResponse(ok=True, **result)


@app.post("/analyze/weekly/stream")
async def weekly_stream(payload: AnalyzeWeeklyRequest) -> StreamingResponse:
    async def events():
        def line(event: dict[str, object]) -> str:
            return json.dumps(event, ensure_ascii=False, default=str) + "\n"

        task: asyncio.Task[str] | None = None
        try:
            yield line(
                {
                    "type": "start",
                    "start_date": payload.start_date.isoformat(),
                    "end_date": payload.end_date.isoformat(),
                    "message": "开始生成周报：先汇总新闻与公众讨论，再调用 AI 写作。",
                }
            )

            articles = load_articles(payload.start_date, payload.end_date)
            yield line(
                {
                    "type": "articles_loaded",
                    "article_count": len(articles),
                    "message": f"已读取新闻文章 {len(articles)} 篇。",
                }
            )

            yield line({"type": "stage", "stage": "structured_input", "message": "正在做事件聚类、主题趋势、实体频次、历史对比和公众讨论汇总。"})
            structured = await build_structured_input(payload.start_date, payload.end_date, articles)
            discussion = structured.get("discussion_signals", {}) or {}
            history = structured.get("historical_comparison", {}) or {}
            topic_changes = structured.get("topic_changes", []) or []
            clusters = structured.get("event_clusters", []) or []
            top_topics = [
                {
                    "topic_name": item.get("topic_name"),
                    "current_count": item.get("current_count"),
                    "change_label": item.get("change_label"),
                }
                for item in topic_changes[:6]
            ]
            yield line(
                {
                    "type": "structured_ready",
                    "article_count": len(articles),
                    "event_cluster_count": len(clusters),
                    "topic_count": len(topic_changes),
                    "discussion_post_count": discussion.get("post_count", 0),
                    "discussion_comment_count": discussion.get("comment_count", 0),
                    "recent_discussion_attitude": (discussion.get("recent_sentiment") or {}).get("overall_attitude", "样本不足"),
                    "long_term_discussion_attitude": (discussion.get("long_term_sentiment") or {}).get("overall_attitude", "样本不足"),
                    "sentiment_comparison": (discussion.get("sentiment_comparison") or {}).get("label", "不足以判断"),
                    "political_temperature": history.get("political_temperature", "不足以判断"),
                    "top_topics": top_topics,
                    "message": "结构化输入已完成，公众讨论会作为舆论/情绪信号进入 AI 分析，但不作为事实证据。",
                }
            )

            yield line(
                {
                    "type": "llm_start",
                    "message": "AI 正在撰写周报。页面会持续显示进度；即使报告很长，也不会再让浏览器干等。",
                    "summary": _weekly_progress_summary(structured),
                }
            )
            task = asyncio.create_task(generate_weekly_markdown(payload.start_date, payload.end_date, structured))
            elapsed = 0
            while not task.done():
                await asyncio.sleep(5)
                elapsed += 5
                yield line(
                    {
                        "type": "heartbeat",
                        "elapsed_seconds": elapsed,
                        "message": "AI 仍在分析和写作中。",
                        "summary": _weekly_progress_summary(structured),
                    }
                )

            markdown = await task
            yield line({"type": "llm_done", "message": "AI 分析已完成，正在保存 Markdown 报告。"})
            result = save_weekly_report_record(payload.start_date, payload.end_date, structured, markdown)
            yield line({"type": "done", "ok": True, **result})
        except asyncio.CancelledError:
            if task and not task.done():
                task.cancel()
            raise
        except Exception as exc:
            yield line({"type": "error", "ok": False, "message": str(exc)})
        finally:
            if task and not task.done():
                task.cancel()

    return StreamingResponse(events(), media_type="application/x-ndjson; charset=utf-8")


def _weekly_progress_summary(structured: dict[str, object]) -> str:
    clusters = structured.get("event_clusters", []) or []
    topics = structured.get("topic_changes", []) or []
    discussion = structured.get("discussion_signals", {}) or {}
    cluster_titles = [
        str(cluster.get("representative_title"))
        for cluster in clusters[:3]
        if isinstance(cluster, dict) and cluster.get("representative_title")
    ]
    topic_names = [
        str(topic.get("topic_name"))
        for topic in topics[:5]
        if isinstance(topic, dict) and topic.get("topic_name")
    ]
    discussion_text = (
        f"公众讨论样本 {discussion.get('post_count', 0)} 篇帖子、{discussion.get('comment_count', 0)} 条评论"
        if isinstance(discussion, dict)
        else "公众讨论样本不足"
    )
    parts = []
    if cluster_titles:
        parts.append("重点事件：" + "；".join(cluster_titles))
    if topic_names:
        parts.append("高频主题：" + "、".join(topic_names))
    parts.append(discussion_text + "，仅作为舆论/情绪信号")
    return "。".join(parts)


@app.get("/api/reports")
async def list_reports(region: str = Query(default="japan", regex="^(japan|all)$")) -> dict[str, object]:
    """List generated reports. Use ?region=all for all reports."""
    if region == "all":
        pattern = "*.md"
    else:
        pattern = f"{JAPAN_REPORT_PREFIX}-*.md"
    files = sorted(reports_dir().glob(pattern), reverse=True)
    return {
        "ok": True,
        "region": region,
        "reports": [
            {"name": path.name, "path": f"reports/{path.name}", "size": path.stat().st_size}
            for path in files
        ],
    }


@app.get("/reports/{filename}")
async def read_report(filename: str):
    safe_name = Path(filename).name
    path = reports_dir() / safe_name
    if not path.exists() or not safe_name.endswith(".md"):
        return PlainTextResponse("report not found", status_code=404)
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=safe_name,
        content_disposition_type="attachment",
    )


@app.get("/api/japan/reports")
async def list_japan_reports_alias() -> dict[str, object]:
    return await list_reports(region="japan")


@app.get("/japan/reports/{filename}")
async def read_japan_report_alias(filename: str):
    return await read_report(filename)


# ══════════════════════════════════════════════════════════════
# Japan anti-war intelligence API endpoints
# ══════════════════════════════════════════════════════════════

# --- Domain module lazy loading ---

def _get_domain_func(module_name: str, func_name: str):
    try:
        import importlib
        mod = importlib.import_module(f".{module_name}", package="app")
        return getattr(mod, func_name, None)
    except (ImportError, AttributeError):
        return None


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _empty_db_response(collection_name: str, exc: Exception) -> dict[str, object]:
    return {
        "ok": False,
        collection_name: [],
        "count": 0,
        "diagnostics": [
            {
                "source": "postgres",
                "status": "unavailable",
                "message": str(exc),
            }
        ],
    }


# --- GET /api/japan/social/accounts ---

@app.get("/api/japan/social/accounts")
async def japan_list_social_accounts(
    verified_only: bool = Query(default=False),
    platform: str | None = None,
    category: str | None = None,
    status: str | None = None,
    organization: str | None = None,
    q: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    """List Japan social accounts. Candidate accounts excluded from default view."""
    list_func = _get_domain_func("japan_social_accounts", "list_accounts")
    if list_func:
        try:
            accounts = await _maybe_await(
                list_func(
                    platform=platform,
                    category=category,
                    status=status or ("verified" if verified_only else None),
                    organization=organization,
                    search=search or q,
                    limit=limit,
                    offset=offset,
                )
            )
            return {"ok": True, "accounts": accounts, "count": len(accounts)}
        except Exception as exc:
            return _empty_db_response("accounts", exc)

    from .db import fetch_all

    conditions = ["1=1"]
    params: list[object] = []

    if verified_only:
        conditions.append("verified = TRUE")
    if status:
        conditions.append("status = %s")
        params.append(status)
    if platform:
        conditions.append("platform = %s")
        params.append(platform)
    if category:
        conditions.append("category = %s")
        params.append(category)
    if organization:
        conditions.append("organization ILIKE %s")
        params.append(f"%{organization}%")
    if search or q:
        term = search or q or ""
        conditions.append("(account_name ILIKE %s OR handle ILIKE %s OR note ILIKE %s)")
        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])

    where = " AND ".join(conditions)
    params.extend([limit, offset])
    try:
        rows = fetch_all(
            f"SELECT * FROM japan_social_accounts WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            tuple(params),
        )
    except Exception as exc:
        return _empty_db_response("accounts", exc)
    return {"ok": True, "accounts": rows, "count": len(rows)}


@app.get("/api/japan/social-accounts")
async def japan_list_social_accounts_alias(
    verified_only: bool = Query(default=False),
    platform: str | None = None,
    category: str | None = None,
    status: str | None = None,
    organization: str | None = None,
    q: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    return await japan_list_social_accounts(
        verified_only=verified_only,
        platform=platform,
        category=category,
        status=status,
        organization=organization,
        q=q,
        search=search,
        limit=limit,
        offset=offset,
    )


# --- POST /japan/social/accounts ---

@app.post("/japan/social/accounts")
async def japan_create_social_account(payload: JapanSocialAccountCreate) -> dict[str, object]:
    """Manually add a Japan social account."""
    upsert_func = _get_domain_func("japan_social_accounts", "upsert_account")
    if not upsert_func:
        raise HTTPException(status_code=503, detail="japan_social_accounts module unavailable")
    result = await _maybe_await(
        upsert_func(
            platform=payload.platform,
            handle=payload.username,
            account_name=payload.display_name or payload.username,
            category=payload.category,
            url=payload.account_url,
            note=payload.bio or "",
            status="seed",
            discovery_method="manual",
            evidence_url=payload.account_url,
        )
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("error", "invalid account"))
    return {"ok": True, "account": result["account"]}


@app.post("/api/japan/social-accounts")
async def japan_create_social_account_alias(payload: JapanSocialAccountCreate) -> dict[str, object]:
    return await japan_create_social_account(payload)


# --- POST /japan/social/accounts/discover ---

@app.post("/japan/social/accounts/discover")
async def japan_discover_social_accounts(payload: JapanSocialAccountDiscover) -> dict[str, object]:
    """Discover social accounts via domain module or fallback."""
    discover_func = _get_domain_func("japan_social_accounts", "discover_japan_social_accounts")
    if discover_func:
        result = await _maybe_await(discover_func(
            platform=payload.platform,
            keywords=payload.keywords,
            category=payload.category,
            max_accounts=payload.max_accounts,
        ))
        return {"ok": True, **result}
    return {
        "ok": False,
        "message": "japan_social_accounts domain module not yet integrated. "
                   "Accounts will be discoverable once the module is deployed.",
        "accounts": [],
    }


@app.post("/api/japan/social-accounts/discover")
async def japan_discover_social_accounts_alias(payload: JapanSocialAccountDiscover) -> dict[str, object]:
    return await japan_discover_social_accounts(payload)


# --- POST /japan/social/accounts/{id}/verify ---

@app.post("/japan/social/accounts/{account_id}/verify")
async def japan_verify_social_account(
    account_id: int,
    payload: JapanSocialAccountVerify,
) -> dict[str, object]:
    """Verify or reject a social account. Existing account URL can be used as evidence."""
    verify_func = _get_domain_func("japan_social_accounts", "verify_account")
    if not verify_func:
        raise HTTPException(status_code=503, detail="japan_social_accounts module unavailable")
    result = await _maybe_await(
        verify_func(account_id, verified=payload.verified, evidence_url=payload.evidence_url)
    )
    if not result.get("ok"):
        detail = result.get("error", "account not found")
        raise HTTPException(status_code=422 if "evidence" in str(detail) else 404, detail=detail)
    return {"ok": True, "account": result.get("account", result)}


@app.post("/api/japan/social-accounts/{account_id}/verify")
async def japan_verify_social_account_alias(account_id: int, payload: JapanSocialAccountVerify) -> dict[str, object]:
    return await japan_verify_social_account(account_id, payload)


@app.get("/api/japan/social-accounts/{account_id}")
async def japan_get_social_account_alias(account_id: int) -> dict[str, object]:
    from .db import fetch_one

    try:
        account = fetch_one("SELECT * FROM japan_social_accounts WHERE id = %s", (account_id,))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    alias_func = _get_domain_func("japan_social_accounts", "_with_aliases")
    if alias_func:
        account = alias_func(account)
    return {"ok": True, "account": account}


# --- POST /japan/social/fetch/stream ---

@app.post("/japan/social/fetch/stream")
async def japan_social_fetch_stream(payload: JapanSocialFetchRequest) -> StreamingResponse:
    """Stream-fetch Japan social posts via domain module."""
    fetch_func = _get_domain_func("japan_peace_social", "fetch_japan_social_stream")

    async def events():
        def line(event: dict[str, object]) -> str:
            return json.dumps(event, ensure_ascii=False, default=str) + "\n"

        yield line({
            "type": "start",
            "start_date": payload.start_date.isoformat(),
            "end_date": payload.end_date.isoformat(),
            "platform": payload.platform,
            "message": "日本 SNS データの取得を開始します。",
        })

        if not fetch_func:
            yield line({
                "type": "error",
                "ok": False,
                "message": "japan_peace_social domain module not yet integrated.",
            })
            return

        async for event in fetch_func(
            start_date=payload.start_date,
            end_date=payload.end_date,
            platform=payload.platform,
            platforms=payload.platforms,
            account_ids=payload.account_ids,
            include_verified_accounts=payload.include_verified_accounts,
            include_candidate_accounts=payload.include_candidate_accounts,
            max_posts=payload.max_posts,
            keywords=payload.keywords,
        ):
            yield line(event)

    return StreamingResponse(events(), media_type="application/x-ndjson; charset=utf-8")


# --- POST /japan/social/search/stream ---

@app.post("/japan/social/search/stream")
async def japan_social_search_stream(payload: JapanSocialSearchRequest) -> StreamingResponse:
    """Stream-search social media by keywords across platforms. No account database needed."""
    search_func = _get_domain_func("japan_peace_social", "search_social_by_keywords")

    async def events():
        def line(event: dict[str, object]) -> str:
            return json.dumps(event, ensure_ascii=False, default=str) + "\n"

        platforms = payload.platforms or ["reddit", "twitter", "instagram", "mastodon", "youtube"]
        yield line({
            "type": "start",
            "keywords": payload.keywords,
            "start_date": payload.start_date.isoformat(),
            "end_date": payload.end_date.isoformat(),
            "platforms": platforms,
            "message": f"キーワード検索を開始: {', '.join(payload.keywords[:5])}",
        })

        if not search_func:
            yield line({
                "type": "error",
                "ok": False,
                "message": "japan_peace_social domain module not yet integrated.",
            })
            return

        all_posts, diagnostics = await search_func(
            keywords=payload.keywords,
            start_date=payload.start_date,
            end_date=payload.end_date,
            platforms=platforms,
            max_posts_per_platform=payload.max_posts_per_platform,
        )

        for post in all_posts:
            yield line({
                "type": "post",
                "platform": post.get("platform"),
                "url": post.get("url"),
                "content_text": post.get("raw_text") or post.get("title"),
                "engagement_count": post.get("engagement_count", 0),
            })

        yield line({
            "type": "done",
            "ok": True,
            "fetched": len(all_posts),
            "diagnostics": diagnostics,
        })

    return StreamingResponse(events(), media_type="application/x-ndjson; charset=utf-8")


# --- GET /api/japan/social/posts ---

@app.get("/api/japan/social/posts")
async def japan_list_social_posts(
    start_date: date | None = None,
    end_date: date | None = None,
    platform: str | None = None,
    account_id: int | None = None,
    keyword: str | None = None,
    verified_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    """List Japan social posts. Default excludes candidate account posts from main view."""
    list_func = _get_domain_func("japan_peace_social", "list_japan_social_posts")
    if list_func:
        try:
            posts = list_func(
                start_date=start_date,
                end_date=end_date,
                platform=platform,
                account_id=account_id,
                keyword=keyword,
                verified_only=verified_only,
                limit=limit,
            )
            return {"ok": True, "posts": posts, "count": len(posts)}
        except Exception as exc:
            return _empty_db_response("posts", exc)

    from .db import fetch_all

    conditions = ["1=1"]
    params: list[object] = []

    if verified_only:
        conditions.append(
            "account_id IN (SELECT id FROM japan_social_accounts WHERE verified = TRUE AND is_candidate = FALSE)"
        )

    if start_date:
        conditions.append("COALESCE(published_at, fetched_at) >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("COALESCE(published_at, fetched_at) < %s")
        params.append(end_date + timedelta(days=1))
    if platform:
        conditions.append("platform = %s")
        params.append(platform)
    if account_id:
        conditions.append("account_id = %s")
        params.append(account_id)
    if keyword:
        conditions.append("content_text ILIKE %s")
        params.append(f"%{keyword}%")

    where = " AND ".join(conditions)
    params.append(limit)
    try:
        rows = fetch_all(
            f"SELECT * FROM japan_social_posts WHERE {where} ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        return _empty_db_response("posts", exc)
    return {"ok": True, "posts": rows, "count": len(rows)}


@app.get("/api/japan/contents")
async def japan_list_contents_alias(
    start_date: date | None = None,
    end_date: date | None = None,
    keyword: str | None = None,
    type: str | None = None,
    platform: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    if type in (None, "", "news"):
        news = await japan_list_news(start_date=start_date, end_date=end_date, keyword=keyword, limit=limit)
        for article in news.get("articles", []):
            items.append({"type": "news", **article})
    if type in (None, "", "social"):
        posts = await japan_list_social_posts(
            start_date=start_date,
            end_date=end_date,
            keyword=keyword,
            platform=platform,
            limit=limit,
        )
        for post in posts.get("posts", []):
            items.append({"type": "social", **post})
    items = sorted(items, key=lambda item: str(item.get("published_at") or item.get("fetched_at") or ""), reverse=True)[:limit]
    return {"ok": True, "items": items, "contents": items, "count": len(items)}


# --- POST /japan/news/fetch/stream ---

@app.post("/japan/news/fetch/stream")
async def japan_news_fetch_stream(payload: JapanNewsFetchRequest) -> StreamingResponse:
    """Stream-fetch Japan peace/anti-war news via domain module."""
    fetch_func = _get_domain_func("japan_peace_news", "fetch_japan_news_stream")

    async def events():
        def line(event: dict[str, object]) -> str:
            return json.dumps(event, ensure_ascii=False, default=str) + "\n"

        yield line({
            "type": "start",
            "start_date": payload.start_date.isoformat(),
            "end_date": payload.end_date.isoformat(),
            "message": "日本平和・反戦ニュースの取得を開始します。",
        })

        if not fetch_func:
            yield line({
                "type": "error",
                "ok": False,
                "message": "japan_peace_news domain module not yet integrated.",
            })
            return

        async for event in fetch_func(
            start_date=payload.start_date,
            end_date=payload.end_date,
            keywords=payload.keywords,
            categories=payload.categories,
            max_records=payload.max_records,
            ingest=payload.ingest,
        ):
            yield line(event)

    return StreamingResponse(events(), media_type="application/x-ndjson; charset=utf-8")


# --- GET /api/japan/news ---

@app.get("/api/japan/news")
async def japan_list_news(
    start_date: date | None = None,
    end_date: date | None = None,
    keyword: str | None = None,
    category: str | None = None,
    antiwar_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    """List Japan peace/anti-war news."""
    list_func = _get_domain_func("japan_peace_news", "list_japan_news")
    if list_func:
        try:
            articles = list_func(
                start_date=start_date,
                end_date=end_date,
                keyword=keyword,
                category=category,
                antiwar_only=antiwar_only,
                limit=limit,
            )
            return {"ok": True, "articles": articles, "count": len(articles)}
        except Exception as exc:
            return _empty_db_response("articles", exc)

    from .db import fetch_all

    conditions = ["1=1"]
    params: list[object] = []

    if start_date:
        conditions.append("COALESCE(published_at, fetched_at) >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("COALESCE(published_at, fetched_at) < %s")
        params.append(end_date + timedelta(days=1))
    if keyword:
        conditions.append("(title ILIKE %s OR clean_text ILIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if category:
        conditions.append("category = %s")
        params.append(category)
    if antiwar_only:
        conditions.append("antiwar_relevance = TRUE")

    where = " AND ".join(conditions)
    params.append(limit)
    try:
        rows = fetch_all(
            f"SELECT * FROM japan_news WHERE {where} ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        return _empty_db_response("articles", exc)
    return {"ok": True, "articles": rows, "count": len(rows)}


# --- GET /api/japan/events ---

@app.get("/api/japan/events")
async def japan_list_events(
    start_date: date | None = None,
    end_date: date | None = None,
    category: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    """List Japan anti-war events."""
    from .db import fetch_all

    conditions = ["1=1"]
    params: list[object] = []

    if start_date:
        conditions.append("event_date >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("event_date <= %s")
        params.append(end_date)
    if category:
        conditions.append("category = %s")
        params.append(category)

    where = " AND ".join(conditions)
    params.append(limit)
    try:
        rows = fetch_all(
            f"SELECT * FROM japan_antiwar_events WHERE {where} ORDER BY event_date DESC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        return _empty_db_response("events", exc)
    return {"ok": True, "events": rows, "count": len(rows)}


# --- POST /japan/analyze/stream ---

@app.post("/japan/analyze/stream")
async def japan_analyze_stream(payload: JapanAnalyzeRequest) -> StreamingResponse:
    """Stream-generate Japan anti-war weekly analysis."""
    analyze_func = _get_domain_func("japan_analysis", "analyze_japan_period_stream")

    async def events():
        def line(event: dict[str, object]) -> str:
            return json.dumps(event, ensure_ascii=False, default=str) + "\n"

        yield line({
            "type": "start",
            "start_date": payload.start_date.isoformat(),
            "end_date": payload.end_date.isoformat(),
            "include_social": payload.include_social,
            "include_news": payload.include_news,
            "include_events": payload.include_events,
            "message": "日本反戦態勢分析を開始します。",
        })

        if analyze_func:
            async for event in analyze_func(
                start_date=payload.start_date,
                end_date=payload.end_date,
                include_social=payload.include_social,
                include_news=payload.include_news,
                include_events=payload.include_events,
                model=payload.model,
            ):
                yield line(event)
            return

        # Fallback: use built-in Japan weekly analysis
        task: asyncio.Task[str] | None = None
        try:
            yield line({"type": "stage", "stage": "gathering", "message": "ニュース・SNS・イベントデータを集計中。"})
            structured = await build_japan_structured_input(payload.start_date, payload.end_date)

            yield line({
                "type": "structured_ready",
                "article_count": len(structured.get("source_list", [])),
                "social_post_count": structured.get("social_signals", {}).get("total_posts", 0),
                "event_count": len(structured.get("antiwar_events", [])),
                "message": "構造化入力の準備が完了しました。candidate アカウントの投稿は主要な結論から除外されています。",
            })

            yield line({
                "type": "llm_start",
                "message": "AI が日本反戦態勢週報を作成中。candidate アカウントは素材リストのみに記載、主要結論に含めません。",
            })

            from .weekly_analysis import generate_japan_weekly_markdown, save_japan_report_record

            task = asyncio.create_task(
                generate_japan_weekly_markdown(payload.start_date, payload.end_date, structured)
            )
            elapsed = 0
            while not task.done():
                await asyncio.sleep(5)
                elapsed += 5
                yield line({
                    "type": "heartbeat",
                    "elapsed_seconds": elapsed,
                    "message": "AI が分析・執筆中。candidate アカウントの投稿は主要結論から除外されています。",
                })

            markdown = await task
            yield line({"type": "llm_done", "message": "AI 分析が完了しました。"})
            result = save_japan_report_record(payload.start_date, payload.end_date, structured, markdown)
            yield line({"type": "done", "ok": True, **result})

        except asyncio.CancelledError:
            if task and not task.done():
                task.cancel()
            raise
        except Exception as exc:
            yield line({"type": "error", "ok": False, "message": str(exc)})
        finally:
            if task and not task.done():
                task.cancel()

    return StreamingResponse(events(), media_type="application/x-ndjson; charset=utf-8")


# --- POST /japan/research/tasks ---

@app.post("/japan/research/tasks", response_model=JapanResearchTaskResponse)
async def japan_create_research_task(payload: JapanResearchTaskCreate) -> JapanResearchTaskResponse:
    """Create an async research task via domain module or fallback."""
    create_func = _get_domain_func("async_research", "create_research_task")
    if create_func:
        result = await _maybe_await(create_func(task_type=payload.task_type, params=payload.params))
        task = result.get("task", result) if isinstance(result, dict) else result
        return JapanResearchTaskResponse(ok=True, task=task)

    from .db import execute, fetch_one

    execute(
        """
        INSERT INTO japan_research_tasks (task_type, status, params)
        VALUES (%s, 'pending', %s)
        """,
        (payload.task_type, json.dumps(payload.params, ensure_ascii=False, default=str)),
    )
    task = fetch_one(
        "SELECT * FROM japan_research_tasks ORDER BY created_at DESC LIMIT 1"
    )
    return JapanResearchTaskResponse(ok=True, task=task or {})


# --- GET /api/japan/research/tasks/{id} ---

@app.get("/api/japan/research/tasks/{task_id}", response_model=JapanResearchTaskResponse)
async def japan_get_research_task(task_id: int) -> JapanResearchTaskResponse:
    """Get research task status via domain module or fallback."""
    get_func = _get_domain_func("async_research", "get_research_task")
    if get_func:
        task = await _maybe_await(get_func(task_id))
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        return JapanResearchTaskResponse(ok=True, task=task)

    from .db import fetch_one

    task = fetch_one("SELECT * FROM japan_research_tasks WHERE id = %s", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return JapanResearchTaskResponse(ok=True, task=task)


@app.post("/api/japan/research-tasks", response_model=JapanResearchTaskResponse)
async def japan_create_research_task_alias(payload: JapanResearchTaskCreate) -> JapanResearchTaskResponse:
    return await japan_create_research_task(payload)


@app.get("/api/japan/research-tasks")
async def japan_list_research_tasks_alias(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, object]:
    list_func = _get_domain_func("async_research", "list_research_tasks")
    if list_func:
        try:
            tasks = await _maybe_await(list_func(limit=limit))
            return {"ok": True, "tasks": tasks}
        except Exception as exc:
            return _empty_db_response("tasks", exc)
    from .db import fetch_all

    try:
        tasks = fetch_all("SELECT * FROM japan_research_tasks ORDER BY created_at DESC LIMIT %s", (limit,))
    except Exception as exc:
        return _empty_db_response("tasks", exc)
    return {"ok": True, "tasks": tasks}


@app.get("/api/japan/research-tasks/{task_id}", response_model=JapanResearchTaskResponse)
async def japan_get_research_task_alias(task_id: int) -> JapanResearchTaskResponse:
    return await japan_get_research_task(task_id)


@app.delete("/api/japan/research-tasks/{task_id}")
async def japan_delete_research_task_alias(task_id: int) -> dict[str, object]:
    from .db import execute

    execute("DELETE FROM japan_research_tasks WHERE id = %s", (task_id,))
    return {"ok": True, "task_id": task_id}
