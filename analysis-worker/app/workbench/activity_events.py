"""Source-backed activity review and prefecture counts, separate from mentions."""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import re

from ..japan_geo import MAPPING as ALIAS_MAP

PREFECTURES = list(dict.fromkeys(value for value in ALIAS_MAP.values() if re.fullmatch(r".+[都道府県]", value)))
ACTIVITY = re.compile(r"集会|デモ(?:行進)?|シンポジウム|講演会|抗議行動|街頭行動|平和行進|座り込み|討論会|上映会|抗议活动|示威|和平活动")
DATE = re.compile(r"(?:(20\d{2})[年/-])?(\d{1,2})[月/-](\d{1,2})日?")
REVIEWS = "atlas_activity_reviews"


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def capture_hash(item):
    return digest(str(item.get("title", "")) + "\n" + str(item.get("content", "")))


def extract_candidates(item):
    text = str(item.get("content") or item.get("title") or "")
    candidates, seen = [], set()
    for paragraph in re.split(r"[。\n]", text):
        match = ACTIVITY.search(paragraph)
        if not match:
            continue
        quote = paragraph[max(0, match.start() - 100):match.end() + 170].strip()
        normalized = re.sub(r"\s+", "", quote)
        if normalized in seen or not quote:
            continue
        seen.add(normalized)
        prefs, spans = set(), []
        matches = []
        for alias, canonical in ALIAS_MAP.items():
            pref = next((name for name in PREFECTURES if canonical.startswith(name)), "")
            if not pref:
                continue
            pattern = re.escape(alias)
            if alias.isascii():
                pattern = r"(?<![A-Za-z])" + pattern + r"(?![A-Za-z])"
            matches.extend((match.start(), match.end(), pref) for match in re.finditer(pattern, quote, re.I))
        # 東京都 contains the short string 京都. Longest non-overlapping
        # matches keep Tokyo intact rather than proposing two prefectures.
        for start, end, pref in sorted(matches, key=lambda row: (row[0] - row[1], row[0])):
            if not any(start < b and end > a for a, b in spans):
                spans.append((start, end))
                prefs.add(pref)
        found = DATE.search(quote)
        day = ""
        # A publication date cannot silently become an activity date.
        if found and found[1]:
            try:
                day = date(int(found[1]), int(found[2]), int(found[3])).isoformat()
            except ValueError:
                pass
        candidates.append({"id": digest(item["id"] + ":" + normalized)[:32], "item_id": item["id"],
                           "title": item.get("title") or quote[:100], "quote": quote,
                           "date": day, "prefecture": next(iter(prefs)) if len(prefs) == 1 else "",
                           "status": "candidate", "source_url": item.get("url", ""),
                           "source_capture_hash": capture_hash(item), "location_options": sorted(prefs),
                           "basis": "keyword_candidate", "reason": "活动关键词线索；时间、实际举办地点及重复报道需核对。"})
        if len(candidates) == 6:
            break
    return candidates


def item_activities(item):
    candidates = {row["id"]: row for row in extract_candidates(item)}
    metadata = item.get("metadata") or {}
    reviews = metadata.get(REVIEWS, {})
    if not isinstance(reviews, dict):
        reviews = {}
    for key, review in reviews.items():
        if not isinstance(review, dict) or review.get("item_id") != item["id"]:
            continue
        row = {**review, "id": key}
        if row.get("source_capture_hash") != capture_hash(item):
            row.update(status="candidate", review_outcome="pending", reason="来源正文已更正，原核对结论需重新确认。", stale=True)
        candidates[key] = row
    return list(candidates.values())


def summarize(rows, start_date="", end_date=""):
    visible = [row for row in rows if (not row.get("date") or (not start_date or row["date"] >= start_date) and (not end_date or row["date"] <= end_date))]
    groups, candidates, rejected = {}, [], 0
    for row in visible:
        if row["status"] == "rejected":
            rejected += 1
        elif row["status"] != "confirmed" or not row.get("date"):
            candidates.append(row)
        else:
            key = row.get("event_key") or row["id"]
            group = groups.setdefault(key, {**row, "sources": [], "record_ids": []})
            group["record_ids"].append(row["id"])
            if row["source_url"] not in {s["url"] for s in group["sources"]}:
                group["sources"].append({"url": row["source_url"], "item_id": row["item_id"], "quote": row["quote"]})
    events = list(groups.values())
    located = [event for event in events if event.get("prefecture") in PREFECTURES]
    counts = Counter(event["prefecture"] for event in located)
    denominator = len(located)
    return {"prefectures": [{"name": name, "count": counts[name], "percent": counts[name] / denominator * 100 if denominator else None} for name in PREFECTURES],
            "denominator": denominator, "confirmed": len(events), "unlocated": len(events) - denominator,
            "pending": sum(row.get('review_outcome') != 'inconclusive' or bool(row.get('stale')) for row in candidates),
            "inconclusive": sum(row.get('review_outcome') == 'inconclusive' and not row.get('stale') for row in candidates),
            "rejected": rejected, "unknown_date": sum(not row.get("date") for row in candidates),
            "events": sorted(events, key=lambda row: (row["date"], row["id"]), reverse=True),
            "candidates": candidates, "start_date": start_date, "end_date": end_date,
            "definition": "占比 = 本地已核对且去重的活动数 ÷ 当前日期范围内已核对、已定位的全部活动数。每次活动计一个举办地；多地巡回分别建记录。待核对、日期不明、未定位和已排除记录不进入分母。仅反映本资料库覆盖。"}


