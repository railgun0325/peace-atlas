from __future__ import annotations

from collections import Counter
from typing import Any


SENTIMENT_ORDER = ["positive", "negative", "anxiety", "anger", "sarcasm", "doubt", "neutral"]

SENTIMENT_DISPLAY: dict[str, dict[str, str]] = {
    "positive": {"name": "正向/支持", "icon": "✅", "color": "#16a34a"},
    "negative": {"name": "负向/反对", "icon": "▼", "color": "#dc2626"},
    "anxiety": {"name": "焦虑/担忧", "icon": "⚠️", "color": "#f59e0b"},
    "anger": {"name": "愤怒/不满", "icon": "🔥", "color": "#b91c1c"},
    "sarcasm": {"name": "嘲讽/戏谑", "icon": "◐", "color": "#7c3aed"},
    "doubt": {"name": "疑虑/求证", "icon": "?", "color": "#2563eb"},
    "neutral": {"name": "中性/信息不足", "icon": "●", "color": "#64748b"},
}

SENTIMENT_LEXICON: dict[str, list[str]] = {
    "positive": ["支持", "肯定", "加油", "赞成", "贊成", "合理", "不错", "不錯", "good", "agree", "support", "賛成"],
    "negative": ["反对", "反對", "烂", "爛", "失望", "荒谬", "荒謬", "糟", "bad", "fail", "disagree", "反対"],
    "anxiety": ["担心", "擔心", "忧虑", "憂慮", "恐", "怕", "风险", "風險", "危机", "危機", "worry", "risk", "concern", "懸念"],
    "anger": ["怒", "气死", "氣死", "可恶", "可惡", "垃圾", "黑箱", "愤怒", "憤怒", "angry", "outrage"],
    "sarcasm": ["笑死", "呵呵", "嘻嘻", "可怜", "可憐", "又来了", "又來了", "lol", "lmao", "草"],
    "doubt": ["真的假的", "有证据", "有證據", "不确定", "不確定", "怀疑", "懷疑", "是不是", "?", "？", "疑問"],
}

NARRATIVE_LEXICON: dict[str, list[str]] = {
    "两岸安全": ["台海", "两岸", "兩岸", "中国", "中國", "中共", "解放军", "解放軍", "pla", "china"],
    "国防预算": ["国防", "國防", "军购", "軍購", "预算", "預算", "defense", "military"],
    "政党攻防": ["民进党", "民進黨", "国民党", "國民黨", "民众党", "民眾黨", "dpp", "kmt"],
    "政府治理": ["总统府", "總統府", "行政院", "赖清德", "賴清德", "政府", "内阁", "內閣"],
    "立法院争议": ["立法院", "立委", "法案", "预算", "預算", "legislature", "parliament"],
    "外交国际": ["外交", "美国", "美國", "日本", "international", "diplomacy", "united states", "japan"],
    "经济产业": ["经济", "經濟", "产业", "產業", "台积电", "台積電", "半导体", "半導體", "tsmc"],
}


def classify_sentiment(text: str) -> dict[str, Any]:
    haystack = (text or "").lower()
    scores: Counter[str] = Counter()
    matched: dict[str, list[str]] = {}
    for label, words in SENTIMENT_LEXICON.items():
        for word in words:
            if word.lower() in haystack:
                scores[label] += 1
                matched.setdefault(label, []).append(word)
    if not scores:
        return {"label": "neutral", "confidence": 0.35, "keywords": [], "narrative_tags": narrative_tags(text)}
    label, score = scores.most_common(1)[0]
    total = sum(scores.values())
    confidence = min(0.45 + score / max(total, 1) * 0.45, 0.9)
    return {
        "label": label,
        "confidence": round(confidence, 2),
        "keywords": matched.get(label, [])[:8],
        "narrative_tags": narrative_tags(text),
    }


def narrative_tags(text: str) -> list[str]:
    haystack = (text or "").lower()
    tags = []
    for tag, words in NARRATIVE_LEXICON.items():
        if any(word.lower() in haystack for word in words):
            tags.append(tag)
    return tags[:6]


def summarize_sentiments(items: list[dict[str, Any]]) -> dict[str, Any]:
    sentiment_counts = Counter(item.get("sentiment_label") or item.get("label") or "neutral" for item in items)
    tag_counts: Counter[str] = Counter()
    for item in items:
        for tag in item.get("narrative_tags") or []:
            tag_counts[tag] += 1
    profile = sentiment_profile_from_counts(dict(sentiment_counts))
    return {
        "sentiment_counts": dict(sentiment_counts),
        "sentiment_percentages": profile["percentages"],
        "dominant_sentiment": profile["dominant_label"],
        "dominant_sentiment_name": profile["dominant_name"],
        "overall_attitude": profile["attitude_label"],
        "overall_attitude_icon": profile["icon"],
        "sample_size": profile["sample_size"],
        "top_narrative_tags": [{"tag": tag, "count": count} for tag, count in tag_counts.most_common(12)],
    }


def sentiment_profile_from_counts(counts: dict[str, int] | Counter[str]) -> dict[str, Any]:
    normalized = {label: int(counts.get(label, 0) or 0) for label in SENTIMENT_ORDER}
    for label, value in counts.items():
        if label not in normalized:
            normalized[label] = int(value or 0)
    total = sum(max(value, 0) for value in normalized.values())
    if total <= 0:
        return {
            "sample_size": 0,
            "counts": normalized,
            "percentages": {label: 0.0 for label in normalized},
            "dominant_label": "neutral",
            "dominant_name": SENTIMENT_DISPLAY["neutral"]["name"],
            "attitude_label": "样本不足",
            "icon": "○",
            "color": SENTIMENT_DISPLAY["neutral"]["color"],
        }

    percentages = {label: round(count / total * 100, 1) for label, count in normalized.items()}
    dominant_label = max(normalized, key=lambda label: (normalized[label], -SENTIMENT_ORDER.index(label) if label in SENTIMENT_ORDER else -99))
    display = SENTIMENT_DISPLAY.get(dominant_label, SENTIMENT_DISPLAY["neutral"])
    if total < 5:
        attitude = f"样本较少，暂呈{display['name']}"
    elif dominant_label == "neutral":
        attitude = "整体偏中性，缺少明确情绪方向"
    elif dominant_label == "positive":
        attitude = "整体偏正向/支持"
    elif dominant_label == "negative":
        attitude = "整体偏负向/反对"
    elif dominant_label == "anxiety":
        attitude = "整体偏焦虑/担忧"
    elif dominant_label == "anger":
        attitude = "整体偏愤怒/不满"
    elif dominant_label == "sarcasm":
        attitude = "嘲讽/戏谑较多"
    elif dominant_label == "doubt":
        attitude = "疑虑/求证较多"
    else:
        attitude = display["name"]

    return {
        "sample_size": total,
        "counts": normalized,
        "percentages": percentages,
        "dominant_label": dominant_label,
        "dominant_name": display["name"],
        "attitude_label": attitude,
        "icon": display["icon"],
        "color": display["color"],
    }
