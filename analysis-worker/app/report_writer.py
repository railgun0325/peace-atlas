from __future__ import annotations

import os
import html
from pathlib import Path
from typing import Any

from .sentiment import SENTIMENT_DISPLAY, SENTIMENT_ORDER, sentiment_profile_from_counts


BASE_DIR = Path(__file__).resolve().parents[1]
VISUAL_MARKER_START = "<!-- jai-visual-signals-start -->"
VISUAL_MARKER_END = "<!-- jai-visual-signals-end -->"


def reports_dir() -> Path:
    configured = os.getenv("REPORTS_DIR")
    if configured:
        path = Path(configured)
    else:
        path = BASE_DIR / "reports"
        if not path.exists():
            path = BASE_DIR.parent / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def report_filename(start_date: str) -> str:
    return f"japan-antiwar-weekly-{start_date}.md"


def write_report(markdown: str, start_date: str) -> Path:
    path = reports_dir() / report_filename(start_date)
    path.write_text(markdown, encoding="utf-8")
    return path


def extract_summary(markdown: str, max_chars: int = 600) -> str:
    lines = [line.strip("- ").strip() for line in markdown.splitlines() if line.strip()]
    useful = [line for line in lines if not line.startswith("#")]
    return " ".join(useful[:5])[:max_chars]


def fallback_report(start_date: str, end_date: str, structured: dict[str, Any], reason: str) -> str:
    clusters = structured.get("event_clusters", [])
    topic_changes = structured.get("topic_changes", [])
    entity_changes = structured.get("entity_changes", [])
    source_list = structured.get("source_list", [])
    temperature = structured.get("historical_comparison", {}).get("political_temperature", "不足以判断")
    top_judgments = _top_judgments(clusters, topic_changes)
    return "\n".join(
        [
            f"# 日本反戦平和運動公開情報週報：{start_date} 至 {end_date}",
            "",
            "## 1. 一页纸结论",
            f"- 本报告使用本地 fallback 生成：{reason}",
            f"- 本周态势评估：{temperature or '不足以判断'}。该判断来自本周与过去 4 周主题数量对比；若历史样本不足，则不足以判断。",
            *top_judgments,
            "",
            "## 2. 本周反戦關鍵事件时间线",
            *_timeline(clusters),
            "",
            "### 事件簇表",
            *_event_cluster_table(clusters),
            "",
            "## 3. 重大事件深度分析",
            *_event_sections(clusters),
            "",
            "## 4. 主题趋势变化",
            "### 主题升温/降温表",
            *_topic_table(topic_changes),
            "",
            *_topic_lines(topic_changes),
            "",
            "## 5. 政党与机构互动",
            "### 实体出现频次表",
            *_entity_table(entity_changes),
            "",
            *_japan_actor_lines(entity_changes),
            "",
            "## 6. 憲法第九条・自衛隊・在日米軍専章",
            *_japan_chapter_lines(clusters, ["憲法9条", "憲法改正", "自衛隊", "在日米軍", "沖縄", "安保", "平和"]),
            "",
            "## 7. 媒体与叙事差异",
            "### 来源覆盖情况",
            *_source_coverage_table(source_list),
            "",
            *_media_divergence_lines(structured.get("media_divergence", [])),
            "",
            "## 8. 公众讨论与情绪信号",
            *_discussion_signal_lines(structured.get("discussion_signals", {})),
            "",
            "## 8.1 公開SNSアカウント概览",
            *_japan_account_summary_lines(structured.get("account_summary", {})),
            "",
            *_visual_signal_lines(structured),
            "",
            "### 证据等级说明",
            "- A：多个可靠来源相互印证。",
            "- B：有可靠来源但仍需观察。",
            "- C：主要来自单一来源或评论推断。",
            "- 来源背景备注只用于提示交叉验证方向，不能直接作为事实判断依据。",
            "",
            "## 9. 下周观察清单",
            "- 是否有官方来源补充确认本周争议事件。",
            "- 国会、外務省、防衛省、内閣府是否发布后续说明。",
            "- 同一事件是否获得通讯社、不同立场媒体与国际媒体交叉报道。",
            "",
            "## 10. 风险与不确定性",
            "- 信息不足之处：未被多个来源交叉确认的事件均需继续观察。",
            "- 单一来源风险：证据等级 C 的判断主要来自单一来源或评论推断。",
            "- 需要等待官方确认的内容：未出现官方链接时，一律标记为不足以判断。",
            "- 模型推断的局限：本地 fallback 不调用大模型，只根据规则统计生成，无法替代人工研判。",
            "",
            "## 11. 附录：来源列表",
            *_source_lines(source_list),
            "",
        ]
    )


