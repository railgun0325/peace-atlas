"""Explainable social research filter; no model calls or inferred endorsement."""
from __future__ import annotations

import re
import unicodedata


def _text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"https?://\S+", " ", value)
    return re.sub(r"憲法(?:第)?[9九]条|9条(?=を?守)", "憲法九条", value)


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    # Latin word boundaries avoid such false positives as baseball / base.
    return [term for term in terms if (re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text)
            if term.isascii() else term in text)]


SPECIFIC = ("憲法九条", "宪法九条", "九条の会", "平和憲法", "非核三原則", "日本被団協",
            "日米安保", "日米同盟", "安保法制", "防衛増税")
JAPAN = ("日本", "沖縄", "东京", "東京", "大阪", "広島", "長崎", "japan", "japanese",
         "okinawa", "hiroshima", "nagasaki", "tokyo", "henoko", "futenma", "自衛隊", "在日米軍", "辺野古", "普天間")
CIVIC = ("反戦", "戦争反対", "戦争を止め", "戦争をやめ", "平和運動", "平和集会", "平和行進", "平和デモ",
         "反基地", "核兵器廃絶", "核廃絶", "核禁止条約", "護憲", "軍拡反対", "武力行使反対",
         "antiwar", "anti-war", "peace movement", "peace march", "peace protest", "nuclear abolition",
         "nuclear disarmament", "article 9", "pacifism", "no war")
POLICY = ("核兵器", "被爆", "軍縮", "軍拡", "防衛費", "基地負担", "米軍基地", "戦争責任",
          "平和教育", "改憲", "集団的自衛権", "核の傘", "停戦", "原爆", "nuclear weapons", "hibakusha",
          "military base", "defense budget", "defence budget", "ceasefire", "atomic bomb",
          "基地建設", "基地移設", "基地問題", "防衛政策", "基地反対")


def assess(item: dict, mode: str = "focused") -> dict:
    """Classify the actual post body, excluding handles, URLs, quotes and alt text."""
    if mode not in {"focused", "broad"}:
        raise ValueError("相关性模式必须为 focused 或 broad。")
    metadata = item.get("metadata") or {}
    if mode == "broad":
        return {"mode": mode, "matched": True, "topics": [], "reasons": ["按用户检索条件保留，研究相关性待人工确认。"]}
    if metadata.get("content_kind", "body") != "body":
        return {"mode": mode, "matched": False, "topics": [], "reasons": ["缺少已读取的帖子正文；图片描述不作为正文筛选依据。"]}
    text = _text(str(item.get("content") or ""))
    specific, civic, policy, japan = (_hits(text, terms) for terms in (SPECIFIC, CIVIC, POLICY, JAPAN))
    japanese = bool(re.search(r"[ぁ-んァ-ヶ]", text))
    # Japanese civic anti-war statements are within scope even when the author
    # omits Japan. Generic peace slogans and place names alone are insufficient.
    matched = bool(specific or (civic and (japan or japanese)) or (policy and japan))
    reasons = []
    if specific:
        reasons.append("正文包含日本和平/安全政策的明确议题：" + "、".join(specific[:4]))
    elif matched:
        reasons.append("正文同时包含和平/反战议题与日本语境：" + "、".join((civic or policy)[:4]))
    else:
        reasons.append("正文缺少明确的日本和平/反战研究关联；仅命中宽泛词或地点不足以收录。")
    return {"mode": mode, "matched": matched, "topics": list(dict.fromkeys(specific + civic + policy)), "reasons": reasons}


def filter_items(items: list[dict], mode: str = "focused", limit: int = 30) -> tuple[list[dict], dict]:
    kept, rejected, examples = [], 0, []
    for item in items:
        result = assess(item, mode)
        if result["matched"]:
            kept.append({**item, "metadata": {**(item.get("metadata") or {}), "relevance": result}})
        else:
            rejected += 1
            if len(examples) < 10:
                examples.append({"url": item.get("url", ""), "reason": result["reasons"][0]})
    diagnostic = {"source": "relevance", "ok": True, "outcome": "filtered" if rejected else "ok",
                  "mode": mode, "scanned": len(items), "accepted": min(len(kept), limit), "irrelevant": rejected,
                  "over_limit": max(0, len(kept) - limit), "examples": examples,
                  "message": f"相关性筛选：读取 {len(items)} 条正文，排除 {rejected} 条，保留 {min(len(kept), limit)} 条；规则结果不代表事实核验。"}
    return kept[:limit], diagnostic
