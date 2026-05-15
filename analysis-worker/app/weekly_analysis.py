from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .cluster import cluster_articles, cluster_articles_by_vectors
from .db import fetch_all, get_conn
from .discussions import load_discussion_signals
from .llm_client import generate_japan_report, generate_weekly_report
from .qdrant_store import retrieve_vectors
from .report_writer import (
    ensure_visual_sections,
    extract_summary,
    fallback_japan_report,
    fallback_report,
    write_japan_report,
    write_report,
)


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_dt, end_dt


def load_articles(start: date, end: date) -> list[dict[str, Any]]:
    start_dt, end_dt = _window(start, end)
    rows = fetch_all(
        """
        SELECT *
        FROM articles
        WHERE COALESCE(published_at, fetched_at) >= %s
          AND COALESCE(published_at, fetched_at) < %s
        ORDER BY COALESCE(published_at, fetched_at), id
        """,
        (start_dt, end_dt),
    )
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    topics = fetch_all(
        "SELECT article_id, topic_name FROM article_topics WHERE article_id = ANY(%s)",
        (ids,),
    )
    entities = fetch_all(
        """
        SELECT article_id, entity_text, entity_type, normalized_name, confidence
        FROM article_entities
        WHERE article_id = ANY(%s)
        """,
        (ids,),
    )
    topics_by_article: dict[int, list[str]] = {}
    for topic in topics:
        topics_by_article.setdefault(topic["article_id"], []).append(topic["topic_name"])
    entities_by_article: dict[int, list[dict[str, Any]]] = {}
    for entity in entities:
        entities_by_article.setdefault(entity["article_id"], []).append(entity)
    for row in rows:
        row["topics"] = topics_by_article.get(row["id"], [])
        row["entities"] = entities_by_article.get(row["id"], [])
    return rows


def historical_counts(start: date) -> dict[str, Any]:
    history_start = start - timedelta(days=28)
    history_end = start - timedelta(days=1)
    articles = load_articles(history_start, history_end)
    topic_counts = Counter(topic for article in articles for topic in article.get("topics", []))
    entity_counts = Counter(entity["normalized_name"] for article in articles for entity in article.get("entities", []))
    return {
        "article_count": len(articles),
        "topic_counts": dict(topic_counts),
        "entity_counts": dict(entity_counts),
        "history_start": history_start.isoformat(),
        "history_end": history_end.isoformat(),
    }


def build_topic_changes(current_articles: list[dict[str, Any]], history: dict[str, Any]) -> list[dict[str, Any]]:
    current = Counter(topic for article in current_articles for topic in article.get("topics", []))
    history_topics = history.get("topic_counts", {})
    names = sorted(set(current) | set(history_topics))
    changes = []
    for name in names:
        current_count = current.get(name, 0)
        past_avg = round(history_topics.get(name, 0) / 4, 2)
        if past_avg == 0 and current_count > 0:
            label = "升温"
            basis = "过去 4 周未见或样本极少，本周出现报道"
        elif past_avg == 0:
            label = "不足以判断"
            basis = "本周与历史窗口均缺少样本"
        elif current_count >= past_avg * 1.4:
            label = "升温"
            basis = "本周数量高于过去 4 周周均 40% 以上"
        elif current_count <= past_avg * 0.6:
            label = "降温"
            basis = "本周数量低于过去 4 周周均 40% 以上"
        else:
            label = "平稳"
            basis = "本周数量接近过去 4 周周均"
        changes.append(
            {
                "topic_name": name,
                "current_count": current_count,
                "past_weekly_avg": past_avg,
                "change_label": label,
                "basis": basis,
            }
        )
    return sorted(changes, key=lambda item: (-item["current_count"], item["topic_name"]))


def build_entity_changes(current_articles: list[dict[str, Any]], history: dict[str, Any]) -> list[dict[str, Any]]:
    current = Counter(entity["normalized_name"] for article in current_articles for entity in article.get("entities", []))
    history_entities = history.get("entity_counts", {})
    names = sorted(set(current) | set(history_entities))
    changes = []
    for name in names:
        current_count = current.get(name, 0)
        past_avg = round(history_entities.get(name, 0) / 4, 2)
        if past_avg == 0 and current_count > 0:
            label = "升温"
        elif past_avg == 0:
            label = "不足以判断"
        elif current_count >= past_avg * 1.4:
            label = "升温"
        elif current_count <= past_avg * 0.6:
            label = "降温"
        else:
            label = "平稳"
        changes.append(
            {
                "entity_name": name,
                "current_count": current_count,
                "past_weekly_avg": past_avg,
                "change_label": label,
            }
        )
    return sorted(changes, key=lambda item: (-item["current_count"], item["entity_name"]))[:30]