def ensure_visual_sections(markdown: str, structured: dict[str, Any]) -> str:
    if VISUAL_MARKER_START in markdown:
        return markdown
    visual = "\n".join(["## 可视化信号补充", *_visual_signal_lines(structured)])
    return f"{markdown.rstrip()}\n\n{visual}\n"


def _top_judgments(clusters: list[dict[str, Any]], topic_changes: list[dict[str, Any]]) -> list[str]:
    lines = []
    for cluster in clusters[:5]:
        lines.append(
            f"- {cluster['representative_title']}（证据等级 {cluster.get('evidence_grade', 'C')}；来源数 {cluster.get('source_count', 0)}）"
        )
    if not lines:
        lines.append("- 本周样本不足，重大政治判断不足以判断。（证据等级 C）")
    if topic_changes:
        hottest = topic_changes[0]
        lines.append(
            f"- 主题热度最高：{hottest['topic_name']}，本周 {hottest['current_count']} 篇；延续性不足以判断。（证据等级 C）"
        )
    return lines


def _timeline(clusters: list[dict[str, Any]]) -> list[str]:
    if not clusters:
        return ["- 本周无可聚类事件，或样本不足。"]
    lines = []
    for cluster in clusters[:12]:
        date = (cluster.get("first_seen") or "")[:10] or "日期不足"
        icon = _cluster_icon(cluster)
        lines.append(f"- {icon} **{date}**：{cluster['representative_title']}（{cluster.get('source_count', 0)} 个来源）")
    return lines


def _event_sections(clusters: list[dict[str, Any]]) -> list[str]:
    if not clusters:
        return ["### 样本不足", "", "- 事件概述：不足以判断。", "- 来源链接：无。"]
    lines: list[str] = []
    for cluster in clusters[:8]:
        lines.extend(
            [
                f"### {cluster['representative_title']}",
                f"- 事件概述：该事件簇包含 {cluster.get('article_count', 0)} 篇文章、{cluster.get('source_count', 0)} 个来源。",
                f"- 涉及行为体：{', '.join(cluster.get('entities') or []) or '不足以判断'}。",
                f"- 官方表态：{_official_claim(cluster)}",
                f"- 主要媒体报道角度：{', '.join(cluster.get('source_types') or []) or '不足以判断'}。",
                "- 不同立场来源的差异：第一版仅按来源类型分组，细粒度立场差异需要大模型或人工补充。",
                "- 可能的政治影响：模型推断不足；需结合后续官方回应与跨媒体报道。",
                "- 后续观察点：是否出现官方确认、政党回应、立法院议程或国际媒体持续报道。",
                "- 来源链接：",
                *_cluster_source_lines(cluster),
                "",
            ]
        )
    return lines


def _event_cluster_table(clusters: list[dict[str, Any]]) -> list[str]:
    if not clusters:
        return ["| 事件簇 | 来源数 | 主题 | 实体 | 证据 | 方法 |", "|---|---:|---|---|---|---|", "| 不足以判断 | 0 | - | - | C | - |"]
    lines = ["| 事件簇 | 来源数 | 主题 | 实体 | 证据 | 方法 |", "|---|---:|---|---|---|---|"]
    for cluster in clusters[:20]:
        lines.append(
            "| {title} | {source_count} | {topics} | {entities} | {grade} | {method} |".format(
                title=_cell(cluster.get("representative_title", "")),
                source_count=cluster.get("source_count", 0),
                topics=_cell(", ".join(cluster.get("dominant_topics") or []) or "不足以判断"),
                entities=_cell(", ".join(cluster.get("entities") or []) or "不足以判断"),
                grade=cluster.get("evidence_grade", "C"),
                method=cluster.get("cluster_method", "local_similarity"),
            )
        )
    return lines


