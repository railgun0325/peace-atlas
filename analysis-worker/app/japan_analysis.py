from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .db import fetch_all, get_conn
from .japan_geo import normalize_or_original, search as geo_search
from .japan_social_accounts import account_summary

BASE_DIR = Path(__file__).resolve().parents[1]
PROMPT_PATH = BASE_DIR.parent / "prompts" / "japan_antiwar_analysis_prompt.md"


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return start_dt, end_dt


def load_japan_prompt() -> str:
    candidates = [
        PROMPT_PATH,
        BASE_DIR / "prompts" / "japan_antiwar_analysis_prompt.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return "请基于结构化输入生成中立、证据分层的日本反战和平运动公开情报周报。"


def load_japan_articles(start: date, end: date) -> list[dict[str, Any]]:
    start_dt, end_dt = _window(start, end)
    rows = fetch_all(
        """
        SELECT *
        FROM articles
        WHERE COALESCE(published_at, fetched_at) >= %s
          AND COALESCE(published_at, fetched_at) < %s
          AND (
            raw_text ILIKE '%japan%' OR raw_text ILIKE '%日本%' OR
            raw_text ILIKE '%平和%' OR raw_text ILIKE '%反戦%' OR
            raw_text ILIKE '%anti-war%' OR raw_text ILIKE '%antiwar%' OR
            raw_text ILIKE '%沖縄%' OR raw_text ILIKE '%Okinawa%' OR
            raw_text ILIKE '%憲法%' OR raw_text ILIKE '%九条%' OR
            raw_text ILIKE '%原爆%' OR raw_text ILIKE '%hibakusha%' OR
            raw_text ILIKE '%自衛隊%' OR raw_text ILIKE '%SDF%'
          )
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


def japan_historical_counts(start: date) -> dict[str, Any]:
    history_start = start - timedelta(days=28)
    history_end = start - timedelta(days=1)
    articles = load_japan_articles(history_start, history_end)
    topic_counts = Counter(topic for article in articles for topic in article.get("topics", []))
    entity_counts = Counter(
        entity["normalized_name"] for article in articles for entity in article.get("entities", [])
    )
    return {
        "article_count": len(articles),
        "topic_counts": dict(topic_counts),
        "entity_counts": dict(entity_counts),
        "history_start": history_start.isoformat(),
        "history_end": history_end.isoformat(),
    }


def build_japan_topic_changes(
    current_articles: list[dict[str, Any]], history: dict[str, Any]
) -> list[dict[str, Any]]:
    current = Counter(topic for article in current_articles for topic in article.get("topics", []))
    history_topics = history.get("topic_counts", {})
    names = sorted(set(current) | set(history_topics))
    changes = []
    for name in names:
        current_count = current.get(name, 0)
        past_avg = round(history_topics.get(name, 0) / 4, 2)
        if past_avg == 0 and current_count > 0:
            label = "新出"
        elif past_avg == 0:
            label = "样本不足"
        elif current_count >= past_avg * 1.4:
            label = "増加"
        elif current_count <= past_avg * 0.6:
            label = "減少"
        else:
            label = "横ばい"
        changes.append({
            "topic_name": name,
            "current_count": current_count,
            "past_weekly_avg": past_avg,
            "change_label": label,
        })
    return sorted(changes, key=lambda item: (-item["current_count"], item["topic_name"]))


def build_japan_entity_changes(
    current_articles: list[dict[str, Any]], history: dict[str, Any]
) -> list[dict[str, Any]]:
    current = Counter(
        entity["normalized_name"] for article in current_articles for entity in article.get("entities", [])
    )
    history_entities = history.get("entity_counts", {})
    names = sorted(set(current) | set(history_entities))
    changes = []
    for name in names:
        current_count = current.get(name, 0)
        past_avg = round(history_entities.get(name, 0) / 4, 2)
        if past_avg == 0 and current_count > 0:
            label = "新出"
        elif past_avg == 0:
            label = "样本不足"
        elif current_count >= past_avg * 1.4:
            label = "増加"
        elif current_count <= past_avg * 0.6:
            label = "減少"
        else:
            label = "横ばい"
        changes.append({
            "entity_name": name,
            "current_count": current_count,
            "past_weekly_avg": past_avg,
            "change_label": label,
        })
    return sorted(changes, key=lambda item: (-item["current_count"], item["entity_name"]))[:30]


def extract_geo_locations(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract and normalize geographic locations mentioned in articles."""
    location_counts: Counter[str] = Counter()
    for article in articles:
        text = f"{article.get('title', '')} {article.get('clean_text', '')}"
        locations = geo_search(text)
        for loc in locations:
            location_counts[loc["canonical_name"]] += 1
    return [
        {"location": loc, "count": count}
        for loc, count in location_counts.most_common(30)
    ]


async def build_japan_structured_input(
    start: date, end: date, articles: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the structured analysis input for Japan anti-war weekly report."""
    from .cluster import cluster_articles

    clusters = cluster_articles(articles) if articles else []
    history = japan_historical_counts(start)
    topic_changes = build_japan_topic_changes(articles, history)
    entity_changes = build_japan_entity_changes(articles, history)
    geo_locations = extract_geo_locations(articles)
    accounts = account_summary()

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

    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "event_clusters": clusters[:20],
        "topic_changes": topic_changes,
        "entity_changes": entity_changes,
        "geo_locations": geo_locations,
        "source_list": source_list,
        "account_summary": accounts,
        "historical_comparison": {
            **history,
            "current_article_count": len(articles),
        },
        "methodology_notes": [
            "公開情報のみを処理。ログイン不要、CAPTCHA回避なし。",
            "事実・出所評価・モデル推論を明示的に分離。",
            "情報が不足する場合は「不足」と明記し、推測で補わない。",
            "SNS感情分析はルールベースの暫定値であり、事実確認には使用しない。",
            "アカウントのstatus（verified/seed/candidate）は検証状態を示す。candidateは未検証であり信頼性評価に使用しない。",
            "RSSHub経由の情報はルーティング可否に依存し、取得できない場合がある。",
        ],
    }


async def generate_japan_report_markdown(
    structured: dict[str, Any],
) -> str:
    """Generate a Markdown report via LLM, falling back to a local summary."""
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return _fallback_japan_report(structured, "DEEPSEEK_API_KEY が未設定です。")

    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    system_prompt = load_japan_prompt()
    user_payload = json.dumps(structured, ensure_ascii=False, indent=2, default=str)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "以下はクレンジング、重複排除、クラスタリング済みの日本反戦・平和運動に関する"
                    "構造化された公開情報です。システム指示に従いMarkdown週報を生成してください。"
                    "情報源を捏造しないでください。\n\n"
                    f"{user_payload}"
                ),
            },
        ],
        "temperature": 0.2,
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout_seconds = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "0"))
    timeout = (
        httpx.Timeout(None, connect=30.0, read=None, write=30.0, pool=None)
        if timeout_seconds <= 0
        else httpx.Timeout(timeout_seconds, connect=30.0, read=timeout_seconds, write=30.0)
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            endpoint = base_url
            if not endpoint.endswith("/chat/completions"):
                endpoint = f"{endpoint}/chat/completions"
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        return _fallback_japan_report(structured, f"LLM呼び出し失敗: {exc}")


def save_japan_report(start: date, end: date, structured: dict[str, Any], markdown: str) -> dict[str, str]:
    from .report_writer import reports_dir

    dir_path = reports_dir()
    filename = f"japan-peace-weekly-{start.isoformat()}.md"
    path = dir_path / filename
    path.write_text(markdown, encoding="utf-8")
    response_path = f"reports/{filename}"

    summary_lines = [line.strip("- ").strip() for line in markdown.splitlines() if line.strip() and not line.startswith("#")]
    summary = " ".join(summary_lines[:5])[:600]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO weekly_reports
                  (week_start, week_end, markdown_path, executive_summary, political_temperature)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (start, end, response_path, summary, "データ不足"),
            )
    return {"report_path": response_path, "summary": summary}


def _fallback_japan_report(structured: dict[str, Any], reason: str) -> str:
    clusters = structured.get("event_clusters", [])
    topic_changes = structured.get("topic_changes", [])
    geo = structured.get("geo_locations", [])
    accounts = structured.get("account_summary", {})
    sources = structured.get("source_list", [])

    lines: list[str] = [
        f"# 日本反戦・平和運動週報: {structured.get('week_start', '')} 〜 {structured.get('week_end', '')}",
        "",
        "## 1. 要約",
        f"- 本レポートはフォールバック（ローカル）生成です。理由: {reason}",
        f"- 今週の関連記事数: {structured.get('historical_comparison', {}).get('current_article_count', 0)}",
        "",
        "## 2. 主要トピック変化",
    ]

    if topic_changes:
        lines.append("| トピック | 今週 | 過去4週平均 | 変化 |")
        lines.append("|---|---:|---:|---|")
        for item in topic_changes[:15]:
            lines.append(f"| {item['topic_name']} | {item['current_count']} | {item['past_weekly_avg']} | {item['change_label']} |")
    else:
        lines.append("- トピックデータ不足。")

    lines.extend([
        "",
        "## 3. イベントクラスター",
    ])
    if clusters:
        for cluster in clusters[:8]:
            lines.append(f"- **{cluster.get('representative_title', '無題')}**: 記事数 {cluster.get('article_count', 0)}, ソース数 {cluster.get('source_count', 0)}")
    else:
        lines.append("- クラスター化可能なイベントは検出されませんでした。")

    lines.extend([
        "",
        "## 4. 地理的言及",
    ])
    if geo:
        for item in geo[:15]:
            lines.append(f"- {item['location']}: {item['count']}回")
    else:
        lines.append("- 地理情報抽出なし。")

    lines.extend([
        "",
        "## 5. ソーシャルメディアアカウント動向",
    ])
    if accounts:
        lines.append(f"- 総アカウント数: {accounts.get('total_accounts', 0)}")
        lines.append(f"- 検証済み: {accounts.get('verified_count', 0)}")
        lines.append(f"- 候補: {accounts.get('candidate_count', 0)}")
        by_org = accounts.get("by_organization", {})
        if by_org:
            lines.append("")
            lines.append("### 組織別（各グループ最大3件）")
            for org, acc_list in sorted(by_org.items()):
                lines.append(f"- **{org}**: {', '.join(a.get('handle', '') for a in acc_list)}")
        top = accounts.get("top_active", [])
        if top:
            lines.append("")
            lines.append("### アクティブアカウント上位")
            for i, acc in enumerate(top[:10], 1):
                lines.append(f"{i}. {acc.get('handle', '')} ({acc.get('platform', '')})")
        candidates = accounts.get("candidate_samples", [])
        if candidates:
            lines.append("")
            lines.append("### 候補アカウント（検証待ち、最大3件）")
            for acc in candidates:
                evidence = acc.get("evidence_url", "") or "証拠URLなし"
                lines.append(f"- {acc.get('handle', '')} ({acc.get('platform', '')}) — 発見元: {evidence}")

    lines.extend([
        "",
        "## 6. 情報源",
    ])
    if sources:
        for source in sources[:30]:
            lines.append(f"- {source['source_name']}: [{source['title']}]({source['url']})")
    else:
        lines.append("- 情報源なし。")

    lines.extend([
        "",
        "## 7. 来週の注目点",
        "- 憲法改正・護憲関連の動き",
        "- 沖縄基地問題の進展",
        "- 核兵器廃絶関連の国際会議・声明",
        "- 日米安全保障協議の動向",
        "",
        "## 8. リスクと不確実性",
        "- 情報不足の領域: 複数ソースで確認されていない情報は観察継続。",
        "- 単一ソースリスク: 一つのみの報道や声明に基づく判断は回避。",
        "- モデル推論の限界: フォールバックレポートはルールベースであり、AI推論を含まない。",
        "",
        "## 9. 方法論ノート",
    ])
    for note in structured.get("methodology_notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines)


def fallback_japan_report(start_date: str, end_date: str, structured: dict[str, Any], reason: str) -> str:
    """Compatibility wrapper used by tests and older API code."""
    return _fallback_japan_report({**structured, "week_start": start_date, "week_end": end_date}, reason)