def political_temperature(topic_changes: list[dict[str, Any]], current_count: int, history_count: int) -> str:
    if history_count < 5:
        return "不足以判断"
    past_avg = history_count / 4
    hot_topics = sum(1 for item in topic_changes if item["change_label"] == "升温" and item["current_count"] >= 2)
    if current_count >= past_avg * 1.4 or hot_topics >= 3:
        return "升温"
    if current_count <= past_avg * 0.6:
        return "降温"
    return "平稳"


async def build_structured_input(start: date, end: date, articles: list[dict[str, Any]]) -> dict[str, Any]:
    vectors = await retrieve_vectors([int(article["id"]) for article in articles])
    clusters = cluster_articles_by_vectors(articles, vectors) if vectors else cluster_articles(articles)
    history = historical_counts(start)
    topic_changes = build_topic_changes(articles, history)
    entity_changes = build_entity_changes(articles, history)
    temperature = political_temperature(topic_changes, len(articles), history["article_count"])
    source_list = [
        {
            "title": article["title"],
            "url": article["url"],
            "source_name": article["source_name"],
            "source_type": article["source_type"],
            "published_at": article["published_at"].isoformat() if article.get("published_at") else None,
        }
        for article in articles[:100]
    ]
    official_claims = [
        {"cluster_title": cluster["representative_title"], "official_sources": cluster["official_sources"]}
        for cluster in clusters
        if cluster.get("official_sources")
    ]
    media_divergence = [
        {
            "cluster_title": cluster["representative_title"],
            "source_types": cluster.get("source_types", []),
            "note": "不同来源类型均仅代表公开来源分类；具体立场差异需以正文和来源背景交叉验证。",
        }
        for cluster in clusters
        if len(cluster.get("source_types", [])) >= 2
    ]
    discussion_signals = load_discussion_signals(start, end)
    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "event_clusters": clusters[:20],
        "topic_changes": topic_changes,
        "entity_changes": entity_changes,
        "source_list": source_list,
        "official_claims": official_claims,
        "media_divergence": media_divergence,
        "discussion_signals": discussion_signals,
        "historical_comparison": {
            **history,
            "current_article_count": len(articles),
            "political_temperature": temperature,
            "cluster_method": "embedding_qdrant" if vectors else "local_similarity",
            "qdrant_vector_count": len(vectors),
        },
        "methodology_notes": [
            "仅处理公开来源；不登录私人账号，不绕过验证码，不抓取私密内容。",
            "事实、来源观点、模型推断必须分开表述。",
            "没有证据或来源不足时，结论必须写为不足以判断。",
            "第一版实体识别和主题分类为规则匹配；聚类为标题和正文相似度。",
        ],
    }


async def generate_weekly_markdown(start: date, end: date, structured: dict[str, Any]) -> str:
    try:
        markdown = await generate_weekly_report(structured)
        if not markdown:
            markdown = fallback_report(start.isoformat(), end.isoformat(), structured, "未配置 DEEPSEEK_API_KEY")
    except Exception as exc:
        markdown = fallback_report(start.isoformat(), end.isoformat(), structured, f"大模型调用失败：{exc}")
    return ensure_visual_sections(markdown, structured)


def save_weekly_report_record(start: date, end: date, structured: dict[str, Any], markdown: str) -> dict[str, str]:
    path = write_report(markdown, start.isoformat())
    response_path = f"reports/{path.name}"
    summary = extract_summary(markdown)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO weekly_reports
                  (week_start, week_end, markdown_path, executive_summary, political_temperature)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    start,
                    end,
                    response_path,
                    summary,
                    structured["historical_comparison"]["political_temperature"],
                ),
            )
    return {"report_path": response_path, "summary": summary}


async def analyze_weekly(start: date, end: date) -> dict[str, str]:
    articles = load_articles(start, end)
    structured = await build_structured_input(start, end, articles)
    markdown = await generate_weekly_markdown(start, end, structured)
    return save_weekly_report_record(start, end, structured, markdown)


# ═══════════════════════════════════════════════════════
# Japan anti-war weekly analysis
# ═══════════════════════════════════════════════════════