def _official_claim(cluster: dict[str, Any]) -> str:
    official = cluster.get("official_sources") or []
    if not official:
        return "未发现官方来源，官方说法不足以判断。"
    return "；".join(f"{item['source_name']}《{item['title']}》" for item in official[:3])


def _cluster_source_lines(cluster: dict[str, Any]) -> list[str]:
    return [
        f"  - {source['source_name']}：[{source['title']}]({source['url']})"
        for source in (cluster.get("sources") or [])[:10]
    ] or ["  - 无"]


def _topic_lines(topic_changes: list[dict[str, Any]]) -> list[str]:
    if not topic_changes:
        return ["- 主题样本不足以判断。"]
    return [
        (
            f"- {item['topic_name']}：本周 {item['current_count']} 篇，过去 4 周周均 "
            f"{item['past_weekly_avg']} 篇，变化 {item['change_label']}；依据：{item['basis']}。"
        )
        for item in topic_changes
    ]


def _topic_table(topic_changes: list[dict[str, Any]]) -> list[str]:
    if not topic_changes:
        return ["| 主题 | 本周 | 过去4周周均 | 变化 | 依据 |", "|---|---:|---:|---|---|", "| 不足以判断 | 0 | 0 | 不足以判断 | 样本不足 |"]
    lines = ["| 主题 | 本周 | 过去4周周均 | 变化 | 依据 |", "|---|---:|---:|---|---|"]
    for item in topic_changes[:20]:
        lines.append(
            f"| {_cell(item['topic_name'])} | {item['current_count']} | {item['past_weekly_avg']} | {item['change_label']} | {_cell(item['basis'])} |"
        )
    return lines


def _entity_table(entity_changes: list[dict[str, Any]]) -> list[str]:
    if not entity_changes:
        return ["| 实体 | 本周频次 | 过去4周周均 | 变化 |", "|---|---:|---:|---|", "| 不足以判断 | 0 | 0 | 不足以判断 |"]
    lines = ["| 实体 | 本周频次 | 过去4周周均 | 变化 |", "|---|---:|---:|---|"]
    for item in entity_changes[:30]:
        lines.append(f"| {_cell(item['entity_name'])} | {item['current_count']} | {item['past_weekly_avg']} | {item['change_label']} |")
    return lines


def _actor_lines(entity_changes: list[dict[str, Any]]) -> list[str]:
    required = ["自民党", "立憲民主党", "公明党", "共産党", "内閣府", "国会", "防衛省"]
    lines = []
    by_name = {item["entity_name"]: item for item in entity_changes}
    for name in required:
        item = by_name.get(name)
        if item:
            lines.append(f"- {name}：本周出现 {item['current_count']} 次，变化 {item['change_label']}。")
        else:
            lines.append(f"- {name}：本周样本不足以判断。")
    return lines


def _chapter_lines(clusters: list[dict[str, Any]], chapter_topics: list[str]) -> list[str]:
    selected = [
        cluster
        for cluster in clusters
        if any(topic in chapter_topics for topic in cluster.get("dominant_topics", []))
    ]
    if not selected:
        return ["- 本周相关主题样本不足以判断。"]
    return [
        f"- {cluster['representative_title']}：主题 {', '.join(cluster.get('dominant_topics') or [])}，证据等级 {cluster.get('evidence_grade', 'C')}。"
        for cluster in selected[:8]
    ]


def _media_divergence_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- 媒体叙事差异样本不足以判断。"]
    return [
        f"- {item['cluster_title']}：来源类型 {', '.join(item.get('source_types') or [])}；说明：{item.get('note', '不足以判断')}。"
        for item in items[:8]
    ]


