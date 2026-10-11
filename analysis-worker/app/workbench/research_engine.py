"""Budgeted, checkpointed public-evidence research with auditable decisions.

This is a small agent loop around the already validated collectors, not a new
browser automation framework. Every admitted account originates in a visible
post, explicit news link or public relation list. Model suggestions cannot
create accounts, edges or verified affiliations.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import math
import os
import re
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urlsplit

from . import pipeline, relevance
from .pipeline import PipelineError
from .limits import (
    RESEARCH_RECORDS_DEFAULT, RESEARCH_RECORDS_MIN, RESEARCH_RECORDS_MAX,
    RESEARCH_SCROLLS_DEFAULT, RESEARCH_SCROLLS_MIN, RESEARCH_SCROLLS_MAX,
    RESEARCH_TIME_DEFAULT, RESEARCH_TIME_MIN, RESEARCH_TIME_MAX, research_branch_seconds,
    RESEARCH_DEPTH_MAX, RESEARCH_ACCOUNTS_MAX, RESEARCH_EDGES_MAX,
    RESEARCH_PER_ACCOUNT_MAX, RESEARCH_NEIGHBORS_MAX, UNLIMITED_COUNT,
)
from .query_plan import build_query_plan, configured_ai_json, parse_keyword_groups, query_matches
from .relationship_analysis import analyze_relationships_with_ai, mention_handles, stable_id
from .social import collector
from .social.common import SocialError, account, emit, normalize_handle, now, safe_error
from .social.session import browser_session

BLOCKING = {"login_required", "rate_limited", "challenge", "access_denied", "runtime_unavailable", "busy"}


def normalize_research_seeds(values: dict) -> tuple[str, ...]:
    """Validate explicit roots before opening any browser, retaining platform identity."""
    source = values.get("source", "twitter")
    sources = values.get("seed_sources") or [source]
    seeds = values.get("seed_accounts", [])
    if not isinstance(seeds, (list, tuple)) or len(seeds) > 10 or any(not isinstance(seed, str) or len(seed) > 2048 for seed in seeds):
        raise PipelineError("账号种子最多 10 个公开账号名称或主页地址。")
    if values.get("mode") == "account":
        if source not in {"twitter", "instagram"} or not values.get("handle"):
            raise PipelineError("账号研究需要有效的 X 或 Instagram 用户名。")
        seeds = [values["handle"], *seeds]
    normalized = []
    for seed in seeds:
        try:
            parsed = urlsplit(seed.strip())
            if parsed.scheme or parsed.netloc:
                hosts = {"x.com": "twitter", "www.x.com": "twitter", "twitter.com": "twitter", "www.twitter.com": "twitter",
                         "instagram.com": "instagram", "www.instagram.com": "instagram"}
                platform = hosts.get(parsed.hostname)
                if not platform or parsed.scheme != "https" or parsed.port not in {None, 443} or parsed.query or parsed.fragment:
                    raise ValueError()
            else:
                platform = source
            if platform not in {"twitter", "instagram"} or platform not in sources:
                raise ValueError()
            url = account(platform, seed)["url"]
        except (ValueError, TypeError):
            raise PipelineError("种子账号须为已选择平台的有效用户名或 HTTPS 主页地址，不含查询参数。") from None
        if url not in normalized:
            normalized.append(url)
    if len(normalized) > 10:
        raise PipelineError("含主要账号在内，最多指定 10 个研究起点。")
    return tuple(normalized)


def build_research_plan(values: dict) -> dict:
    """An account walk without a topic has no implicit search or theme filter."""
    if values.get("mode") == "account" and not str(values.get("keyword") or "").strip():
        combination = values.get("keyword_mode", "any")
        if combination not in {"any", "all"}:
            raise PipelineError("关键词组合只能选择任一组（any）或所有组（all）。")
        return {"version": 1, "raw_keyword": "", "source": values["source"], "mode": "account", "scope": "account",
                "combination": combination, "groups": [], "queries": [], "suggestions": [], "suggestions_applied": False,
                "preview": "指定账号及其可追溯公开关系；未设置主题过滤。", "separator_help": "可选关键词只用于缩小本次账号研究范围。",
                "ai": {"requested": False, "used": False, "outcome": "not_requested"}}
    return build_query_plan(values)


def retained_seed_queries(params: dict, query_plan: dict, summary: dict) -> list[dict]:
    """Recover original, bounded query work without trusting completion labels.

    Old news refill IDs refer to a longer prefix of the original query. Their
    largest saved request is the best retained observation of that same scope;
    neither marker can hide a known unexhausted or failed prefix.
    """
    if params.get("mode", summary.get("mode", "keyword")) == "account":
        return []
    queries = (query_plan or {}).get("queries") or []
    if not all(isinstance(query, dict) and isinstance(query.get("id"), str) and query.get("keyword") for query in queries) or not queries:
        query_plan = build_research_plan(params)
        queries = query_plan.get("queries") or []
    sources = params.get("seed_sources") or summary.get("seed_sources") or [params.get("source", summary.get("source", "twitter"))]
    known = {(source, query["id"]): {"source": source, "query_id": query["id"], "keyword": query["keyword"], "adaptive": False}
             for query in queries for source in sources if source in {"twitter", "instagram", "google_news"}}
    for query in summary.get("additional_queries") or []:
        if not isinstance(query, dict) or not isinstance(query.get("id"), str) or not isinstance(query.get("keyword"), str) or not query["keyword"]:
            continue
        for source in sources:
            if source in {"twitter", "instagram", "google_news"}:
                known[(source, query["id"])] = {"source": source, "query_id": query["id"], "keyword": query["keyword"], "adaptive": True}
    done = set(summary.get("completed_queries") or [])
    stats = summary.get("seed_query_stats") or {}
    saved = {(row.get("source"), row.get("query_id")): row for row in summary.get("pending_seed_queries") or [] if isinstance(row, dict)}
    canonical_work = summary.get("pending_seed_queries_version") == 1
    pending = []
    for identity, query in known.items():
        key = query["source"] + ":" + query["query_id"]
        observations = [(stats.get(key), 0)]
        if not canonical_work:
            observations.append((stats.get(key + "-news-refill"), 1))
        observations = [(row, order) for row, order in observations if isinstance(row, dict)]
        latest = max(observations, key=lambda pair: (pair[0].get("requested_limit", 0) if isinstance(pair[0].get("requested_limit"), int) else 0, pair[1]))[0] if observations else None
        row = saved.get(identity, {})
        if latest is not None:
            complete = latest.get("failed") is not True and (latest.get("exhausted") is True or latest.get("scope_complete") is True)
            if complete:
                continue
        elif key in done and not row:
            continue
        value = dict(query)
        for field in ("requested_limit", "returned_count", "scanned"):
            number = (latest or row).get(field, 0)
            value[field] = number if isinstance(number, int) and not isinstance(number, bool) and number >= 0 else 0
        number = row.get("retained_prefix", 0)
        value["retained_prefix"] = max(value["returned_count"], number if isinstance(number, int) and not isinstance(number, bool) and number >= 0 else 0)
        value["failed"] = (latest or row).get("failed") is True
        pending.append(value)
    return pending


def compact_analysis_catalog(analysis: dict, *, max_bytes: int = 1500000, excerpt_chars: int = 240) -> dict:
    """Store auditable analysis summaries independently of full source bodies.

    The repository's 2 MiB analysis contract also contains decision history.
    Reserve room for that history and give retained findings their supporting
    source IDs and complete URLs before filling the remaining catalogue. No
    source material in the ordinary library is modified or discarded here.
    """
    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))

    original = {key: list(analysis.get(key) or []) for key in ("evidence", "findings", "affiliations")}
    evidence, encoded_sizes = {}, {}
    compacted = 0
    fields = {"id", "type", "url", "account_key", "source_key", "target_key", "observed_at", "published_at", "excerpt", "excerpt_truncated",
              "content_sha256", "source_char_count"}
    for row in original["evidence"]:
        value = {key: copy.deepcopy(child) for key, child in row.items() if key in fields}
        excerpt = str(value.get("excerpt") or "")
        value["excerpt"] = excerpt[:excerpt_chars]
        if len(excerpt) > excerpt_chars:
            compacted += 1
            value["excerpt_truncated"] = True
        identity = str(value.get("id") or "")
        if identity:
            evidence[identity], encoded_sizes[identity] = value, size(value) + 1
    result = {key: copy.deepcopy(value) for key, value in analysis.items() if key not in original and key != "decisions"}
    result.update(evidence=[], findings=[], affiliations=[])
    # Fixed output metadata is much smaller than this allowance. The byte
    # count below includes each complete URL instead of truncating sources.
    available = max_bytes - 16384
    used, retained = size(result), {}
    fixed_metadata_bytes = used
    unresolved = 0
    for key in ("affiliations", "findings"):
        for row in original[key]:
            ids = list(dict.fromkeys(row.get("evidence_ids") or []))
            if any(identity not in evidence for identity in ids):
                unresolved += 1
                continue
            new_ids = [identity for identity in ids if identity not in retained]
            required = size(row) + 1 + sum(encoded_sizes[identity] for identity in new_ids)
            if used + required > available:
                continue
            result[key].append(copy.deepcopy(row))
            for identity in new_ids:
                retained[identity] = evidence[identity]
            used += required
    for identity, value in evidence.items():
        if identity not in retained and used + encoded_sizes[identity] <= available:
            retained[identity] = value
            used += encoded_sizes[identity]
    result["evidence"] = list(retained.values())
    omitted = {key: len(original[key]) - len(result[key]) for key in original}
    result["serialization"] = {
        "max_bytes": max_bytes, "excerpt_chars": excerpt_chars, "excerpt_compacted_count": compacted,
        "original_counts": {key: len(value) for key, value in original.items()},
        "stored_counts": {key: len(result[key]) for key in original}, "omitted_counts": omitted,
        "unresolved_findings": unresolved,
        "fixed_metadata_bytes": fixed_metadata_bytes,
        "source_bodies": "完整材料在资料库及研究导出中保留；此处仅为可核对分析摘要。",
    }
    if any(omitted.values()):
        result["coverage"] = result.get("coverage", "") + " 分析摘要达到存储预算；已优先保留关联判断及其完整来源链接，其余目录条目仍可在资料库核对，未声称此摘要包含全部分析条目。"
    result["serialization"]["stored_bytes"] = size(result)
    return result


@dataclass(frozen=True)
class ResearchParams:
    source: str
    seed_sources: tuple[str, ...]
    keyword: str
    keyword_mode: str
    start_date: str
    end_date: str
    depth: int
    max_accounts: int
    max_edges: int
    max_records: int
    max_scrolls: int
    time_budget_seconds: int
    max_records_per_account: int
    max_neighbors_per_account: int
    expand_relations: tuple[str, ...]
    analysis_mode: str
    research_goal: str
    relevance: str
    mode: str
    seed_accounts: tuple[str, ...]
    unlimited_limits: tuple[str, ...] = ()

    def public_limit(self, name):
        return 0 if name in self.unlimited_limits else getattr(self, name)

    @classmethod
    def parse(cls, values: dict) -> "ResearchParams":
        source = str(values.get("source") or "twitter")
        if source not in {"twitter", "instagram", "google_news"}:
            raise PipelineError("研究来源只能是 Google News、X 或 Instagram。")
        sources = values.get("seed_sources") or [source]
        if not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= 3 or any(entry not in {"twitter", "instagram", "google_news"} for entry in sources):
            raise PipelineError("种子来源只能从 Google News、X、Instagram 中选择。")
        limits = {}
        unlimited = []
        for name, default, low, high in (("depth", 2, 1, RESEARCH_DEPTH_MAX), ("max_accounts", 0, 0, RESEARCH_ACCOUNTS_MAX), ("max_edges", 0, 0, RESEARCH_EDGES_MAX),
                                         ("max_records", RESEARCH_RECORDS_DEFAULT, RESEARCH_RECORDS_MIN, RESEARCH_RECORDS_MAX),
                                         ("max_scrolls", RESEARCH_SCROLLS_DEFAULT, RESEARCH_SCROLLS_MIN, RESEARCH_SCROLLS_MAX),
                                         ("time_budget_seconds", RESEARCH_TIME_DEFAULT, RESEARCH_TIME_MIN, RESEARCH_TIME_MAX)):
            value = values.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise PipelineError(f"{name} 必须为 {low} 至 {high} 的整数。")
            limits[name] = value
            if value == 0 and name in {"max_records", "max_accounts", "max_edges"}:
                unlimited.append(name)
                limits[name] = UNLIMITED_COUNT
        if 0 < limits["time_budget_seconds"] < 30:
            raise PipelineError("研究时长须为 0 或至少 30 秒。")
        per_account = 1000 if "max_records" in unlimited else min(RESEARCH_PER_ACCOUNT_MAX, max(6, math.ceil(limits["max_records"] / (values.get("max_accounts") or 20))))
        for name, default, low, high in (("max_records_per_account", per_account, 1, RESEARCH_PER_ACCOUNT_MAX), ("max_neighbors_per_account", 100 if unlimited else 8, 1, RESEARCH_NEIGHBORS_MAX)):
            value = values.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise PipelineError(f"{name} 必须为 {low} 至 {high} 的整数。")
            limits[name] = value
        relations = values.get("expand_relations", ["following", "followers", "mentions"])
        if not isinstance(relations, (list, tuple)) or len(relations) > 3 or any(value not in {"following", "followers", "mentions"} for value in relations):
            raise PipelineError("可拓展关系仅为 following、followers、mentions。")
        analysis = str(values.get("analysis_mode") or "auto")
        if analysis not in {"auto", "rules", "ai"}:
            raise PipelineError("分析方式只能是 auto、rules 或 ai。")
        goal = values.get("research_goal", "")
        if not isinstance(goal, str) or len(goal) > 1200:
            raise PipelineError("研究目标最多 1200 字。")
        mode = str(values.get("mode") or "keyword")
        relevance_mode = str(values.get("relevance") or "focused")
        if relevance_mode not in {"focused", "broad"}:
            raise PipelineError("研究相关性只能选择 focused 或 broad。")
        seeds = normalize_research_seeds({**values, "source": source, "seed_sources": sources})
        if len(seeds) > limits["max_accounts"]:
            raise PipelineError("最多探索账号不能少于明确指定的研究起点数量。")
        plan = build_research_plan({**values, "source": source})
        try:
            start, end = date.fromisoformat(str(values["start_date"])), date.fromisoformat(str(values["end_date"]))
            if start > end or (end - start).days > 3660:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise PipelineError("研究必须提供有效且不超过十年的日期范围。") from None
        return cls(source, tuple(dict.fromkeys(sources)), plan["raw_keyword"], plan["combination"], str(start), str(end),
                   **limits, expand_relations=tuple(dict.fromkeys(relations)), analysis_mode=analysis, research_goal=goal, relevance=relevance_mode,
                   mode=mode, seed_accounts=tuple(seeds), unlimited_limits=tuple(unlimited))


class _AccountLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            for key, value in attrs:
                if key.lower() == "href" and value:
                    self.links.append(value)


def linked_accounts(material: str) -> list[dict]:
    """Explicit platform profile/status URLs only; never infer a handle from names."""
    values = []
    for url in re.findall(r"https://(?:www\.)?(?:x\.com|twitter\.com|instagram\.com)/[^\s<>\"'。；，、！？（）\[\]()]+", str(material or ""))[:80]:
        try:
            parsed = urlsplit(url.rstrip(".,;:!?"))
            if parsed.username or parsed.password or parsed.scheme != "https":
                continue
            platform = "instagram" if parsed.hostname in {"instagram.com", "www.instagram.com"} else "twitter"
            parts = parsed.path.strip("/").split("/")
            if len(parts) not in {1, 3} or (len(parts) == 3 and parts[1] not in {"status", "p", "reel"}):
                continue
            entry = account(platform, parts[0])
            if entry["account_key"] not in {row["account_key"] for row in values}:
                values.append(entry)
        except (ValueError, UnicodeError):
            continue
    return values


class BrowserResearchAdapter:
    """At most one independent context per selected platform; requests sequential."""
    def __init__(self):
        self.stack = AsyncExitStack()
        self.pages = {}

    async def __aenter__(self):
        await self.stack.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.stack.__aexit__(*args)

    async def page(self, platform):
        if platform not in self.pages:
            _, page = await self.stack.enter_async_context(browser_session(platform, visible=False, require_session=True))
            self.pages[platform] = page
        return self.pages[platform]

    async def search(self, platform, keyword, params, limit, accept):
        deadline = time.monotonic() + params.time_budget_seconds
        if platform == "google_news":
            result = await collect_research_news(keyword, params, limit)
            # News summaries can contain literal public account URLs. A small
            # same-source HTML sample also preserves explicit hrefs, without
            # visiting any account inferred from an organization name.
            for item in result["items"][:3]:
                links = linked_accounts(str(item.get("content", "")))
                try:
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    async with asyncio.timeout(min(8, max(0.1, deadline - time.monotonic()))):
                        body, final_url, _ = await pipeline._fetch_public_feed(item["url"])
                    parser = _AccountLinks()
                    parser.feed(body.decode("utf-8", errors="replace"))
                    links += linked_accounts(" ".join(parser.links))
                    item.setdefault("metadata", {})["news_link_page_url"] = final_url
                except (Exception, TimeoutError):
                    item.setdefault("metadata", {})["news_link_sample_unavailable"] = True
                item.setdefault("metadata", {})["linked_accounts"] = list({row["account_key"]: row for row in links}.values())
            return {**result, "accounts": [], "relations": []}
        page = await self.page(platform)
        return await collector.collect_on_page(page, {"source": platform, "mode": "hashtag" if params.mode == "hashtag" else "keyword",
            "keyword": keyword, "start_date": params.start_date, "end_date": params.end_date, "max_records": limit,
            "max_accounts": min(params.max_accounts, 10), "max_scrolls": params.max_scrolls, "include_following": False, "max_following": 0}, accept=accept, time_budget_seconds=max(0.1, deadline - time.monotonic()))

    async def profile(self, platform, handle):
        return await collector.read_profile(await self.page(platform), platform, handle)

    async def posts(self, platform, handle, params, limit, accept):
        deadline = time.monotonic() + params.time_budget_seconds
        page = await self.page(platform)
        return await collector.collect_on_page(page, {"source": platform, "mode": "account", "handle": handle,
            "keyword": "", "start_date": params.start_date, "end_date": params.end_date, "max_records": limit,
            "max_accounts": 1, "max_scrolls": params.max_scrolls, "include_following": False, "max_following": 0}, accept=accept, time_budget_seconds=max(0.1, deadline - time.monotonic()))

    async def neighbors(self, platform, handle, direction, params, known_count):
        deadline = time.monotonic() + params.time_budget_seconds
        page = await self.page(platform)
        return await collector.read_neighbors(page, platform, handle, direction,
            limit=params.max_neighbors_per_account, max_scrolls=params.max_scrolls, known_count=known_count, time_budget_seconds=max(0.1, deadline - time.monotonic()))


async def collect_research_news(keyword: str, params: ResearchParams, limit: int) -> dict:
    """Reuse vetted RSS transport/date enrichment with the present research topic.

    The legacy single-purpose peace taxonomy remains in simple collection.
    Deep research must not silently discard explicitly requested labour or
    student material before the current-topic evidence gate gets to see it.
    """
    start, end = date.fromisoformat(params.start_date), date.fromisoformat(params.end_date)
    search_start = date.fromordinal(max(date.min.toordinal(), start.toordinal() - 1))
    search_end = date.fromordinal(min(date.max.toordinal(), end.toordinal() + 2))
    query = f"{keyword} after:{search_start} before:{search_end}"
    url = pipeline.GOOGLE_NEWS_RSS + "?" + urlencode({"q": query, "hl": "ja", "gl": "JP", "ceid": "JP:ja"})
    diag = {"source": "google_news", "stage": "research_seed", "query": query, "url": url, "ok": False,
            "fetched": 0, "accepted": 0, "unknown_dates": 0, "date_filtered": 0, "query_mismatch": 0, "invalid_records": 0,
            "coverage": "本次公开 RSS 返回的样本，原日期范围按 Asia/Tokyo 复核。"}
    items = []
    try:
        async with asyncio.timeout(pipeline.COLLECT_TIMEOUT):
            body, final_url, status = await pipeline._fetch_public_feed(url)
        diag.update(final_url=final_url, status_code=status)
        parsed = pipeline.feedparser.parse(body)
        if not parsed.version or parsed.get("bozo"):
            raise PipelineError("研究新闻入口没有返回完整可识别的 RSS/Atom。")
        diag["fetched"] = len(parsed.entries)
        branch_plan = build_query_plan({"keyword": keyword, "source": "google_news"})
        seen = set()
        for entry in parsed.entries:
            if len(items) >= limit:
                break
            try:
                publisher = entry.get("source") or {}
                raw_summary = str(entry.get("summary") or "")
                item = pipeline.enrich_item({"title": str(entry.get("title") or ""), "content": raw_summary,
                    "url": entry.get("link"), "source_name": publisher.get("title", "Google News") if isinstance(publisher, dict) else "Google News",
                    "published_at": entry.get("published") or entry.get("updated"), "type": "news", "source_type": "news_aggregator"})
                if item["publication_date_unknown"]:
                    diag["unknown_dates"] += 1
                    continue
                if not pipeline._in_period(item, start, end):
                    diag["date_filtered"] += 1
                    continue
                if not query_matches(branch_plan, item["title"] + "\n" + item["content"]):
                    diag["query_mismatch"] += 1
                    continue
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                parser = _AccountLinks()
                parser.feed(raw_summary)
                item["metadata"] = {"collection_method": "public_rss_current_research_topic", "source_url": url, "collected_at": now(),
                    "linked_accounts": linked_accounts(" ".join(parser.links)), "relevance_basis": "query_match",
                    "relevance_reason": "新闻标题或已读摘要满足本次用户关键词，不使用历史和平词表作硬过滤。"}
                items.append(item)
            except (ValueError, TypeError, OverflowError):
                diag["invalid_records"] += 1
        diag.update(ok=True, outcome="ok" if items else "no_matches", accepted=len(items),
                    message="已读取符合当前主题与原日期范围的公开新闻样本。" if items else "已核对本次 RSS 样本，没有符合当前主题与原日期范围的条目。")
    except TimeoutError:
        diag.update(outcome="timeout", message="新闻入口达到 35 秒有界等待；没有把超时标为空结果。")
    except PipelineError as exc:
        # The vetted transport raises controlled messages for DNS/URL/feed
        # policy failures. Retain that reason while unknown exceptions below
        # remain categorical and never expose their request or secret values.
        diag.update(outcome="source_error", message=str(exc), **safe_error(exc, "research_news"))
    except Exception as exc:
        diag.update(outcome="source_error", message="新闻研究入口没有完成读取；未将失败标为空结果。", **safe_error(exc, "research_news"))
    return {"items": items, "accounts": [], "relations": [], "diagnostics": [diag]}


def collection_snapshot_is_terminal(summary: dict, phase="") -> bool:
    """A stopped collection can still supply evidence for an analysis-only retry."""
    return (summary.get("collection_complete") is True
            or summary.get("collection_outcome") in {"completed", "partial", "time_budget", "record_budget", "account_budget", "edge_budget", "partial_error", "blocked", "failed"}
            or (summary.get("phase") == "analysis" or phase == "analysis") and summary.get("collection_outcome") != "running")


async def run_research(params: dict[str, Any], progress=None, checkpoint=None, resume=None) -> dict[str, Any]:
    analysis_only = params.get("_analysis_only") is True
    execution_params = dict(params)
    if resume and not analysis_only:
        saved_scope = {**(resume.get("summary") or {}), **(resume.get("params") or {})}
        for field in ("max_records_per_account", "max_neighbors_per_account"):
            if field not in execution_params and field in saved_scope:
                execution_params[field] = saved_scope[field]
    options = ResearchParams.parse(execution_params)
    if analysis_only and (not resume or not collection_snapshot_is_terminal(resume.get("summary") or {}, resume.get("phase", ""))):
        raise PipelineError("仅重试分析需要已结束采集的保存快照。")
    plan = build_research_plan({**params, "keyword": options.keyword, "keyword_mode": options.keyword_mode, "source": options.source})
    account_scope = plan.get("scope") == "account"
    accounts, items, relations, nodes, deferred, diagnostics, decisions = {}, {}, {}, {}, {}, [], []
    excluded = set(params.get("_excluded_account_keys") or []) | set((resume or {}).get("excluded_account_keys") or [])
    excluded.update(row["account_key"] for row in (resume or {}).get("accounts", []) if row.get("status") == "excluded")
    from .account_relevance import describe_relevance
    from .graph_insights import explicit_root_keys
    explicit_roots = explicit_root_keys({**params, 'seed_accounts': list(options.seed_accounts)})
    registry_context = params.get('_registry_account_context') or {}
    registry_context = registry_context if isinstance(registry_context, dict) else {}
    excluded.update(key for key, row in registry_context.items() if isinstance(row, dict)
                    and describe_relevance(row)['research_relevance']['state'] == 'excluded')
    def apply_registry_context(value):
        from .account_identity import RESERVED_METADATA, describe_identity
        metadata = value.get('metadata')
        if isinstance(metadata, dict):
            # Public source data cannot forge a human include/exclude/type
            # before the repository's import guard is reached.
            value['metadata'] = {name: copy.deepcopy(child) for name, child in metadata.items() if name not in RESERVED_METADATA}
        trusted = registry_context.get(value.get('account_key'))
        if not isinstance(trusted, dict):
            return
        metadata = trusted.get('metadata')
        value['metadata'] = copy.deepcopy(metadata) if isinstance(metadata, dict) else {}
        known_identity = describe_identity(trusted)
        if known_identity['account_type'] != 'unknown' and not value['metadata'].get('account_identity'):
            value['metadata']['account_identity'] = {'version': 1, 'type': known_identity['account_type'],
                **known_identity['account_type_details']}
    visited, completed_queries, blocked_platforms = set(), set(), set()
    pending_seed_queries, failed_seed_queries = {}, set()
    attempted_accounts = set()
    ai_rounds, additional_queries = 0, []
    started = time.monotonic()
    def remaining_seconds():
        return options.time_budget_seconds - (time.monotonic() - started) if options.time_budget_seconds else math.inf
    wants_final_ai = options.analysis_mode == "ai" or (options.analysis_mode == "auto" and bool(os.getenv("DEEPSEEK_API_KEY", "").strip()))
    estimated_evidence = max(len((resume or {}).get("items") or []), options.public_limit("max_records") or options.max_records_per_account) + (options.public_limit("max_accounts") or 20) + (options.public_limit("max_edges") or 80)
    estimated_batches = max(1, math.ceil(estimated_evidence / 80))
    analysis_reserve = min(options.time_budget_seconds * .3, max(120.0, estimated_batches * 60.0)) if wants_final_ai and options.time_budget_seconds >= 60 else 0
    collection_seconds = options.time_budget_seconds - analysis_reserve if options.time_budget_seconds else None
    summary = {"source": options.source, "seed_sources": list(options.seed_sources), "start_date": options.start_date, "end_date": options.end_date,
               "depth": options.depth, "max_accounts": options.public_limit("max_accounts"), "max_edges": options.public_limit("max_edges"), "max_records": options.public_limit("max_records"),
               "time_budget_seconds": options.time_budget_seconds, "max_scrolls": options.max_scrolls,
               "max_records_per_account": options.max_records_per_account, "max_neighbors_per_account": options.max_neighbors_per_account,
               "expand_relations": list(options.expand_relations), "relevance": options.relevance,
               "analysis_catalog_scope": "all_saved_evidence", "research_goal": options.research_goal or options.keyword,
               "mode": options.mode, "seed_accounts": list(options.seed_accounts), "account_scope": account_scope,
               "collection_time_budget_seconds": collection_seconds, "final_analysis_reserve_seconds": analysis_reserve,
               "outcome": "running", "stop_reason": None, "ai_used": False, "seed_query_stats": {}, "deferred_frontier_version": 1, "pending_work_version": 1,
               "pending_seed_queries_version": 1,
               "coverage": "公开页面有限范围；日期、层数和每页读取范围固定。可选总量与总时长为 0 时不提前截断；关系或模型候选均不自动提升账号核验等级。"}
    if resume:
        previous = resume.get("query_plan") or {}
        old_summary = resume.get("summary") or {}
        if (previous.get("raw_keyword"), previous.get("combination"), old_summary.get("start_date"), old_summary.get("end_date")) != (plan["raw_keyword"], plan["combination"], options.start_date, options.end_date):
            raise PipelineError("续查必须保持原关键词组合及日期范围；请另建任务修改研究范围。")
        if "seed_accounts" in old_summary and (old_summary["seed_accounts"] != list(options.seed_accounts) or old_summary.get("mode") != options.mode):
            raise PipelineError("续查必须保持原指定账号与研究方式；请另建任务修改研究起点。")
        saved_scope = {**old_summary, **(resume.get("params") or {})}
        expected_scope = {"source": options.source, "seed_sources": list(options.seed_sources), "depth": options.depth,
                          "max_scrolls": options.max_scrolls, "expand_relations": list(options.expand_relations), "relevance": options.relevance,
                          "max_records_per_account": options.max_records_per_account, "max_neighbors_per_account": options.max_neighbors_per_account}
        if not analysis_only and any(key in saved_scope and saved_scope[key] != value for key, value in expected_scope.items()):
            raise PipelineError("续查必须保持原来源、层数、滚动范围、相关性与关系方向；请另建任务修改研究范围。")
        if "research_goal" in old_summary and old_summary["research_goal"] != (options.research_goal or options.keyword):
            raise PipelineError("续查必须保持原研究目标；请另建任务修改研究范围。")
        for row in resume.get("accounts", []):
            if row["account_key"] in excluded:
                continue
            accounts[row["account_key"]] = copy.deepcopy(row)
            apply_registry_context(accounts[row['account_key']])
        for row in resume.get("items", []):
            if row.get("account_key") in excluded:
                continue
            items[row["url"]] = copy.deepcopy(row)
        for row in resume.get("relations", []):
            if row["source_key"] in excluded or row["target_key"] in excluded:
                continue
            relations[(row["source_key"], row["target_key"], row["relation"])] = copy.deepcopy(row)
        for row in resume.get("nodes", []):
            if row["account_key"] in excluded:
                continue
            nodes[row["account_key"]] = copy.deepcopy(row)
        for row in resume.get("deferred_frontier", []):
            if row.get("account_key") in excluded:
                continue
            # A cached status cannot create an admitted or visited node. Only
            # the independently saved accounts/nodes/visited establish that.
            try:
                cached = copy.deepcopy(row)
                acc = cached["account"]
                canonical = account(acc["platform"], acc["url"])
                if (canonical["account_key"] != cached["account_key"] or acc["account_key"] != cached["account_key"]
                        or isinstance(cached["depth"], bool) or not isinstance(cached["depth"], int) or not 0 <= cached["depth"] <= RESEARCH_DEPTH_MAX
                        or not isinstance(cached.get("items", []), list) or not isinstance(cached.get("relations", []), list)):
                    raise ValueError()
                def public_reference(value):
                    if not isinstance(value, str):
                        return False
                    parsed = urlsplit(value)
                    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
                saved_admitted = cached["account_key"] in accounts
                if (isinstance(cached.get("score"), bool) or not isinstance(cached.get("score"), (int, float))
                        or not 0 <= cached["score"] <= 100
                        or not isinstance(cached.get("discovery_paths"), list)
                        or any(not isinstance(path, dict) or not path.get("evidence_urls")
                               or any(not public_reference(url) for url in path["evidence_urls"]) for path in cached["discovery_paths"])
                        or any(not isinstance(item, dict) or item.get("account_key") != cached["account_key"] or not public_reference(item.get("url"))
                               for item in cached.get("items", []))
                        or any(not isinstance(relation, dict) or relation.get("relation") not in {"follows", "mentions"}
                               or not (public_reference(relation.get("evidence_url")) or
                                       any(public_reference(url) for url in relation.get("evidence_urls", [])))
                               for relation in cached.get("relations", []))):
                    raise ValueError()
                if not saved_admitted and (not cached["discovery_paths"] or not cached.get("evidence_urls")):
                    raise ValueError()
                if saved_admitted and not cached["discovery_paths"] and not (cached.get("items") or cached.get("relations")):
                    continue
                cached["discovery_paths"] = [path for path in cached["discovery_paths"] if path.get("parent_key") not in excluded]
                if not cached["discovery_paths"] and not saved_admitted:
                    continue
                live_urls = {url for path in cached["discovery_paths"] for url in path["evidence_urls"]}
                cached["evidence_urls"] = [url for url in cached["evidence_urls"] if url in live_urls]
                cached["relations"] = [relation for relation in cached.get("relations", [])
                    if relation["source_key"] not in excluded and relation["target_key"] not in excluded]
                if cached.get("parent_key") in excluded:
                    if cached["discovery_paths"]:
                        first_path = min(cached["discovery_paths"], key=lambda path: path["depth"])
                        cached.update(parent_key=first_path["parent_key"], depth=first_path["depth"], discovery_reason=first_path["reason"])
                    else:
                        # Independently saved material of a real admitted
                        # account survives removal of its discoverer. Do not
                        # invent another parent or reinterpret the old path.
                        cached["parent_key"] = None
                cached["admission_state"] = "admitted" if cached["account_key"] in accounts else "deferred"
                if cached["account_key"] not in accounts:
                    cached["account"]["status"] = "candidate"
                deferred[cached["account_key"]] = cached
            except (KeyError, TypeError, ValueError):
                raise PipelineError("保存的待准入候选缺少有效公开账号或来源路径；未创建虚构账号。") from None
        diagnostics = copy.deepcopy(resume.get("diagnostics", []))
        decisions = copy.deepcopy(resume.get("decisions", []))
        visited = set(resume.get("visited", [])) - excluded
        if not analysis_only:
            for key, node in nodes.items():
                if (node.get('outcome') == 'irrelevant_account'
                        and not describe_relevance(accounts[key], key in explicit_roots)['research_relevance']['skip_expansion']):
                    visited.discard(key)
                    node.update(status='queued', outcome='relevance_restored')
                    decision_entry = {'id': stable_id('decision',len(decisions),'restore_relevance',now()), 'observed_at':now(),
                        'action':'restore_relevance', 'basis':'人工恢复已保存候选；重新读取此前未执行的公开分支。',
                        'result':{'account_key':key}, 'ai_used':False}
                    decisions.append(decision_entry)
        completed_queries = set(old_summary.get("completed_queries", []))
        ai_rounds = int(old_summary.get("ai_rounds", 0))
        additional_queries = copy.deepcopy(old_summary.get("additional_queries", []))
        summary["seed_query_stats"] = copy.deepcopy(old_summary.get("seed_query_stats", {}))
        summary["account_budget_skipped"] = int(old_summary.get("account_budget_skipped", 0))
        if (old_summary.get("frontier_recovery_required") is True
                or old_summary.get("account_budget_skipped", 0) and old_summary.get("deferred_frontier_version") != 1 and not deferred):
            summary.update(frontier_recovery_required=True, legacy_deferred_unknown=True)
        from .research_frontier import infer_missing_research_work
        if infer_missing_research_work(old_summary, execution_params).get("collection_recovery_required") is True:
            summary.update(collection_recovery_required=True, legacy_collection_work_unknown=True)
        if len(accounts) > options.max_accounts or len(items) > options.max_records or len(relations) > options.max_edges:
            raise PipelineError("已保存研究图超过本次预算；续查不能使用更小的预算。")
        if analysis_only:
            diagnostics = [row for row in diagnostics if row.get("stage") != "relationship_analysis"]
            summary.update(copy.deepcopy(old_summary))
            summary.update(outcome=old_summary.get("collection_outcome", "completed"), stop_reason=old_summary.get("collection_stop_reason", "frontier_exhausted"),
                           ai_used=False, analysis_only=True)
            summary.pop("analysis_sample_limit", None)
            summary.pop("analysis_sampled_items", None)
    prior_diagnostics_count = len(diagnostics)
    if not analysis_only:
        seed_summary = {**summary, "completed_queries": sorted(completed_queries), "additional_queries": additional_queries,
                        "pending_seed_queries": (resume or {}).get("summary", {}).get("pending_seed_queries", []),
                        "pending_seed_queries_version": (resume or {}).get("summary", {}).get("pending_seed_queries_version") if resume else 1}
        pending_seed_queries = {row["source"] + ":" + row["query_id"]: row
            for row in retained_seed_queries(execution_params, plan, seed_summary)}
        for key in pending_seed_queries:
            completed_queries.discard(key)
            completed_queries.discard(key + "-news-refill")

    def frontier():
        return sorted((copy.deepcopy(node) for key, node in nodes.items() if key not in visited), key=lambda row: (-row["score"], row["depth"], row["account_key"]))

    def deferred_frontier():
        return sorted(copy.deepcopy(list(deferred.values())), key=lambda row: (-row["score"], row["depth"], row["account_key"]))

    def deferred_counts():
        return {"deferred_count": sum(key not in accounts for key in deferred),
                "deferred_record_count": sum(len(row.get("items", [])) for row in deferred.values()),
                "deferred_relation_count": len({(relation["source_key"], relation["target_key"], relation["relation"], relation.get("evidence_url", ""))
                    for row in deferred.values() for relation in row.get("relations", [])})}

    def pending_work_counts():
        pending = [row.get("pending_work", {}) for key, row in nodes.items() if key not in visited]
        return {"pending_account_work_count": sum(any(work.get(field) for field in ("profile", "posts", "mentions", "neighbors")) for work in pending),
                "pending_posts_count": sum(bool(work.get("posts")) for work in pending),
                "pending_profile_count": sum(bool(work.get("profile")) for work in pending),
                "pending_relation_branch_count": sum(bool(work.get("mentions")) + len(work.get("neighbors", [])) for work in pending)}

    def decision(action, basis, result=None, ai=False, **extra):
        entry = {"id": stable_id("decision", len(decisions), action, now()), "observed_at": now(), "action": action, "basis": basis,
                 "result": result or {}, "ai_used": ai, **extra}
        decisions.append(entry)
        return entry

    def observe(node, depth, parent, reason, evidence_urls, score):
        observed_at = now()
        if not node:
            node.update(depth=depth, parent_key=parent, discovery_reason=reason, evidence_urls=[], score=score,
                        status="queued", discovery_count=0, observed_at=observed_at, discovery_paths=[])
        if evidence_urls:
            new_evidence = set(evidence_urls) - set(node.get("evidence_urls", []))
            paths = node.setdefault("discovery_paths", [])
            path = next((row for row in paths if (row.get("parent_key"), row.get("reason"), row.get("depth")) == (parent, reason, depth)), None)
            fresh_path = path is None
            if fresh_path:
                path = {"parent_key": parent, "reason": reason, "depth": depth, "evidence_urls": [],
                        "observed_at": observed_at, "first_observed_at": observed_at, "last_observed_at": observed_at}
                paths.append(path)
            else:
                path["last_observed_at"] = observed_at
            path["evidence_urls"] = list(dict.fromkeys(path.get("evidence_urls", []) + evidence_urls))
            if new_evidence or fresh_path:
                node["discovery_count"] = node.get("discovery_count", 0) + 1
            node["score"] = min(100, max(node.get("score", 0), score) + (3 if node["discovery_count"] > 1 and (new_evidence or fresh_path) else 0))
            node["evidence_urls"] = list(dict.fromkeys(node.get("evidence_urls", []) + evidence_urls))
        if depth < node["depth"]:
            node.update(depth=depth, parent_key=parent, discovery_reason=reason)

    def cache_candidate(acc, depth, parent, reason, evidence_urls, score, defer_reason="account_budget"):
        key = acc["account_key"]
        if key not in deferred:
            node = {}
            observe(node, depth, parent, reason, evidence_urls, score)
            deferred[key] = {**node, "account_key": key, "account": copy.deepcopy(acc), "items": [], "relations": []}
        else:
            node = deferred[key]
            observe(node, depth, parent, reason, evidence_urls, score)
            node["account"].update({field: copy.deepcopy(value) for field, value in acc.items() if value and not node["account"].get(field)})
        deferred[key].update(admission_state="admitted" if key in accounts else "deferred", status="deferred", defer_reason=defer_reason)
        return deferred[key]

    def admit(acc, depth, parent, reason, evidence_urls, score, *, replay=False):
        key = acc["account_key"]
        if key in excluded or acc.get("status") == "excluded":
            excluded.add(key)
            summary["excluded_discovery_skipped"] = summary.get("excluded_discovery_skipped", 0) + 1
            return False
        if depth > options.depth:
            cache_candidate(acc, depth, parent, reason, evidence_urls, score, "depth_budget")
            return False
        if key not in accounts and len(accounts) >= options.max_accounts:
            summary["account_budget_skipped"] = summary.get("account_budget_skipped", 0) + 1
            cache_candidate(acc, depth, parent, reason, evidence_urls, score)
            return False
        if key in accounts:
            accounts[key].update({field: value for field, value in acc.items() if field == "profile" or (value and not accounts[key].get(field))})
        else:
            accounts[key] = copy.deepcopy(acc)
            accounts[key]["status"] = "candidate"
        apply_registry_context(accounts[key])
        if key not in nodes:
            cached = deferred.get(key)
            nodes[key] = {field: copy.deepcopy(value) for field, value in (cached or {}).items()
                          if field not in {"account", "items", "relations", "admission_state", "defer_reason"}}
            if nodes[key]:
                nodes[key]["status"] = "queued"
            if not replay or not nodes[key]:
                observe(nodes[key], depth, parent, reason, evidence_urls, score)
            nodes[key]["account_key"] = key
        else:
            if not replay:
                observe(nodes[key], depth, parent, reason, evidence_urls, score)
        if key in deferred:
            deferred[key]["admission_state"] = "admitted"
        return True

    def merge_relation_observation(previous, observed):
        if observed.get("evidence_url") and ((observed.get("evidence_url"), observed.get("observed_at")) !=
                (previous.get("evidence_url"), previous.get("observed_at"))):
            observations = previous.setdefault("observations", [])
            if not observations:
                observations.append({field: copy.deepcopy(value) for field, value in previous.items() if field not in {"observations", "evidence_urls"}})
            if observed not in observations:
                observations.append(copy.deepcopy(observed))
            previous["evidence_urls"] = list(dict.fromkeys([previous.get("evidence_url", ""), *previous.get("evidence_urls", []), observed["evidence_url"]]))
            return True
        return False

    def merge(result, depth, parent, reason, query=None):
        new = {"items": [], "accounts": [], "relations": []}
        incoming = {row["account_key"]: row for row in result.get("accounts", [])}
        for item in result.get("items", []):
            if item["url"] in items:
                continue
            key = item.get("account_key")
            row = copy.deepcopy(item)
            row["evidence_id"] = row.get("evidence_id") or stable_id("post" if key else "news", row["url"])
            row.setdefault("metadata", {}).update(research_depth=depth, discovery_parent=parent, discovery_reason=reason,
                discovery_query=query, research_observed_at=now())
            if key:
                if key in excluded:
                    continue
                acc = incoming.get(key) or accounts.get(key)
                if not acc:
                    try:
                        platform, handle = key.split(":", 1)
                        acc = account(platform, handle)
                    except ValueError:
                        continue
                admitted = admit(acc, depth, parent, reason, [item["url"]], 80)
                if key in excluded:
                    continue
                if not admitted or len(items) >= options.max_records:
                    cached = cache_candidate(acc, depth, parent, reason, [item["url"]], 80,
                                             "record_budget" if admitted else "account_budget" if depth <= options.depth else "depth_budget")
                    if row["url"] not in {value["url"] for value in cached["items"]}:
                        cached["items"].append(row)
                    continue
            elif len(items) >= options.max_records:
                continue
            items[row["url"]] = row
            new["items"].append(row)
            if key:
                new["accounts"].append(copy.deepcopy(accounts[key]))
            else:
                explicit = (row.get("metadata") or {}).get("linked_accounts", []) + linked_accounts(row.get("content", ""))
                for acc in explicit:
                    if admit(acc, 0, None, "news_explicit_account_link", [row["url"]], 75):
                        new["accounts"].append(copy.deepcopy(accounts[acc["account_key"]]))
        for acc in result.get("accounts", []):
            if acc["account_key"] in accounts:
                admit(acc, nodes[acc["account_key"]]["depth"], parent, reason, [], nodes[acc["account_key"]]["score"])
        for relation in result.get("relations", []):
            if relation["source_key"] in excluded or relation["target_key"] in excluded:
                continue
            identity = (relation["source_key"], relation["target_key"], relation["relation"])
            if identity in relations:
                if merge_relation_observation(relations[identity], relation):
                    new["relations"].append(copy.deepcopy(relations[identity]))
            elif len(relations) < options.max_edges and relation["source_key"] in accounts and relation["target_key"] in accounts:
                row = copy.deepcopy(relation)
                row["evidence_id"] = row.get("evidence_id") or stable_id("list", row["source_key"], row["target_key"], row.get("evidence_url", ""))
                relations[identity] = row
                new["relations"].append(row)
            elif identity not in relations:
                # Retain the actual direction, typed relation, timestamp and
                # observed list URL even if either endpoint is not admitted.
                key = next((key for key in (relation["source_key"], relation["target_key"]) if key in deferred), None)
                if key is None:
                    key = next((key for key in (relation["target_key"], relation["source_key"]) if key in accounts), None)
                    if key:
                        source_node = nodes[key]
                        cache_candidate(accounts[key], source_node["depth"], source_node.get("parent_key"),
                                        source_node["discovery_reason"], [relation.get("evidence_url", "")], source_node["score"], "edge_budget")
                if key:
                    cached = deferred[key]
                    if relation not in cached["relations"]:
                        cached["relations"].append(copy.deepcopy(relation))
        diagnostics.extend(copy.deepcopy(result.get("diagnostics", [])))
        return new

    def drain_deferred():
        delta = {"items": [], "accounts": [], "relations": []}
        for cached in deferred_frontier():
            key = cached["account_key"]
            if key not in accounts and cached["depth"] <= options.depth and len(accounts) < options.max_accounts:
                if admit(cached["account"], cached["depth"], cached.get("parent_key"), cached["discovery_reason"], cached["evidence_urls"], cached["score"], replay=True):
                    delta["accounts"].append(copy.deepcopy(accounts[key]))
                    decision("admit_deferred_account", "沿已保存的公开来源路径准入候选；未重读已完成账号。",
                             {"account_key": key, "depth": cached["depth"], "evidence_urls": cached["evidence_urls"]})
        for key in list(deferred):
            cached = deferred[key]
            pending_items = []
            for row in cached.get("items", []):
                if row["url"] in items:
                    continue
                if key in accounts and len(items) < options.max_records:
                    items[row["url"]] = copy.deepcopy(row)
                    delta["items"].append(copy.deepcopy(row))
                else:
                    pending_items.append(row)
            cached["items"] = pending_items
            pending_relations = []
            for row in cached.get("relations", []):
                if row["source_key"] in excluded or row["target_key"] in excluded:
                    continue
                identity = (row["source_key"], row["target_key"], row["relation"])
                if identity in relations:
                    if merge_relation_observation(relations[identity], row):
                        delta["relations"].append(copy.deepcopy(relations[identity]))
                    continue
                if len(relations) < options.max_edges and row["source_key"] in accounts and row["target_key"] in accounts:
                    value = copy.deepcopy(row)
                    value["evidence_id"] = value.get("evidence_id") or stable_id("list", value["source_key"], value["target_key"], value.get("evidence_url", ""))
                    relations[identity] = value
                    delta["relations"].append(value)
                else:
                    pending_relations.append(row)
            cached["relations"] = pending_relations
            if key in accounts and not pending_items and not pending_relations:
                del deferred[key]
        return delta

    async def persist(kind, delta=None, analysis=None):
        summary.update(phase=kind, frontier_count=len(frontier()), completed_query_count=len(completed_queries), planned_query_count=len(plan.get("queries") or []) * len(options.seed_sources), account_count=len(accounts), record_count=len(items), edge_count=len(relations), visited_count=len(visited),
                        elapsed_seconds=round(time.monotonic() - started, 2), completed_queries=sorted(completed_queries), ai_rounds=ai_rounds,
                        additional_queries=copy.deepcopy(additional_queries), **deferred_counts(), **pending_work_counts())
        if not analysis_only:
            summary.update(pending_seed_queries=copy.deepcopy(list(pending_seed_queries.values())),
                           pending_seed_query_count=len(pending_seed_queries), pending_seed_queries_version=1)
        event = {"kind": kind, "items": (delta or {}).get("items", []), "accounts": (delta or {}).get("accounts", []),
                 "relations": (delta or {}).get("relations", []), "nodes": copy.deepcopy(list(nodes.values())),
                  "diagnostics": copy.deepcopy(diagnostics), "decisions": copy.deepcopy(decisions), "frontier": frontier(),
                  "deferred_frontier": deferred_frontier(), "visited": sorted(visited),
                 "query_plan": copy.deepcopy(plan), "summary": copy.deepcopy(summary)}
        if analysis is not None:
            event["analysis"] = analysis
        if checkpoint:
            returned = checkpoint(event)
            if inspect.isawaitable(returned):
                await returned

    async def adapt_frontier():
        nonlocal ai_rounds
        pending = frontier()
        wants_ai = options.analysis_mode == "ai" or (options.analysis_mode == "auto" and bool(os.getenv("DEEPSEEK_API_KEY", "").strip()))
        round_limit = 12 if account_scope else 3
        if not wants_ai or ai_rounds >= round_limit or (not pending and not decisions) or remaining_seconds() < 25:
            return
        if account_scope:
            from .account_exploration import ACCOUNT_PRIORITY_PROMPT, account_frontier_context, validate_account_priorities
            eligible = [row for row in pending if row['account_key'] not in attempted_accounts
                        and accounts[row['account_key']]['platform'] not in blocked_platforms
                        and not describe_relevance(accounts[row['account_key']], row['account_key'] in explicit_roots)['research_relevance']['skip_expansion']]
            if len(eligible) < 2:
                return
            context = account_frontier_context(eligible, list(accounts.values()), list(items.values()), list(relations.values()),
                research_goal=options.research_goal or '从指定公开账号开始，核对可继续读取的多层关系路径。')
            if not any(row['supporting_evidence_ids'] for row in context['candidate_accounts']):
                return
            ai_rounds += 1
            try:
                response = await configured_ai_json(ACCOUNT_PRIORITY_PROMPT, context, max_tokens=8192,
                    timeout_limit=min(45, max(1, remaining_seconds())))
                priorities = validate_account_priorities(response, context)
                for row in priorities:
                    nodes[row['account_key']].update(score=row['priority'], priority_basis=row['reason'],
                        priority_method='configured_ai_account_scope', priority_evidence_ids=row['evidence_ids'])
                summary.update(ai_used=True, account_priority_rounds=ai_rounds, account_priority_round_limit=round_limit)
                cited = {eid for row in priorities for eid in row['evidence_ids']}
                retained_sources = [{**{k: value for k, value in source.items() if k != 'excerpt'},
                    'excerpt': source.get('excerpt', '')[:240],
                    'excerpt_truncated': source.get('excerpt_truncated', False) or len(source.get('excerpt', '')) > 240}
                    for source in context['evidence'] if source['id'] in cited]
                decision('reprioritize_account_frontier', '已发现候选的公开来源、发现路径与未读分支；未选择账号仍保留。',
                    {'priorities': priorities, 'candidate_count': len(context['candidate_accounts']),
                     'evidence': retained_sources, 'coverage': context['coverage']}, ai=True)
                await persist('account')
            except PipelineError as exc:
                decision('ai_decision_failed', str(exc), {'fallback': '可解释规则排序', 'round': ai_rounds, 'scope': 'account'}, ai=False)
            return
        ai_rounds += 1
        try:
            response = await configured_ai_json(
                '决定公开证据研究的下一步优先级。返回 {"priorities":[{"account_key":"候选ID", "priority":0至100整数, "reason":"依据"}], "suggested_keywords":[{"keyword":"相关普通词组", "reason":"依据"}]}。只可重排给定候选，不能创建账号、关系或组织隶属。最多10个优先级和2个关键词。不要修改用户日期、总预算或研究主题。仅基于给定公开证据，资料少需保守，不从关注推支持。',
                {"research_goal": options.research_goal or options.keyword, "original_groups": plan["groups"],
                 "candidate_accounts": [{"account_key": row["account_key"], "depth": row["depth"], "score": row["score"], "discovery_reason": row["discovery_reason"],
                    "evidence_urls": row["evidence_urls"]} for row in pending[:30]],
                 "recent_evidence": [{"evidence_id": row.get("evidence_id"), "account_key": row.get("account_key"), "content": str(row.get("content", ""))[:600]} for row in list(items.values())[-15:]],
                 "recent_results": decisions[-8:]}, max_tokens=8192, timeout_limit=45)
            if not isinstance(response, dict) or set(response) != {"priorities", "suggested_keywords"} or not isinstance(response["priorities"], list) or len(response["priorities"]) > 10 or not isinstance(response["suggested_keywords"], list) or len(response["suggested_keywords"]) > 2:
                raise PipelineError("AI 行动建议不符合限定格式。")
            allowed = {row["account_key"] for row in pending[:30]}
            for row in response["priorities"]:
                if not isinstance(row, dict) or set(row) != {"account_key", "priority", "reason"} or row["account_key"] not in allowed or isinstance(row["priority"], bool) or not isinstance(row["priority"], int) or not 0 <= row["priority"] <= 100 or not isinstance(row["reason"], str) or not 1 <= len(row["reason"]) <= 180:
                    raise PipelineError("AI 行动建议引用了未知候选或不合法优先级。")
            extras = []
            for row in response["suggested_keywords"]:
                if not isinstance(row, dict) or set(row) != {"keyword", "reason"} or not isinstance(row["reason"], str) or not 1 <= len(row["reason"]) <= 180:
                    raise PipelineError("AI 拓展关键词不符合限定格式。")
                groups = parse_keyword_groups(row["keyword"])
                if len(groups) != 1 or len(row["keyword"]) > 80:
                    raise PipelineError("AI 拓展关键词必须是一个有界普通关键词组。")
                build_query_plan({"source": options.source, "keyword": groups[0]["text"], "mode": "hashtag" if options.mode == "hashtag" else "keyword"})
                if not any(query["keyword"] == groups[0]["text"] for query in plan["queries"] + additional_queries):
                    extras.append({"keyword": groups[0]["text"], "reason": row["reason"], "method": "configured_ai", "id": stable_id("adaptive-query", groups[0]["text"])})
            for row in response["priorities"]:
                nodes[row["account_key"]].update(score=row["priority"], priority_basis=row["reason"], priority_method="configured_ai")
            for extra in extras:
                if len(additional_queries) < 3:
                    additional_queries.append(extra)
            summary["ai_used"] = True
            decision("reprioritize_frontier", "现有公开证据与已知候选；已校验模型不能添加账号或关系。", response, ai=True)
        except PipelineError as exc:
            decision("ai_decision_failed", str(exc), {"fallback": "可解释规则排序", "round": ai_rounds}, ai=False)

    def topical(item):
        # Research follows the user's present topic, not the historical peace
        # taxonomy. A direct body match is traceable even for labour/student
        # themes. An unmatched item still requires explicit semantic evidence.
        if account_scope:
            matched = bool(str(item.get("content") or "").strip())
            reason = "指定账号及公开关系路径的原帖；未设置主题过滤。"
            if matched:
                item.setdefault("metadata", {}).update(relevance_basis="account_scope", relevance_reason=reason)
            return {"matched": matched, "mode": options.relevance, "reasons": [reason]}
        matched = query_matches(plan, item.get("content", ""))
        if matched:
            item.setdefault("metadata", {}).update(relevance_basis="query_match", relevance_reason="原帖正文满足本次用户关键词组。")
        return {"matched": matched, "mode": options.relevance, "reasons": ["原帖正文满足本次用户关键词组。" if matched else "正文未直接匹配本次研究主题，需要单独语义证据才可收录。"]}

    async def review_semantics(profile_data, candidates):
        nonlocal ai_rounds
        profile_match = bool(profile_data.get("verified_in_dom") and (account_scope or query_matches(plan, profile_data.get("bio", ""))))
        accepted, review = [], []
        for row in candidates:
            if topical(row)["matched"]:
                accepted.append(row)
            else:
                review.append({"evidence_id": stable_id("post", row["url"]), "type": "post", "url": row["url"], "text": str(row.get("content", ""))[:1800]})
        profile_id = None
        if profile_data.get("verified_in_dom") and profile_data.get("bio") and not profile_match:
            profile_id = stable_id("profile-relevance", profile_data.get("url"), profile_data["bio"])
            review.append({"evidence_id": profile_id, "type": "profile", "url": profile_data["url"], "text": profile_data["bio"][:1800]})
        wants_model = options.analysis_mode == "ai" or (options.analysis_mode == "auto" and bool(os.getenv("DEEPSEEK_API_KEY", "").strip()))
        if not review or not wants_model or ai_rounds >= 3 or remaining_seconds() < 25:
            return accepted, profile_match
        ai_rounds += 1
        supplied = {row["evidence_id"]: row for row in review[:30]}
        try:
            response = await configured_ai_json(
                '判断公开正文是否与用户当前研究关键词/目标明确相关，不受固定和平词表限制。关注关系、账号名相似和推荐列表不是相关性证据。focused要求同一议题；broad可接受与明确目标直接相邻的议题，但不能接受无关体育、营销或泛泛口号。返回 {"relevance":[{"evidence_id":"输入ID", "matched":true或false, "reason":"理由", "quote":"正文中的精确片段"}]}。仅引用输入ID。matched=true必须提供正文原样短引文，不能凭空补充事实。不要判断账号身份、政治隶属或幕后控制。',
                {"research_goal": options.research_goal or options.keyword, "keyword_groups": plan["groups"], "relevance_mode": options.relevance,
                 "public_evidence": list(supplied.values())}, max_tokens=8192, timeout_limit=45)
            if not isinstance(response, dict) or set(response) != {"relevance"} or not isinstance(response["relevance"], list) or len(response["relevance"]) > len(supplied):
                raise PipelineError("AI 语义相关性没有符合限定格式。")
            verified = {}
            for row in response["relevance"]:
                if not isinstance(row, dict) or set(row) != {"evidence_id", "matched", "reason", "quote"} or row["evidence_id"] not in supplied or row["evidence_id"] in verified or not isinstance(row["matched"], bool) or not isinstance(row["reason"], str) or not 1 <= len(row["reason"]) <= 240 or not isinstance(row["quote"], str) or len(row["quote"]) > 160:
                    raise PipelineError("AI 语义相关性引用了未知证据或不合法判断。")
                if row["matched"] and (not row["quote"].strip() or row["quote"] not in supplied[row["evidence_id"]]["text"]):
                    raise PipelineError("AI 语义相关性正向判断缺少正文原样引文；未采用结果。")
                verified[row["evidence_id"]] = row
            for row in candidates:
                eid = stable_id("post", row["url"])
                result = verified.get(eid)
                if result and result["matched"]:
                    row.setdefault("metadata", {}).update(relevance_basis="ai_semantic", relevance_reason=result["reason"],
                        relevance_quote=result["quote"], relevance_evidence_id=eid, relevance_boundary="AI 语义判断，不等于身份或隶属核验。")
                    accepted.append(row)
            if profile_id in verified and verified[profile_id]["matched"]:
                result = verified[profile_id]
                profile_match = True
                profile_data.update(relevance_basis="ai_semantic", relevance_reason=result["reason"], relevance_quote=result["quote"], relevance_evidence_id=profile_id)
            summary["ai_used"] = True
            decision("review_semantic_evidence", "原关键词与明确研究目标；正向判断已核对证据ID和正文原样引文。",
                     {"reviewed": len(supplied), "accepted": sum(row["matched"] for row in verified.values()), "round": ai_rounds}, ai=True)
        except PipelineError as exc:
            decision("ai_semantic_review_failed", str(exc), {"fallback": "仅保留正文直接匹配的证据", "round": ai_rounds})
        return accepted, profile_match

    async def execute_query(adapter, source, query, limit, adaptive=False):
        query_key = source + ":" + query["id"]
        if query_key in completed_queries or source in blocked_platforms or len(items) >= options.max_records:
            return
        pending_seed_queries.setdefault(query_key, {"source": source, "query_id": query["id"], "keyword": query["keyword"],
            "adaptive": adaptive, "retained_prefix": 0, "requested_limit": 0, "returned_count": 0, "scanned": 0, "failed": False})
        branch_plan = build_query_plan({"source": source, "keyword": query["keyword"], "start_date": options.start_date, "end_date": options.end_date,
                                        "mode": "hashtag" if options.mode == "hashtag" else "keyword"})
        def seed_gate(item):
            target_plan = branch_plan if adaptive else plan
            if not query_matches(target_plan, item.get("content", "")):
                return {"matched": False, "reasons": ["正文不满足本次关键词组。"]}
            return topical(item)
        await emit(progress, stage="seed_query", current=len(completed_queries), total=len(plan.get("queries") or []) * len(options.seed_sources), message=f"正在查询 {source}：{query['keyword']}；按已确认的日期与读取范围执行。")
        # Retain the pending identity before starting an interruptible read.
        await persist("seed")
        try:
            # A interrupted prefix must be read again with room for both its
            # saved rows and the remaining quota. Asking only for the quota
            # would repeatedly return the saved prefix and miss its tail.
            retained_urls = {row["url"] for row in [*items.values(), *(row for cached in deferred.values() for row in cached.get("items", []))]
                             if (row.get("metadata") or {}).get("discovery_query") == query["keyword"]
                             and ((source == "google_news" and row.get("type") == "news") or str(row.get("account_key") or "").startswith(source + ":"))}
            retained_prefix = max(len(retained_urls), pending_seed_queries[query_key]["retained_prefix"])
            request_limit = min(options.max_records, options.max_records_per_account if "max_records" in options.unlimited_limits else max(1, limit) + retained_prefix)
            branch_budget = min(research_branch_seconds(options.max_scrolls), max(1, remaining_seconds()))
            # The collector returns its partial evidence before this outer
            # timeout, leaving a little time to close a delayed page operation.
            branch_options = replace(options, time_budget_seconds=max(0.1, branch_budget - 3))
            async with asyncio.timeout(branch_budget):
                result = await adapter.search(source, query["keyword"], branch_options, request_limit, seed_gate)
            returned_count = len(result.get("items", []))
            if source == "google_news":
                result["items"] = [row for row in result.get("items", []) if query_matches(branch_plan if adaptive else plan, f"{row.get('title', '')}\n{row.get('content', '')}")]
            delta = merge(result, 0, None, "ai_related_query" if adaptive else "keyword_seed", query["keyword"])
            outcomes = [row.get("outcome") for row in result.get("diagnostics", [])]
            blocked = next((value for value in outcomes if value in BLOCKING), None)
            if blocked:
                blocked_platforms.add(source)
            failed = any(row.get("ok") is False for row in result.get("diagnostics", []))
            scope_stops = {row.get("scan_stop_reason") for row in result.get("diagnostics", [])}
            scope_complete = not failed and not scope_stops & {"record_budget", "time_budget"} and bool(scope_stops & {"explicit_empty", "candidate_budget", "no_new_content", "scroll_budget"})
            if not failed and "max_records" in options.unlimited_limits and request_limit >= options.max_records_per_account:
                # The confirmed per-query prefix is a scope boundary, not an
                # unfinished global record allowance or proof of exhaustion.
                scope_complete = True
            exhausted = not failed and returned_count < request_limit and not scope_stops & {"record_budget", "time_budget"}
            if exhausted or scope_complete:
                completed_queries.add(query_key)
                pending_seed_queries.pop(query_key, None)
            else:
                completed_queries.discard(query_key)
                pending_seed_queries[query_key].update(retained_prefix=max(retained_prefix, returned_count), requested_limit=request_limit,
                                                       returned_count=returned_count, failed=failed)
            if failed:
                failed_seed_queries.add(query_key)
            else:
                failed_seed_queries.discard(query_key)
            scanned = sum(row.get("scanned", row.get("fetched", 0)) for row in result.get("diagnostics", []))
            summary["seed_query_stats"][query_key] = {"requested_limit": request_limit, "returned_count": returned_count,
                "new_records": len(delta["items"]), "scanned": scanned, "exhausted": exhausted, "scope_complete": scope_complete, "failed": failed}
            if query_key in pending_seed_queries:
                pending_seed_queries[query_key]["scanned"] = scanned
            decision("seed_query", "用户关键词分组" if not adaptive else query.get("reason", "模型建议的相关入口"),
                     {"source": source, "keyword": query["keyword"], "new_records": len(delta["items"]), "scanned": scanned,
                      "next": "发现作者后按主题证据排序" if delta["items"] else "停止该空结果分支，转向其他关键词或平台", "blocked": blocked}, ai=adaptive)
            await persist("seed", delta)
        except SocialError as exc:
            failed_seed_queries.add(query_key)
            pending_seed_queries[query_key]["failed"] = True
            if exc.code in BLOCKING:
                blocked_platforms.add(source)
            diagnostics.append({"platform": source, "stage": "seed", "ok": False, "outcome": exc.code, "message": exc.message, **exc.diagnostic})
            decision("stop_seed_branch", exc.message, {"source": source, "keyword": query["keyword"], "outcome": exc.code})
            await persist("seed")
        except TimeoutError:
            failed_seed_queries.add(query_key)
            pending_seed_queries[query_key]["failed"] = True
            diagnostics.append({"platform": source, "stage": "seed", "ok": False, "outcome": "timeout", "message": "本关键词分支达到有界等待预算；未将未加载页面标为成功。"})
            decision("stop_seed_branch", "有界等待超时", {"source": source, "keyword": query["keyword"]})
            await persist("seed")

    try:
        async with asyncio.timeout(collection_seconds):
            if analysis_only:
                decision("retry_analysis", "使用已结束采集的保存快照，仅继续最终 AI 分析；保留原采集范围与预算终态。")
                await persist("analysis")
            else:
                async with BrowserResearchAdapter() as adapter:
                    for seed in options.seed_accounts:
                        platform = "instagram" if urlsplit(seed).hostname in {"instagram.com", "www.instagram.com"} else "twitter"
                        entry = account(platform, seed)
                        admit(entry, 0, None, "user_supplied_account", [entry["url"]], 90)
                    resumed_delta = drain_deferred()
                    resumed_delta["accounts"] = list(accounts.values())
                    await persist("seed", resumed_delta)
                    if options.mode != "account":
                        # Reserve evidence slots for the vertical traversal rather
                        # than letting the first several seed queries consume the
                        # entire task. At least one result per explicit branch.
                        branches = [(source, query) for query in plan["queries"] for source in options.seed_sources]
                        social_selected = any(source in {"twitter", "instagram"} for source in options.seed_sources)
                        seed_limit = options.max_records_per_account if "max_records" in options.unlimited_limits else max(1, (options.max_records // 3) // max(1, len(branches)))
                        for index, (source, query) in enumerate(branches):
                            remaining_records = max(1, options.max_records - len(items))
                            if "max_records" in options.unlimited_limits:
                                quota = options.max_records_per_account
                            elif not social_selected:
                                quota = max(1, (remaining_records + len(branches) - index - 1) // (len(branches) - index))
                            elif source == "google_news" and not any(acc["platform"] in options.seed_sources for acc in accounts.values()):
                                quota = max(1, remaining_records // (len(branches) - index))
                            else:
                                quota = seed_limit
                            await execute_query(adapter, source, query, min(quota, remaining_records))
                    await adapt_frontier()
                    for extra in additional_queries:
                        for source in options.seed_sources:
                            await execute_query(adapter, source, extra, min(3, max(1, options.max_records - len(items))), adaptive=True)
                    visits_since_ai = 0
                    seed_refill_done = False
                    while True:
                        if collection_seconds is not None and time.monotonic() - started >= collection_seconds:
                            raise TimeoutError()
                        for candidate in frontier():
                            platform = accounts[candidate["account_key"]]["platform"]
                            relevance_state = describe_relevance(accounts[candidate['account_key']], candidate['account_key'] in explicit_roots)['research_relevance']
                            if relevance_state['skip_expansion']:
                                nodes[candidate['account_key']].update(status='skipped', outcome='irrelevant_account', stop_reason=relevance_state['reason'])
                                visited.add(candidate['account_key'])
                                decision('retain_unexpanded_general_account', relevance_state['reason'],
                                    {'account_key': candidate['account_key'], 'basis': relevance_state['basis'], 'sources_retained': True})
                                continue
                            if platform not in options.seed_sources:
                                nodes[candidate["account_key"]].update(status="skipped", outcome="platform_not_selected",
                                    stop_reason="未选择该平台，保留新闻中的公开账号链接线索，未打开该平台。")
                                visited.add(candidate["account_key"])
                                decision("retain_unselected_platform_link", "用户限定可拓展来源", {"account_key": candidate["account_key"], "platform": platform})
                        pending = [row for row in frontier() if accounts[row["account_key"]]["platform"] not in blocked_platforms and row["account_key"] not in attempted_accounts]
                        if not pending:
                            if not seed_refill_done and pending_seed_queries and len(items) < options.max_records:
                                seed_refill_done = True
                                refillable = [copy.deepcopy(row) for key, row in pending_seed_queries.items()
                                              if row["source"] not in blocked_platforms and key not in failed_seed_queries]
                                for index, saved_query in enumerate(refillable):
                                    remaining_records = options.max_records - len(items)
                                    if remaining_records <= 0:
                                        break
                                    quota = options.max_records_per_account if "max_records" in options.unlimited_limits else max(1, remaining_records // max(1, len(refillable) - index))
                                    query = {"id": saved_query["query_id"], "keyword": saved_query["keyword"]}
                                    decision("reallocate_news_budget" if saved_query["source"] == "google_news" else "reallocate_seed_budget",
                                             "可访问账号前沿已耗尽，将剩余记录预算公平分配给原范围中尚未读完的查询前缀；每组本轮只补充一次。",
                                             {"source": saved_query["source"], "keyword": query["keyword"], "remaining_records": remaining_records,
                                              "request_limit": min(options.max_records, saved_query["retained_prefix"] + quota)})
                                    await execute_query(adapter, saved_query["source"], query, quota, adaptive=saved_query["adaptive"])
                                if any(accounts[row["account_key"]]["platform"] in options.seed_sources and accounts[row["account_key"]]["platform"] not in blocked_platforms
                                       and row["account_key"] not in attempted_accounts for row in frontier()):
                                    continue
                            break
                        node = nodes[pending[0]["account_key"]]
                        key = node["account_key"]
                        entry = accounts[key]
                        platform, handle = entry["platform"], entry["handle"]
                        work = node.setdefault("pending_work", {"profile": True, "posts": True, "mentions": False, "neighbors": []})
                        # A completed saved branch is never reread to repair a
                        # later budget stop in a different branch.
                        if work.get("posts") and len(items) >= options.max_records:
                            node.update(status="queued", outcome="record_budget")
                            break
                        attempted_accounts.add(key)
                        node["status"] = "processing"
                        await persist("account")
                        await emit(progress, stage="account", current=len(visited), total=options.max_accounts, account_key=key,
                                   depth=node["depth"], message=f"第 {node['depth']} 层：读取 @{handle} 的公开资料与主题证据。")
                        delta = {"items": [], "accounts": [], "relations": []}
                        profile_error = None
                        account_incomplete = False
                        if work.get("profile"):
                            try:
                                profile = await adapter.profile(platform, handle)
                                accounts[key].update({field: value for field, value in profile.items() if field not in {"status", "organization", "tags"}})
                                work["profile"] = False
                                delta["accounts"].append(copy.deepcopy(accounts[key]))
                                await persist("account", {"accounts": [copy.deepcopy(accounts[key])]})
                            except SocialError as exc:
                                profile_error = exc.code
                                account_incomplete = True
                                diagnostics.append({"platform": platform, "stage": "profile", "account_key": key, "ok": False, "outcome": exc.code, "message": exc.message, **exc.diagnostic})
                                if exc.code in BLOCKING:
                                    blocked_platforms.add(platform)
                        timeline_prefix = sum(1 for row in items.values() if row.get("account_key") == key and (row.get("metadata") or {}).get("discovery_reason") == "profile_timeline")
                        if timeline_prefix >= options.max_records_per_account:
                            work["posts"] = False
                        if work.get("posts") and platform not in blocked_platforms and len(items) < options.max_records and timeline_prefix < options.max_records_per_account:
                            model_available = options.analysis_mode == "ai" or (options.analysis_mode == "auto" and bool(os.getenv("DEEPSEEK_API_KEY", "").strip()))
                            semantic_possible = model_available and ai_rounds < 3 and remaining_seconds() >= 25
                            def candidate_gate(item):
                                direct = topical(item)
                                return direct if direct["matched"] or not semantic_possible else {"matched": True, "reasons": ["暂存为语义复核候选，尚未收录。"]}
                            limit = min(options.max_records_per_account - timeline_prefix, options.max_records - len(items))
                            branch_budget = min(research_branch_seconds(options.max_scrolls), max(1, remaining_seconds()))
                            branch_options = replace(options, time_budget_seconds=max(0.1, branch_budget - 3))
                            result = await adapter.posts(platform, handle, branch_options, min(options.max_records, (timeline_prefix + limit) * (3 if semantic_possible else 1)), candidate_gate)
                            post_failed = any(row.get("ok") is False for row in result.get("diagnostics", []))
                            account_incomplete = account_incomplete or post_failed
                            returned_count = len(result.get("items", []))
                            global_truncated = limit < options.max_records_per_account - timeline_prefix and returned_count >= timeline_prefix + limit
                            if not post_failed and not global_truncated:
                                work["posts"] = False
                            profile_hit = False
                            candidates = [row for row in result.get("items", []) if row.get("url") not in items]
                            result["items"], profile_hit = await review_semantics(accounts[key].get("profile") or {}, candidates)
                            result["items"] = result["items"][:limit]
                            if len(result["items"]) < len(candidates):
                                result.setdefault("diagnostics", []).append({"platform": platform, "stage": "research_relevance", "account_key": key,
                                    "ok": True, "outcome": "filtered", "scanned": len(candidates), "accepted": len(result["items"]),
                                    "message": "只保留原主题直接匹配或具有经核对原文片段的 AI 语义证据；其他候选不入库。"})
                            fresh = merge(result, node["depth"], node.get("parent_key"), "profile_timeline", None)
                            for field in delta:
                                delta[field] += fresh[field]
                            await persist("account", fresh)
                            blocked = next((row.get("outcome") for row in result.get("diagnostics", []) if row.get("outcome") in BLOCKING), None)
                            if blocked:
                                blocked_platforms.add(platform)
                        profile_data = accounts[key].get("profile") or {}
                        profile_hit = bool(profile_data.get("verified_in_dom") and (account_scope or query_matches(plan, profile_data.get("bio", "")) or profile_data.get("relevance_basis") == "ai_semantic"))
                        account_items = [row for row in items.values() if row.get("account_key") == key]
                        useful = bool(account_items or profile_hit)
                        expanded = 0
                        if useful and node["depth"] < options.depth and not node.get("expansion_initialized"):
                            work["mentions"] = "mentions" in options.expand_relations
                            work["neighbors"] = [direction for direction in options.expand_relations if direction != "mentions"]
                            node["expansion_initialized"] = True
                        if platform not in blocked_platforms and useful and node["depth"] < options.depth:
                            if work.get("mentions"):
                                for item in account_items:
                                    for mention in mention_handles(platform, item.get("content", "")):
                                        neighbor = account(platform, mention)
                                        if neighbor["account_key"] == key:
                                            continue
                                        if admit(neighbor, node["depth"] + 1, key, "post_mention", [item["url"]], 65):
                                            expanded += 1
                                            delta["accounts"].append(copy.deepcopy(accounts[neighbor["account_key"]]))
                                            edge = {"source_key": key, "target_key": neighbor["account_key"], "relation": "mentions", "evidence_url": item["url"], "evidence_id": item["evidence_id"], "observed_at": now()}
                                            fresh = merge({"relations": [edge]}, node["depth"] + 1, key, "post_mention")
                                            delta["relations"] += fresh["relations"]
                                        else:
                                            edge = {"source_key": key, "target_key": neighbor["account_key"], "relation": "mentions", "evidence_url": item["url"], "evidence_id": item["evidence_id"], "observed_at": now()}
                                            merge({"relations": [edge]}, node["depth"] + 1, key, "post_mention")
                                work["mentions"] = bool(work.get("posts"))
                            for direction in list(work.get("neighbors", [])):
                                if direction == "mentions" or len(relations) >= options.max_edges or platform in blocked_platforms:
                                    continue
                                count_key = "following_count" if direction == "following" else "followers_count"
                                branch_budget = min(research_branch_seconds(options.max_scrolls), max(1, remaining_seconds()))
                                branch_options = replace(options, time_budget_seconds=max(0.1, branch_budget - 3))
                                result = await adapter.neighbors(platform, handle, direction, branch_options, profile_data.get(count_key))
                                neighbor_failed = any(row.get("ok") is False for row in result.get("diagnostics", []))
                                account_incomplete = account_incomplete or neighbor_failed
                                for candidate in result.get("accounts", []):
                                    if candidate["account_key"] == key:
                                        continue
                                    candidate_hit = query_matches(plan, candidate.get("name", "")) or topical({"content": candidate.get("name", "")}).get("matched")
                                    if admit(candidate, node["depth"] + 1, key, direction, [entry["url"].rstrip("/") + "/" + direction + "/"], 55 if candidate_hit else 20):
                                        expanded += 1
                                        delta["accounts"].append(copy.deepcopy(accounts[candidate["account_key"]]))
                                fresh = merge(result, node["depth"] + 1, key, direction)
                                delta["relations"] += fresh["relations"]
                                if not neighbor_failed:
                                    work["neighbors"].remove(direction)
                                await persist("account", {"accounts": delta["accounts"], "relations": fresh["relations"]})
                                if any(row.get("outcome") in BLOCKING for row in result.get("diagnostics", [])):
                                    blocked_platforms.add(platform)
                        if platform in blocked_platforms:
                            node.update(status="blocked", outcome="platform_blocked")
                        elif account_incomplete:
                            node.update(status="failed", outcome="branch_incomplete", stop_reason="本次账号分支未完成读取；已读证据保留，供显式续查。")
                        elif any(work.get(field) for field in ("profile", "posts", "mentions", "neighbors")):
                            node.update(status="queued", outcome="budget_pending", stop_reason="尚有受预算限制的公开分支；已完成分支不重复读取。")
                        else:
                            visited.add(key)
                            node.update(status="completed" if useful else "skipped", outcome="evidence_found" if useful else "no_public_evidence" if account_scope else "no_topic_evidence")
                        decision("visit_account", "沿指定账号的可核对公开路径拓展；无可读原帖或已观察主页时停止分支。" if account_scope else "优先读取主题匹配、被多条证据发现的账号；无主题证据停止拓展。",
                                 {"account_key": key, "depth": node["depth"], "new_records": len(delta["items"]), "profile_topic_match": profile_hit,
                                  "topic_records": len(account_items), "expanded_candidates": expanded, "status": node["status"], "profile_error": profile_error})
                        retained_delta = drain_deferred()
                        for field in delta:
                            delta[field] += retained_delta[field]
                        await persist("account", delta)
                        visits_since_ai += 1
                        if visits_since_ai >= (1 if account_scope else 5):
                            await adapt_frontier()
                            visits_since_ai = 0
                        for extra in additional_queries:
                            for source in options.seed_sources:
                                await execute_query(adapter, source, extra, min(3, max(1, options.max_records - len(items))), adaptive=True)
                    failures = any(row.get("ok") is False for row in diagnostics[prior_diagnostics_count:])
                    limits = []
                    if len(items) >= options.max_records:
                        limits.append("record_budget")
                    if any(key not in accounts and row["depth"] <= options.depth for key, row in deferred.items()):
                        limits.append("account_budget")
                    summary["out_of_scope_candidates"] = sum(key not in accounts and row["depth"] > options.depth for key, row in deferred.items())
                    if len(relations) >= options.max_edges:
                        limits.append("edge_budget")
                    summary["limit_hits"] = limits
                    if blocked_platforms:
                        summary.update(outcome="partial" if items or accounts else "blocked", stop_reason="platform_blocked", stopped_reason="平台登录、限流或验证阻止进一步读取；已读分支保留，可在处理后显式续查。")
                    elif limits:
                        summary.update(outcome="partial", stop_reason=limits[0], stopped_reason="达到用户设置的预算：" + "、".join(limits) + "；已读结果保留。")
                    elif summary.get("frontier_recovery_required"):
                        summary.update(outcome="partial", stop_reason="missing_deferred_frontier", stopped_reason="旧任务保存了预算拒绝次数，但没有保存被拒候选的公开账号 URL；不能凭次数恢复账号或声称已探索完。")
                    elif summary.get("collection_recovery_required"):
                        summary.update(outcome="partial", stop_reason="missing_collection_work", stopped_reason="旧任务未保存材料或关系预算停止时的待执行分支，无法确认已读范围；不能声称已探索完。")
                    elif failures:
                        summary.update(outcome="partial" if items or accounts else "failed", stop_reason="branch_errors" if items or accounts else "no_readable_evidence",
                                       stopped_reason="部分公开页面没有完成读取；未把加载或解析失败标为成功。")
                    elif frontier() or any(row["depth"] <= options.depth for row in deferred.values()) or pending_seed_queries:
                        summary.update(outcome="partial", stop_reason="pending_collection_work", stopped_reason="仍有已保存的查询前缀、账号分支或观察证据待完成；未声称前沿已探索完。")
                    else:
                        summary.update(outcome="completed", stop_reason="frontier_exhausted" if items or accounts else "no_matches")
                    summary["blocked_platforms"] = sorted(blocked_platforms)
    except TimeoutError:
        summary.update(outcome="time_budget", stop_reason="time_budget", stopped_reason="采集已达到时间边界，已完成分支的证据保留，可显式续查。" + (" 已在原总预算内为最终分析预留时间。" if analysis_reserve else ""))
        decision("stop_research", "总时间预算已用尽", {"frontier_remaining": len(frontier())})
    except asyncio.CancelledError:
        summary.update(outcome="cancelled", stop_reason="cancelled", stopped_reason="用户取消；已提交分支保留，浏览器上下文关闭。")
        # Last fully completed branch was persisted before the next browser
        # operation. A start checkpoint also retains the interrupted frontier.
        raise
    except SocialError as exc:
        summary.update(outcome="blocked", stop_reason=exc.code, stopped_reason=exc.message)
        diagnostics.append({"platform": options.source, "ok": False, "outcome": exc.code, "message": exc.message, **exc.diagnostic})
    except Exception as exc:
        summary.update(outcome="partial_error", stop_reason="runtime_error", stopped_reason="研究分支发生运行错误；已完成证据仍保留。")
        diagnostics.append({"ok": False, "outcome": "runtime_error", "message": summary["stopped_reason"], **safe_error(exc, "research")})
    if not analysis_only:
        summary["collection_outcome"] = summary["outcome"]
        summary["collection_stop_reason"] = summary.get("stop_reason")
        summary["collection_stopped_reason"] = summary.get("stopped_reason")
        summary["collection_complete"] = summary["outcome"] == "completed"
    wants_ai = options.analysis_mode == "ai" or (options.analysis_mode == "auto" and bool(os.getenv("DEEPSEEK_API_KEY", "").strip()))
    previous_analysis = copy.deepcopy((resume or {}).get("analysis"))
    last_analysis = None

    def analysis_summary(value):
        ai = value.get("ai") or {}
        complete = ai.get("complete") is True
        pending = ai.get("pending_evidence_count", max(0, ai.get("available_evidence_count", len(value.get("evidence") or [])) - ai.get("input_evidence_count", 0)))
        summary.update(phase="analysis", outcome=summary["collection_outcome"], stop_reason=summary.get("collection_stop_reason"),
                       analysis_status=("completed" if complete else "partial" if ai.get("used") else "failed") if wants_ai else "rules",
                       analysis_pending_evidence=pending if wants_ai else 0,
                       analysis_reviewed_evidence=ai.get("input_evidence_count", 0),
                       analysis_available_evidence=ai.get("available_evidence_count", len(value.get("evidence") or [])),
                       analysis_completed_batches=ai.get("completed_batches", 0), analysis_stop_reason=ai.get("stop_reason"),
                       analysis_available_items=len(items), analysis_catalog_scope="all_saved_evidence")
        if summary.get("collection_stopped_reason"):
            summary["stopped_reason"] = summary["collection_stopped_reason"]
        else:
            summary.pop("stopped_reason", None)
        summary["ai_used"] = bool(summary.get("ai_used") or ai.get("used"))
        if wants_ai and not complete and summary["outcome"] == "completed":
            summary.update(outcome="partial", stop_reason="analysis_partial" if ai.get("used") else "analysis_failed",
                           stopped_reason="AI 分析尚未完成；成功批次与待审证据已保存，可继续分析。")

    async def analysis_checkpoint(value):
        nonlocal last_analysis
        last_analysis = copy.deepcopy(value)
        analysis_summary(last_analysis)
        await persist("analysis", analysis=compact_analysis_catalog(last_analysis))

    # Save the phase boundary before a model starts so a user cancellation can
    # continue analysis from the durable collection, even with no job.result.
    summary.update(phase="analysis", analysis_status="pending" if wants_ai else "rules",
                   analysis_pending_evidence=((previous_analysis or {}).get("ai") or {}).get("pending_evidence_count", len(items)))
    await persist("analysis")
    await emit(progress, stage="analysis", message=f"采集阶段已保存 {len(items)} 条材料、{len(accounts)} 个账号和 {len(relations)} 条关系；正在按证据目录完成分析。")
    # Keep final analysis within the original wall-clock budget as well. A
    # finished traversal never silently adds another minute to a 30 s task.
    remaining = remaining_seconds()
    use_analysis_ai = wants_ai and remaining > 0
    if use_analysis_ai:
        try:
            async with asyncio.timeout(remaining if math.isfinite(remaining) else None):
                analysis = await analyze_relationships_with_ai(list(accounts.values()), list(items.values()), list(relations.values()),
                    use_ai=True, max_findings=500, previous_analysis=previous_analysis,
                    checkpoint=analysis_checkpoint, time_budget_seconds=remaining)
        except TimeoutError:
            if last_analysis is not None:
                analysis = copy.deepcopy(last_analysis)
                if analysis["ai"].get("complete") is not True:
                    analysis["ai"].update(outcome="partial", stop_reason="time_budget", message="原总预算已结束，已完成批次保留，待审证据可继续分析。")
            else:
                analysis = await analyze_relationships_with_ai(list(accounts.values()), list(items.values()), list(relations.values()), use_ai=False)
                analysis["ai"] = {"requested": True, "used": False, "complete": False, "outcome": "time_budget", "stop_reason": "time_budget",
                                  "available_evidence_count": len(analysis.get("evidence") or []), "input_evidence_count": 0,
                                  "pending_evidence_count": len(analysis.get("evidence") or []), "message": "原总预算不足，保留证据规则结果与待审目录。"}
    else:
        analysis = await analyze_relationships_with_ai(list(accounts.values()), list(items.values()), list(relations.values()), use_ai=False)
        if wants_ai:
            analysis["ai"] = {"requested": True, "used": False, "complete": False, "outcome": "time_budget", "stop_reason": "time_budget",
                              "available_evidence_count": len(analysis.get("evidence") or []), "input_evidence_count": 0,
                              "pending_evidence_count": len(analysis.get("evidence") or []), "message": "原总预算不足，保留证据规则结果与待审目录。"}
    analysis_summary(analysis)
    if wants_ai and analysis["ai"].get("complete") is not True:
        diagnostic = {"source": "ai", "stage": "relationship_analysis", "ok": False,
                      "outcome": "analysis_partial" if analysis["ai"].get("used") else "analysis_failed",
                      "ai_outcome": analysis["ai"].get("outcome", "failed"),
                      "pending_evidence_count": summary["analysis_pending_evidence"],
                      "message": "AI 分析尚未完成；已保存采集成果、成功批次与证据规则分析，待审证据可继续分析。"}
        diagnostics.append(diagnostic)
    analysis.update(available_items=len(items), catalog_scope="all_saved_evidence")
    analysis = compact_analysis_catalog(analysis)
    storage = analysis["serialization"]
    omitted = storage["omitted_counts"]
    summary.update(analysis_catalog_entries=storage["stored_counts"]["evidence"],
                   analysis_catalog_omitted=omitted["evidence"], analysis_excerpt_chars=storage["excerpt_chars"])
    if any(omitted.values()):
        diagnostics.append({"source": "research", "stage": "analysis_storage", "ok": False, "outcome": "analysis_size_budget",
                            "omitted_counts": omitted, "stored_counts": storage["stored_counts"],
                            "message": "分析摘要达到存储预算，已优先保留关联判断和完整支持来源；所有取得材料仍保存在资料库，此摘要不代表全部分析条目。"})
        if summary["outcome"] == "completed":
            summary.update(outcome="partial", stop_reason="analysis_size_budget", stopped_reason="全部已取得材料已保存；分析摘要只保留预算内的可核对子集。")
    elif storage["excerpt_compacted_count"]:
        diagnostics.append({"source": "research", "stage": "analysis_storage", "ok": True, "outcome": "excerpt_compacted",
                            "excerpt_chars": storage["excerpt_chars"], "compacted_count": storage["excerpt_compacted_count"],
                            "message": "分析目录保存短摘要及完整来源链接；完整正文仍保存在资料库与研究导出中。"})
    summary["decisions_count"] = len(decisions)
    summary["phase"] = "finished"
    await persist("finished", analysis=analysis)
    await emit(progress, stage="finished", current=len(visited), total=options.max_accounts,
               message=f"已保留 {len(items)} 条证据、{len(accounts)} 个账号、{len(relations)} 条关系；研究结果：{summary['outcome']}。")
    return {"items": list(items.values()), "accounts": list(accounts.values()), "relations": list(relations.values()),
            "nodes": list(nodes.values()), "diagnostics": diagnostics, "decisions": decisions, "frontier": frontier(),
            "deferred_frontier": deferred_frontier(), "visited": sorted(visited),
            "query_plan": plan, "analysis": analysis, "summary": summary}