def load_japan_news(start: date, end: date) -> list[dict[str, Any]]:
    start_dt, end_dt = _window(start, end)
    rows = fetch_all(
        """
        SELECT *
        FROM japan_news
        WHERE COALESCE(published_at, fetched_at) >= %s
          AND COALESCE(published_at, fetched_at) < %s
        ORDER BY COALESCE(published_at, fetched_at), id
        """,
        (start_dt, end_dt),
    )
    return rows


def load_japan_social_posts(start: date, end: date) -> list[dict[str, Any]]:
    start_dt, end_dt = _window(start, end)
    rows = fetch_all(
        """
        SELECT p.*, a.verified AS account_verified, a.is_candidate AS account_is_candidate,
               a.username AS account_username, a.display_name AS account_display_name
        FROM japan_social_posts p
        LEFT JOIN japan_social_accounts a ON p.account_id = a.id
        WHERE COALESCE(p.published_at, p.fetched_at) >= %s
          AND COALESCE(p.published_at, p.fetched_at) < %s
        ORDER BY COALESCE(p.published_at, p.fetched_at), p.id
        """,
        (start_dt, end_dt),
    )
    return rows


def load_japan_events(start: date, end: date) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT *
        FROM japan_antiwar_events
        WHERE event_date >= %s AND event_date <= %s
        ORDER BY event_date, id
        """,
        (start, end),
    )
    return rows


def build_japan_topic_changes(news_articles: list[dict[str, Any]], history: dict[str, Any]) -> list[dict[str, Any]]:
    current: Counter[str] = Counter()
    for article in news_articles:
        category = article.get("category", "general")
        if category:
            current[category] += 1
    history_topics = history.get("topic_counts", {})
    names = sorted(set(current) | set(history_topics))
    changes = []
    for name in names:
        current_count = current.get(name, 0)
        past_avg = round(history_topics.get(name, 0) / 4, 2) if history_topics.get(name, 0) > 0 else 0
        if past_avg == 0 and current_count > 0:
            label = "升温"
            basis = "过去 4 周未见或样本极少，本周出现报道"
        elif past_avg == 0:
            label = "不足以判断"
            basis = "本周与历史窗口均缺少样本"
        elif current_count >= past_avg * 1.4:
            label = "升温"
            basis = "本周数量高于过去 4 周周均 40% 以上"
        elif current_count <= past_avg * 0.6:
            label = "降温"
            basis = "本周数量低于过去 4 周周均 40% 以上"
        else:
            label = "平稳"
            basis = "本周数量接近过去 4 周周均"
        changes.append(
            {
                "topic_name": name,
                "current_count": current_count,
                "past_weekly_avg": past_avg,
                "change_label": label,
                "basis": basis,
            }
        )
    return sorted(changes, key=lambda item: (-item["current_count"], item["topic_name"]))


def build_japan_entity_changes(news_articles: list[dict[str, Any]], history: dict[str, Any]) -> list[dict[str, Any]]:
    current: Counter[str] = Counter()
    for article in news_articles:
        source = article.get("source_name", "")
        if source:
            current[source] += 1
    history_entities = history.get("entity_counts", {})
    names = sorted(set(current) | set(history_entities))
    changes = []
    for name in names:
        current_count = current.get(name, 0)
        past_avg = round(history_entities.get(name, 0) / 4, 2) if history_entities.get(name, 0) > 0 else 0
        if past_avg == 0 and current_count > 0:
            label = "升温"
        elif past_avg == 0:
            label = "不足以判断"
        elif current_count >= past_avg * 1.4:
            label = "升温"
        elif current_count <= past_avg * 0.6:
            label = "降温"
        else:
            label = "平稳"
        changes.append(
            {
                "entity_name": name,
                "current_count": current_count,
                "past_weekly_avg": past_avg,
                "change_label": label,
            }
        )
    return sorted(changes, key=lambda item: (-item["current_count"], item["entity_name"]))[:30]


def japan_historical_counts(start: date) -> dict[str, Any]:
    history_start = start - timedelta(days=28)
    history_end = start - timedelta(days=1)
    articles = load_japan_news(history_start, history_end)
    topic_counts: Counter[str] = Counter(a.get("category", "general") for a in articles)
    entity_counts: Counter[str] = Counter(a.get("source_name", "") for a in articles)
    return {
        "article_count": len(articles),
        "topic_counts": dict(topic_counts),
        "entity_counts": dict(entity_counts),
        "history_start": history_start.isoformat(),
        "history_end": history_end.isoformat(),
    }


async def build_japan_structured_input(start: date, end: date) -> dict[str, Any]:
    news_articles = load_japan_news(start, end)
    social_posts = load_japan_social_posts(start, end)
    events = load_japan_events(start, end)
    history = japan_historical_counts(start)
    topic_changes = build_japan_topic_changes(news_articles, history)
    entity_changes = build_japan_entity_changes(news_articles, history)

    source_list = [
        {
            "title": article["title"],
            "url": article["url"],
            "source_name": article["source_name"],
            "source_type": article["source_type"],
            "published_at": article["published_at"].isoformat() if article.get("published_at") else None,
        }
        for article in news_articles[:100]
    ]

    # Separate verified and candidate social accounts for analysis
    verified_posts = [p for p in social_posts if p.get("account_verified") and not p.get("account_is_candidate")]
    candidate_posts = [p for p in social_posts if p.get("account_is_candidate")]

    social_signals = {
        "total_posts": len(social_posts),
        "verified_post_count": len(verified_posts),
        "candidate_post_count": len(candidate_posts),
        "verified_accounts": list(set(p.get("account_username") for p in verified_posts)),
        "candidate_accounts": list(set(p.get("account_username") for p in candidate_posts)),
        "top_verified_posts": [
            {
                "platform": p.get("platform"),
                "account": p.get("account_username"),
                "content": (p.get("content_text") or "")[:200],
                "url": p.get("url"),
                "published_at": p.get("published_at"),
                "engagement": p.get("engagement_count", 0),
            }
            for p in sorted(verified_posts, key=lambda x: x.get("engagement_count", 0) or 0, reverse=True)[:10]
        ],
        "candidate_posts_summary": (
            f"未検証アカウント（candidate）{len(candidate_posts)} 件の投稿は素材リストのみに記載。"
            "主要な結論には含まれていません。" if candidate_posts else "candidate 投稿なし。"
        ),
    }

    event_list = [
        {
            "title": e.get("title"),
            "description": e.get("description"),
            "event_date": e.get("event_date", "").isoformat() if hasattr(e.get("event_date", ""), "isoformat") else str(e.get("event_date", "")),
            "location": e.get("location"),
            "organizer": e.get("organizer"),
            "category": e.get("category"),
            "verified": e.get("verified", False),
            "source_url": e.get("source_url"),
        }
        for e in events[:50]
    ]

    clusters = cluster_articles(news_articles) if news_articles else []

    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "event_clusters": clusters[:20],
        "topic_changes": topic_changes,
        "entity_changes": entity_changes,
        "source_list": source_list,
        "official_claims": [],
        "media_divergence": [],
        "social_signals": social_signals,
        "antiwar_events": event_list,
        "discussion_signals": {},
        "historical_comparison": {
            **history,
            "current_article_count": len(news_articles),
            "political_temperature": "不足以判断",
            "current_event_count": len(events),
        },
        "methodology_notes": [
            "公開情報のみを処理。プライベートアカウント、認証回避、非公開コンテンツの取得は行いません。",
            "事実、情報源の見解、モデルの推論は明確に区別します。",
            "証拠や情報源が不十分な場合、結論は「不足以判断」とします。",
            "candidate（未検証）アカウントの投稿は主要な結論に含めません。",
            "verified アカウントのみ evidence_url が必須です。",
        ],
    }


async def generate_japan_weekly_markdown(start: date, end: date, structured: dict[str, Any]) -> str:
    try:
        markdown = await generate_japan_report(structured)
        if not markdown:
            markdown = fallback_japan_report(start.isoformat(), end.isoformat(), structured, "未配置 DEEPSEEK_API_KEY")
    except Exception as exc:
        markdown = fallback_japan_report(start.isoformat(), end.isoformat(), structured, f"大模型调用失败：{exc}")
    return ensure_visual_sections(markdown, structured)


def save_japan_report_record(start: date, end: date, structured: dict[str, Any], markdown: str) -> dict[str, str]:
    path = write_japan_report(markdown, start.isoformat())
    response_path = f"reports/{path.name}"
    summary = extract_summary(markdown)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO japan_reports
                  (report_type, title, content_path, summary, period_start, period_end)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    "weekly",
                    f"日本反戦態勢週報 {start.isoformat()} ～ {end.isoformat()}",
                    response_path,
                    summary,
                    start,
                    end,
                ),
            )
    return {"report_path": response_path, "summary": summary}


async def analyze_japan_weekly(start: date, end: date) -> dict[str, str]:
    structured = await build_japan_structured_input(start, end)
    markdown = await generate_japan_weekly_markdown(start, end, structured)
    return save_japan_report_record(start, end, structured, markdown)