def _visual_signal_lines(structured: dict[str, Any]) -> list[str]:
    return [
        VISUAL_MARKER_START,
        "### 情绪时间对比",
        *_sentiment_visual_lines(structured.get("discussion_signals", {}) or {}),
        "",
        "### 图标事件时间线",
        *_timeline_visual_lines(structured.get("event_clusters", []) or []),
        VISUAL_MARKER_END,
    ]


def _sentiment_visual_lines(signals: dict[str, Any]) -> list[str]:
    recent = _sentiment_window_profile(signals.get("recent_sentiment"), fallback=signals, default_label="近期情绪")
    long_term = _sentiment_window_profile(signals.get("long_term_sentiment"), default_label="长期情绪")
    comparison = signals.get("sentiment_comparison") or {}
    lines = [
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:10px 0;">',
        *_sentiment_card_lines("近期情绪", recent),
        *_sentiment_card_lines("长期情绪", long_term),
        "</div>",
        _sentiment_legend_html(),
        f"- 对比判断：{_cell(comparison.get('label') or '不足以判断')}；依据：{_cell(comparison.get('basis') or '缺少足够近期或长期样本。')}",
        "- 注意：该图只展示公开讨论样本的情绪信号，不能作为事实证据。",
    ]
    return lines


def _sentiment_window_profile(profile: Any, fallback: dict[str, Any] | None = None, default_label: str = "情绪") -> dict[str, Any]:
    source = profile if isinstance(profile, dict) else {}
    fallback = fallback or {}
    counts = source.get("sentiment_counts") or fallback.get("sentiment_counts") or {}
    computed = sentiment_profile_from_counts(counts)
    percentages = source.get("sentiment_percentages") or fallback.get("sentiment_percentages") or computed["percentages"]
    window = source.get("window") or fallback.get("window") or {}
    return {
        "label": source.get("label") or default_label,
        "window": window,
        "post_count": source.get("post_count", fallback.get("post_count", 0)),
        "comment_count": source.get("comment_count", fallback.get("comment_count", 0)),
        "sentiment_sample_size": source.get("sentiment_sample_size") or fallback.get("sentiment_sample_size") or computed["sample_size"],
        "sentiment_counts": counts,
        "sentiment_percentages": percentages,
        "dominant_sentiment": source.get("dominant_sentiment") or fallback.get("dominant_sentiment") or computed["dominant_label"],
        "dominant_sentiment_name": source.get("dominant_sentiment_name") or fallback.get("dominant_sentiment_name") or computed["dominant_name"],
        "overall_attitude": source.get("overall_attitude") or fallback.get("overall_attitude") or computed["attitude_label"],
        "overall_attitude_icon": source.get("overall_attitude_icon") or fallback.get("overall_attitude_icon") or computed["icon"],
        "overall_attitude_color": source.get("overall_attitude_color") or fallback.get("overall_attitude_color") or computed["color"],
    }


def _sentiment_card_lines(title: str, profile: dict[str, Any]) -> list[str]:
    window = profile.get("window") or {}
    icon = _html_escape(profile.get("overall_attitude_icon") or "●")
    color = _html_escape(profile.get("overall_attitude_color") or "#64748b")
    attitude = _html_escape(profile.get("overall_attitude") or "不足以判断")
    sample_size = int(profile.get("sentiment_sample_size") or 0)
    post_count = int(profile.get("post_count") or 0)
    comment_count = int(profile.get("comment_count") or 0)
    start = _html_escape(window.get("start") or "时间不足")
    end = _html_escape(window.get("end") or "时间不足")
    return [
        '<div style="border:1px solid #d7dde8;border-radius:8px;padding:12px;background:#fbfcfe;">',
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"><span style="font-size:20px;color:{color};">{icon}</span><strong>{_html_escape(title)}</strong></div>',
        f'<div style="font-size:14px;margin-bottom:8px;">总体态度：<strong style="color:{color};">{attitude}</strong></div>',
        _sentiment_bar_html(profile),
        f'<div style="font-size:12px;color:#475569;margin-top:8px;">窗口：{start} 至 {end}；样本：{post_count} 篇帖子、{comment_count} 条评论、{sample_size} 条情绪记录。</div>',
        f'<div style="font-size:12px;color:#475569;margin-top:4px;">分布：{_html_escape(_sentiment_counts_text(profile))}</div>',
        "</div>",
    ]


