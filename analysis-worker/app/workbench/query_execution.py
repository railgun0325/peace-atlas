"""Explicit news keyword groups share an overall result budget and deduplicate."""
from . import pipeline
from .query_plan import build_query_plan, query_matches


async def collect_queries(params, progress=None, accept=None):
    if params.get("source") in {"twitter", "instagram"}:
        from .social.collector import collect_queries as social_queries
        return await social_queries(params, progress=progress, accept=accept)
    plan = build_query_plan(params)
    items, diagnostics, seen = [], [], set()
    queries = plan["queries"] if params.get("source") != "rss" else [{"keyword": ""}]
    for query in queries:
        remaining = params.get("max_records", 30) - len(items)
        if remaining <= 0:
            break
        result = await pipeline.collect({**params, "keyword": query["keyword"], "max_records": remaining})
        diagnostics.extend([{**row, "keyword_group": query["keyword"]} for row in result.get("diagnostics", [])])
        for item in result.get("items", []):
            if item["url"] in seen or not query_matches(plan, f"{item.get('title', '')}\n{item.get('content', '')}"):
                continue
            seen.add(item["url"]); items.append(item)
    return {"items": items, "accounts": [], "relations": [], "diagnostics": diagnostics, "query_plan": plan}
