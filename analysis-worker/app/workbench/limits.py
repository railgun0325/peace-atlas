"""Shared public budgets for API validation, execution and queued task plans.

These are ceilings for work, not promises about available source material.
Collectors can stop earlier for exhausted pages, duplicate content or access
constraints. Keep the ordinary collection contract separate from research.
"""
from __future__ import annotations

RESEARCH_RECORDS_MIN = 0
RESEARCH_RECORDS_MAX = 1000000
RESEARCH_RECORDS_DEFAULT = 0
RESEARCH_SCROLLS_MIN = 1
RESEARCH_SCROLLS_MAX = 5000
RESEARCH_SCROLLS_DEFAULT = 10
RESEARCH_TIME_MIN = 0
RESEARCH_TIME_MAX = 2592000
RESEARCH_TIME_DEFAULT = 0
RESEARCH_DEPTH_MAX = 8
RESEARCH_ACCOUNTS_MAX = 10000
RESEARCH_EDGES_MAX = 100000
RESEARCH_PER_ACCOUNT_MAX = 50000
RESEARCH_NEIGHBORS_MAX = 5000
# Used for arithmetic inside a finite date/depth/page scope; 0 stays visible
# in saved parameters as the absence of an optional total-count stop.
UNLIMITED_COUNT = 2147483647

COLLECT_RECORDS_MAX = 100
COLLECT_RECORDS_DEFAULT = 30
COLLECT_SCROLLS_MAX = 20
COLLECT_SCROLLS_DEFAULT = 8
BATCH_RECORDS_MAX = 1000000
BATCH_RECORDS_DEFAULT = 1000
BATCH_TASKS_MAX = 200


def record_ceiling(kind: str) -> int:
    if kind == "research":
        return RESEARCH_RECORDS_MAX
    if kind == "collect":
        return COLLECT_RECORDS_MAX
    raise ValueError("任务类型只能是 collect 或 research。")


def budget_limits() -> dict:
    """A fresh JSON object so clients can display the server's actual contract."""
    return {
        "research": {
            "depth": {"min": 1, "max": RESEARCH_DEPTH_MAX, "default": 2},
            "max_accounts": {"min": 0, "max": RESEARCH_ACCOUNTS_MAX, "default": 0, "optional": True},
            "max_edges": {"min": 0, "max": RESEARCH_EDGES_MAX, "default": 0, "optional": True},
            "max_records": {"min": RESEARCH_RECORDS_MIN, "max": RESEARCH_RECORDS_MAX, "default": RESEARCH_RECORDS_DEFAULT},
            "max_scrolls": {"min": RESEARCH_SCROLLS_MIN, "max": RESEARCH_SCROLLS_MAX, "default": RESEARCH_SCROLLS_DEFAULT},
            "time_budget_seconds": {"min": RESEARCH_TIME_MIN, "max": RESEARCH_TIME_MAX, "default": RESEARCH_TIME_DEFAULT},
            "max_records_per_account": {"min": 1, "max": RESEARCH_PER_ACCOUNT_MAX, "default": 1000},
            "max_neighbors_per_account": {"min": 1, "max": RESEARCH_NEIGHBORS_MAX, "default": 100},
        },
        "collect": {
            "max_records": {"min": 1, "max": COLLECT_RECORDS_MAX, "default": COLLECT_RECORDS_DEFAULT},
            "max_scrolls": {"min": 1, "max": COLLECT_SCROLLS_MAX, "default": COLLECT_SCROLLS_DEFAULT},
        },
        "batch": {
            "total_records": {"min": 1, "max": BATCH_RECORDS_MAX, "default": BATCH_RECORDS_DEFAULT},
            "max_tasks": BATCH_TASKS_MAX,
        },
    }


def research_branch_seconds(scrolls: int) -> int:
    """Bound one query without defeating a larger user-selected scroll budget.

    The outer research timeout remains authoritative. The startup allowance
    and per-scroll allowance are upper bounds; an exhausted collector returns
    immediately rather than waiting out this time.
    """
    return max(70, 30 + 6 * scrolls)