def _sentiment_bar_html(profile: dict[str, Any]) -> str:
    percentages = profile.get("sentiment_percentages") or {}
    segments = []
    for label in SENTIMENT_ORDER:
        pct = float(percentages.get(label) or 0)
        if pct <= 0:
            continue
        display = SENTIMENT_DISPLAY.get(label, SENTIMENT_DISPLAY["neutral"])
        title = _html_escape(f"{display['name']} {pct}%")
        segments.append(f'<div title="{title}" style="width:{pct}%;background:{display["color"]};"></div>')
    if not segments:
        segments.append('<div title="样本不足" style="width:100%;background:#e5e7eb;"></div>')
    return f'<div style="display:flex;height:12px;overflow:hidden;border-radius:999px;background:#e5e7eb;">{"".join(segments)}</div>'


def _sentiment_counts_text(profile: dict[str, Any]) -> str:
    counts = profile.get("sentiment_counts") or {}
    percentages = profile.get("sentiment_percentages") or {}
    parts = []
    for label in SENTIMENT_ORDER:
        count = int(counts.get(label) or 0)
        if not count:
            continue
        display = SENTIMENT_DISPLAY.get(label, SENTIMENT_DISPLAY["neutral"])
        parts.append(f"{display['icon']} {display['name']} {count}（{percentages.get(label, 0)}%）")
    return "、".join(parts) if parts else "样本不足"


def _sentiment_legend_html() -> str:
    chips = []
    for label in SENTIMENT_ORDER:
        display = SENTIMENT_DISPLAY.get(label, SENTIMENT_DISPLAY["neutral"])
        chips.append(
            f'<span style="display:inline-block;margin:2px 8px 2px 0;color:{display["color"]};">{_html_escape(display["icon"])} {_html_escape(display["name"])}</span>'
        )
    return f'<div style="font-size:12px;color:#475569;margin:6px 0;">图例：{"".join(chips)}</div>'


def _timeline_visual_lines(clusters: list[dict[str, Any]]) -> list[str]:
    if not clusters:
        return ["- 样本不足，无法生成图标时间线。"]
    lines = ['<div style="border-left:3px solid #cbd5e1;margin:10px 0 4px 8px;padding-left:14px;">']
    for cluster in clusters[:12]:
        icon = _html_escape(_cluster_icon(cluster))
        date = _html_escape((cluster.get("first_seen") or "")[:10] or "日期不足")
        title = _html_escape(cluster.get("representative_title") or "标题不足")
        topics = _html_escape(", ".join(cluster.get("dominant_topics") or []) or "主题不足")
        grade = _html_escape(cluster.get("evidence_grade") or "C")
        source_count = int(cluster.get("source_count") or 0)
        lines.append(
            '<div style="position:relative;margin:0 0 12px 0;padding:2px 0 8px 0;">'
            f'<span style="position:absolute;left:-30px;background:white;">{icon}</span>'
            f'<strong>{date}</strong>：{title}'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px;">主题：{topics}；来源数：{source_count}；证据等级：{grade}</div>'
            "</div>"
        )
    lines.append("</div>")
    return lines