class ActivityRepository:
    def activities(self, start_date="", end_date="", page=1, page_size=30, status="", prefecture=""):
        from .store import _iso_date
        start_date, end_date = _iso_date(start_date, "start_date"), _iso_date(end_date, "end_date")
        if start_date and end_date and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期。")
        with self._transaction() as connection:
            captures = [self._item(row) for row in self._execute(connection, "SELECT * FROM wb_items WHERE " + self.item_visibility() + " ORDER BY created_at DESC,id").fetchall()]
        data = summarize([event for item in captures for event in item_activities(item)], start_date, end_date)
        rows = data.pop("candidates") if status == "candidate" else data.pop("events")
        data.pop("candidates", None)
        data.pop("events", None)
        if prefecture:
            rows = [row for row in rows if row.get("prefecture") == prefecture]
        total = len(rows)
        return {**data, "items": rows[(page - 1) * page_size:page * page_size], "total": total, "page": page, "page_size": page_size, "status": status or "confirmed"}

    def review_activity(self, item_id, data, *, _connection=None, _reviewer='human'):
        from .store import _iso_date, _now, _url
        from contextlib import nullcontext
        from .research_repository import _public_json
        if not isinstance(data, dict):
            raise ValueError("活动核对信息无效。")
        allowed = {"id", "status", "title", "date", "prefecture", "quote", "merge_with", "note"}
        if set(data) - allowed:
            raise ValueError("活动核对含未知字段。")
        status = data.get("status", "confirmed")
        if status not in {"confirmed", "candidate", "rejected", "inconclusive"}:
            raise ValueError("活动状态无效。")
        title, quote, note = (data.get(key, "") for key in ("title", "quote", "note"))
        if any(not isinstance(value, str) for value in (title, quote, note)) or not title.strip() or not quote.strip() or len(title) > 260 or len(quote) > 2000 or len(note) > 1600:
            raise ValueError("活动需有名称与可追溯原文摘录，且字段长度不能超过限制。")
        day = _iso_date(data.get("date", ""), "活动日期")
        pref = data.get("prefecture", "")
        if pref and pref not in PREFECTURES:
            raise ValueError("活动地点需要有效都道府县，无法定位时留空。")
        if status == "confirmed" and not day:
            raise ValueError("计入活动统计前需要核对实际活动日期。")
        with (nullcontext(_connection) if _connection is not None else self._transaction(write=True)) as connection:
            suffix = " FOR UPDATE" if self._postgres else ""
            item = self._item(self._execute(connection, "SELECT * FROM wb_items WHERE id=?" + suffix, (item_id,)).fetchone())
            if not item:
                raise LookupError("资料不存在。")
            if (item.get("metadata", {}).get("atlas_cleanup") or {}).get("status") == "trashed":
                raise ValueError("请先从回收站恢复该资料，再核对活动。")
            if quote.strip() not in str(item.get("title", "")) + "\n" + str(item.get("content", "")):
                raise ValueError("摘录必须能在当前保存的原文中找到；来源更正后请重新核对。")
            source = _url(item.get("url"))
            if status == "confirmed" and not source:
                raise ValueError("活动统计需要可访问的公开来源 URL。")
            metadata = item.get("metadata") or {}
            reviews = metadata.get(REVIEWS, {})
            reviews = reviews if isinstance(reviews, dict) else {}
            identity = data.get("id") or digest(item_id + ":" + re.sub(r"\s+", "", quote.strip()))[:32]
            if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
                raise ValueError("活动记录编号无效。")
            if identity not in reviews and identity not in {row["id"] for row in extract_candidates(item)} and data.get("id"):
                raise LookupError("候选活动不存在。")
            # Matching normalized title/date/place collapses exact reprints;
            # different descriptions merge only through an explicit review.
            event_key = digest(day + "|" + pref + "|" + re.sub(r"[\W_]+", "", title.casefold()) + "|" + re.sub(r"\s+", "", quote.strip()))[:32]
            merge = data.get("merge_with", "")
            if merge:
                target = None
                for row in self._execute(connection, "SELECT * FROM wb_items").fetchall():
                    target = next((event for event in item_activities(self._item(row)) if event.get("event_key") == merge and event.get("status") == "confirmed"), target)
                if not target:
                    raise LookupError("要合并的已核对活动不存在。")
                if target["date"] != day or target.get("prefecture", "") != pref:
                    raise ValueError("合并活动需要相同实际日期和举办地；不同地点应分别记录。")
                event_key = merge
            review = {"id": identity, "item_id": item_id, "title": title.strip(), "quote": quote.strip(), "date": day,
                      "prefecture": pref, "status": 'candidate' if status == 'inconclusive' else status, "review_outcome": status,
                      "source_url": source, "event_key": event_key,
                      "source_capture_hash": capture_hash(item), "basis": "human_review" if _reviewer == 'human' else 'evidence_review',
                      "reviewed_by": _reviewer, "reviewed_at": _now(), "note": note}
            previous = reviews.get(identity)
            if previous:
                history = previous.get("history", [])
                review["history"] = [*history[-7:], {key: value for key, value in previous.items() if key != "history"}]
            reviews[identity] = review
            metadata[REVIEWS] = reviews
            self._execute(connection, "UPDATE wb_items SET metadata=? WHERE id=?", (_public_json(metadata), item_id))
        return review
