"""Uncalibrated planning ranges and parameter-bound execution confirmation."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import time

_SECRET = secrets.token_bytes(32)
TOKEN_SECONDS = 1800


def _digest(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def confirmation_token(values):
    expires = int(time.time()) + TOKEN_SECONDS
    message = f"{expires}:{_digest(values)}"
    return message + ":" + hmac.new(_SECRET, message.encode(), hashlib.sha256).hexdigest()


def validate_confirmation(values, token):
    try:
        expiry, digest, signature = token.split(":")
        message = f"{expiry}:{digest}"
        valid = (int(expiry) >= time.time() and int(expiry) <= time.time() + TOKEN_SECONDS + 5
                 and hmac.compare_digest(digest, _digest(values))
                 and hmac.compare_digest(signature, hmac.new(_SECRET, message.encode(), hashlib.sha256).hexdigest()))
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("请先查看当前参数的预计用时，再确认开始；参数已改变或预估已过期，请重新预估。")


def estimate_research(values, *, query_count=1, ai_configured=False):
    """Scenario ranges, never a claim about reachable platform material.

    Account growth cannot be known without reading source pages. The explicit
    scenario assumptions are therefore retained next to the displayed range.
    No live collection, paid model calls or queue creation happens here.
    """
    sources = values.get("seed_sources") or [values.get("source", "google_news")]
    social = sum(source in {"twitter", "instagram"} for source in sources)
    roots = max(1, len(values.get("seed_accounts") or []) + (values.get("mode") == "account"))
    queries = 0 if values.get("mode") == "account" else max(1, query_count) * len(sources)
    depth = int(values.get("depth", 2))
    directions = sum(value != "mentions" for value in values.get("expand_relations") or [])
    account_cap = values.get("max_accounts", 0)
    # A planning scenario with 1--3 useful new accounts per explored account.
    # The true frontier may be empty or much larger; neither scenario is a cap.
    small = roots * (depth + 1) if social else 0
    large = roots * sum(3 ** level for level in range(depth + 1)) if social else 0
    if account_cap:
        small, large = min(small, account_cap), min(large, account_cap)
    scrolls = values.get("max_scrolls", 10)
    page_low, page_high = 15 + min(scrolls, 20), 45 + 3 * scrolls
    low = max(20, queries * 10 + small * page_low * (1 + directions))
    high = max(low * 2, queries * (45 + 3 * scrolls) + large * page_high * (1 + directions))
    ai = values.get("analysis_mode") == "ai" or values.get("analysis_mode") == "auto" and ai_configured
    if ai:
        likely_evidence = min(values.get("max_records") or 1000000, max(80, (large or queries) * min(values.get("max_records_per_account", 1000), 200)))
        low += 30
        high += math.ceil(likely_evidence / 80) * 90
    # Round to minutes at this deliberately coarse, pre-execution level.
    low = max(60, math.ceil(low / 60) * 60)
    high = max(low + 60, math.ceil(high / 60) * 60)
    seconds_cap = values.get("time_budget_seconds", 0)
    return {"version": 1, "lower_seconds": low, "upper_seconds": high,
            "confidence": "low", "basis": "参数情景推算，尚未用本机或当前平台实测速度校准。",
            "time_limit_seconds": seconds_cap or None,
            "time_limit_may_interrupt": bool(seconds_cap and seconds_cap < high),
            "scenario_accounts": {"lower": small, "upper": large},
            "query_branches": queries,
            "assumptions": ["账号情景假设每层产生 1–3 个可继续的相关公开账号，实际数量可能更少或远多于此。",
                            "用时会受平台加载、登录或限流、去重比例、原文长度与模型响应影响；范围不是完成保证。",
                            "按日期、层数、每页读取范围和关系方向自然结束；总量或总时长为 0 表示不另设提前停止条件。"],
            "scope": {key: values.get(key) for key in ("start_date", "end_date", "depth", "max_scrolls", "max_records_per_account", "max_neighbors_per_account", "max_records", "max_accounts", "max_edges", "time_budget_seconds")},
            "preview_token": confirmation_token(values), "valid_for_seconds": TOKEN_SECONDS}