def _cluster_icon(cluster: dict[str, Any]) -> str:
    text = " ".join([cluster.get("representative_title") or "", *[str(topic) for topic in cluster.get("dominant_topics") or []]]).lower()
    rules = [
        (["国防", "國防", "military", "defense", "军购", "軍購"], "🛡️"),
        (["平和", "peace", "反戦", "antiwar", "pacifism", "constitution", "憲法", "article 9"], "🕊️"),
        (["沖縄", "okinawa", "基地", "base", "futenma", "henoko", "辺野古", "普天間"], "🏝️"),
        (["外交", "diplomacy", "国際", "国际"], "🌐"),
        (["国会", "legislature", "parliament", "diet"], "🏛️"),
        (["選挙", "选举", "election", "poll", "世論"], "🗳️"),
        (["政党", "自民党", "立憲", "共産党", "公明党", "ldp", "cdp"], "⚔️"),
        (["核", "nuclear", "被爆", "hibakusha", "廃絶", "disarmament"], "☢️"),
        (["経済", "经济", "産業", "产业"], "💹"),
        (["安保", "security", "同盟", "alliance", "日米"], "🤝"),
        (["防衛", "defense", "military", "自衛隊", "国防", "軍事"], "🛡️"),
        (["内閣", "政府", "government", "外務省", "防衛省"], "🏢"),
        (["メディア", "媒体", "media", "報道", "journalism"], "📰"),
    ]
    for needles, icon in rules:
        if any(needle.lower() in text for needle in needles):
            return icon
    return "●"


def _discussion_signal_lines(signals: dict[str, Any]) -> list[str]:
    recent = _sentiment_window_profile(signals.get("recent_sentiment") if signals else None, fallback=signals or {}, default_label="近期情绪")
    long_term = _sentiment_window_profile(signals.get("long_term_sentiment") if signals else None, default_label="长期情绪")
    comparison = (signals or {}).get("sentiment_comparison") or {}
    if not signals or not signals.get("post_count"):
        return [
            "- 本周未抓到足够公开讨论样本；公众讨论层不足以判断。",
            "- 说明：公众讨论只作为舆论、叙事、情绪信号，不能作为事实判断依据。",
            f"- 长期情绪参考：{long_term.get('overall_attitude', '样本不足')}；样本量 {long_term.get('post_count', 0)} 篇帖子、{long_term.get('comment_count', 0)} 条评论。",
        ]
    lines = [
        "- 说明：公众讨论只作为舆论、叙事、情绪信号，不能作为事实判断依据，不能提高新闻事件的证据等级。",
        (
            f"- 样本来源：{_cell(signals.get('platform_counts', {}))}；"
            f"样本量：{signals.get('post_count', 0)} 篇帖子、{signals.get('comment_count', 0)} 条评论；"
            f"时间窗口：{signals.get('window', {}).get('start')} 至 {signals.get('window', {}).get('end')}。"
        ),
        f"- 近期总体态度：{recent.get('overall_attitude', '不足以判断')}；情绪分布：{_cell(signals.get('sentiment_counts', {}))}。",
        f"- 长期总体态度：{long_term.get('overall_attitude', '样本不足')}；长期窗口：{long_term.get('window', {}).get('start')} 至 {long_term.get('window', {}).get('end')}。",
        f"- 近期/长期对比：{_cell(comparison.get('label') or '不足以判断')}；依据：{_cell(comparison.get('basis') or '样本不足')}。",
        f"- 常见叙事标签：{_cell(signals.get('narrative_counts', {}))}。",
        "- 局限：公开论坛样本存在平台偏差、活跃用户偏差与转贴噪音；需要结合官方来源、通讯社和新闻报道交叉验证。",
        "",
        "| 平台 | 社区 | 标题 | 评论数 | 链接 |",
        "|---|---|---|---:|---|",
    ]
    for post in signals.get("top_posts", [])[:12]:
        lines.append(
            "| {platform} | {community} | {title} | {comments} | [原文]({url}) |".format(
                platform=_cell(post.get("platform", "")),
                community=_cell(post.get("community") or post.get("board_name", "")),
                title=_cell(post.get("title", "")),
                comments=post.get("comment_count", 0),
                url=post.get("url", ""),
            )
        )
    return lines


def _source_coverage_table(source_list: list[dict[str, Any]]) -> list[str]:
    if not source_list:
        return ["| 来源类型 | 文章数 | 来源数 |", "|---|---:|---:|", "| 无 | 0 | 0 |"]
    coverage: dict[str, dict[str, Any]] = {}
    for source in source_list:
        source_type = source.source_type if hasattr(source, "source_type") else source["source_type"]
        source_name = source.source_name if hasattr(source, "source_name") else source["source_name"]
        row = coverage.setdefault(source_type or "unknown", {"articles": 0, "names": set()})
        row["articles"] += 1
        row["names"].add(source_name)
    lines = ["| 来源类型 | 文章数 | 来源数 |", "|---|---:|---:|"]
    for source_type, row in sorted(coverage.items()):
        lines.append(f"| {_cell(source_type)} | {row['articles']} | {len(row['names'])} |")
    return lines


def _source_lines(source_list: list[dict[str, Any]]) -> list[str]:
    if not source_list:
        return ["- 无来源。"]
    return [
        f"- {source.source_name if hasattr(source, 'source_name') else source['source_name']}：[{source.title if hasattr(source, 'title') else source['title']}]({source.url if hasattr(source, 'url') else source['url']})"
        for source in source_list
    ]


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


# ═══════════════════════════════════════════════════════
# Japan anti-war report functions
# ═══════════════════════════════════════════════════════

JAPAN_REPORT_PREFIX = "japan-antiwar"


def japan_report_filename(start_date: str) -> str:
    return f"{JAPAN_REPORT_PREFIX}-{start_date}.md"


def write_japan_report(markdown: str, start_date: str) -> Path:
    path = reports_dir() / japan_report_filename(start_date)
    path.write_text(markdown, encoding="utf-8")
    return path


def fallback_japan_report(start_date: str, end_date: str, structured: dict[str, Any], reason: str) -> str:
    clusters = structured.get("event_clusters", [])
    topic_changes = structured.get("topic_changes", [])
    entity_changes = structured.get("entity_changes", [])
    source_list = structured.get("source_list", [])
    temperature = structured.get("historical_comparison", {}).get("political_temperature", "")
    top_judgments = _japan_top_judgments(clusters, topic_changes)
    return "\n".join(
        [
            f"# 日本反战态势周报：{start_date} 至 {end_date}",
            "",
            "## 1. 一页纸结论",
            f"- 本报告使用本地 fallback 生成：{reason}",
            f"- 本周政治温度：{temperature or '不足以判断'}。该判断来自本周与过去 4 周主题数量对比；若历史样本不足，则不足以判断。",
            *top_judgments,
            "",
            "## 2. 本周反战关键事件时间线",
            *_timeline(clusters),
            "",
            "### 事件簇表",
            *_event_cluster_table(clusters),
            "",
            "## 3. 重大事件深度分析",
            *_event_sections(clusters),
            "",
            "## 4. 主题趋势变化",
            "### 主题升温/降温表",
            *_topic_table(topic_changes),
            "",
            *_topic_lines(topic_changes),
            "",
            "## 5. 反战行为体与组织互动",
            "### 实体出现频次表",
            *_entity_table(entity_changes),
            "",
            *_japan_actor_lines(entity_changes),
            "",
            "## 6. 宪法第九条、自卫队、驻日美军专章",
            *_japan_chapter_lines(clusters, ["憲法9条", "憲法改正", "自衛隊", "在日米軍", "沖縄", "安保", "平和"]),
            "",
            "## 7. 媒体与叙事差异",
            "### 来源覆盖情况",
            *_source_coverage_table(source_list),
            "",
            *_media_divergence_lines(structured.get("media_divergence", [])),
            "",
            "## 8. 公众讨论与情绪信号",
            *_discussion_signal_lines(structured.get("discussion_signals", {})),
            "",
            "## 8.1 公開SNSアカウント概览",
            *_japan_account_summary_lines(structured.get("account_summary", {})),
            "",
            *_visual_signal_lines(structured),
            "",
            "### 证据等级说明",
            "- A：多个可靠来源相互印证。",
            "- B：有可靠来源但仍需观察。",
            "- C：主要来自单一来源或评论推断。",
            "- 注意：candidate（未经验证）的社交账号内容不进入主要结论。",
            "- 来源背景备注只用于提示交叉验证方向，不能直接作为事实判断依据。",
            "",
            "## 9. 下周观察清单",
            "- 是否有官方来源补充确认本周事件。",
            "- 国会、外務省、防衛省、内閣府是否发布后续说明。",
            "- 同一事件是否获得通讯社、不同立场媒体与国际媒体交叉报道。",
            "",
            "## 10. 风险与不确定性",
            "- 信息不足之处：未被多个来源交叉确认的事件均需继续观察。",
            "- 单一来源风险：证据等级 C 的判断主要来自单一来源或评论推断。",
            "- candidate 账户内容已在主要结论中排除，仅出现在素材清单，仍需验证。",
            "- 需要等待官方确认的内容：未出现官方链接时，一律标记为不足以判断。",
            "- 模型推断的局限：本地 fallback 不调用大模型，只根据规则统计生成，无法替代人工研判。",
            "",
            "## 11. 附录：来源列表",
            *_source_lines(source_list),
            "",
        ]
    )


def _japan_top_judgments(clusters: list[dict[str, Any]], topic_changes: list[dict[str, Any]]) -> list[str]:
    lines = []
    for cluster in clusters[:5]:
        lines.append(
            f"- {cluster['representative_title']}（证据等级 {cluster.get('evidence_grade', 'C')}；来源数 {cluster.get('source_count', 0)}）"
        )
    if not lines:
        lines.append("- 本周样本不足，重大判断不足以判断。（证据等级 C）")
    if topic_changes:
        hottest = topic_changes[0]
        lines.append(
            f"- 主题热度最高：{hottest['topic_name']}，本周 {hottest['current_count']} 篇；延续性不足以判断。（证据等级 C）"
        )
    return lines


def _japan_actor_lines(entity_changes: list[dict[str, Any]]) -> list[str]:
    required = ["憲法改正", "自衛隊", "在日米軍", "沖縄", "国会", "市民団体", "平和運動"]
    lines = []
    by_name = {item["entity_name"]: item for item in entity_changes}
    for name in required:
        item = by_name.get(name)
        if item:
            lines.append(f"- {name}：本周出现 {item['current_count']} 次，变化 {item['change_label']}。")
        else:
            lines.append(f"- {name}：本周样本不足以判断。")
    return lines


def _japan_chapter_lines(clusters: list[dict[str, Any]], chapter_topics: list[str]) -> list[str]:
    selected = [
        cluster
        for cluster in clusters
        if any(topic in chapter_topics for topic in cluster.get("dominant_topics", []))
    ]
    if not selected:
        return ["- 本周相关主题样本不足以判断。"]
    return [
        f"- {cluster['representative_title']}：主题 {', '.join(cluster.get('dominant_topics') or [])}，证据等级 {cluster.get('evidence_grade', 'C')}。"
        for cluster in selected[:8]
    ]


def _japan_account_summary_lines(summary: dict[str, Any]) -> list[str]:
    if not summary:
        return ["- 账号库样本不足。"]
    lines = [
        f"- 账号库总数：{summary.get('total_accounts', 0)}；已核验：{summary.get('verified_count', 0)}；候选账号：{summary.get('candidate_count', 0)}。",
        "- 注意：candidate/候補アカウント仅作为待核验线索，不进入主要结论。",
    ]
    by_org = summary.get("by_organization") or {}
    if by_org:
        lines.append("### 组织账号概览（每组最多 3 个）")
        for org, accounts in list(by_org.items())[:10]:
            handles = ", ".join(str(account.get("handle") or account.get("username") or "") for account in accounts[:3])
            lines.append(f"- {org}: {handles or '账号不足'}")
    top_active = summary.get("top_active") or []
    if top_active:
        lines.append("### 活跃账号 Top 10")
        for index, account in enumerate(top_active[:10], start=1):
            lines.append(f"{index}. {account.get('handle') or account.get('username')} ({account.get('platform', '')})")
    candidates = summary.get("candidate_samples") or []
    if candidates:
        lines.append("### 候補アカウント（待核验样例，最多 3 条）")
        for account in candidates[:3]:
            evidence = account.get("evidence_url") or account.get("verification_evidence_url") or "证据链接不足"
            lines.append(f"- {account.get('handle') or account.get('username')} ({account.get('platform', '')}) - {evidence}")
    return lines
