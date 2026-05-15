from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from .db import get_conn

RESEARCH_TASKS_TABLE = "japan_research_tasks"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bridge_url() -> str | None:
    """Return the configured Windows host bridge URL, or None if absent."""
    host_ip = os.getenv("WINDOWS_HOST_IP", "").strip()
    port = os.getenv("COOKIE_SERVICE_PORT", "8096").strip()
    if not host_ip:
        return None
    return f"http://{host_ip}:{port}"


def _bridge_available() -> bool:
    """Check synchronously whether the Windows host bridge URL is configured.

    Does NOT attempt a network connection — this is a configuration-only check.
    Actual connectivity is tested by the caller if needed.
    """
    return bool(os.getenv("WINDOWS_HOST_IP", "").strip())


def create_research_task(
    task_type: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    priority: int = 0,
    source: str = "japan_analysis",
) -> dict[str, Any]:
    """Create an async research task.

    If no bridge is configured, creates the task with status='pending'
    and a diagnostic noting the missing bridge. Does NOT crash.
    """
    now = _now()
    bridge = _env_bridge_url()

    status = "pending"
    diagnostic = {
        "message": "Task created. Awaiting bridge to execute.",
        "bridge_url": bridge,
        "created_at": now,
    }

    if not _bridge_available():
        status = "pending"
        diagnostic = {
            "level": "warning",
            "message": (
                "WINDOWS_HOST_IP is not configured. Async research tasks will "
                "remain pending until a bridge is available. Set WINDOWS_HOST_IP "
                "environment variable to the Windows host IP address."
            ),
            "created_at": now,
        }

    payload_json = json.dumps(payload or params or {}, ensure_ascii=False, default=str)
    if diagnostic.get("level"):
        payload_with_diagnostic = payload or params or {}
        payload_with_diagnostic = {**payload_with_diagnostic, "_diagnostic": diagnostic, "_priority": priority, "_source": source}
        payload_json = json.dumps(payload_with_diagnostic, ensure_ascii=False, default=str)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {RESEARCH_TASKS_TABLE}
                  (task_type, params, status, error_message, created_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, task_type, status, params, created_at
                """,
                (task_type, payload_json, status, diagnostic.get("message"), now),
            )
            row = dict(cur.fetchone())

    return {
        "ok": True,
        "task": row,
        "diagnostic": diagnostic,
    }


def get_research_task(task_id: int) -> dict[str, Any] | None:
    """Retrieve a research task by ID."""
    from .db import fetch_all

    rows = fetch_all(
        f"SELECT * FROM {RESEARCH_TASKS_TABLE} WHERE id = %s", (task_id,)
    )
    return rows[0] if rows else None


def list_research_tasks(
    *,
    status: str | None = None,
    task_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List research tasks with optional filters."""
    from .db import fetch_all

    clauses = ["1=1"]
    params: list[Any] = []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if task_type:
        clauses.append("task_type = %s")
        params.append(task_type)
    where = " AND ".join(clauses)
    return fetch_all(
        f"SELECT * FROM {RESEARCH_TASKS_TABLE} WHERE {where} ORDER BY created_at DESC LIMIT %s",
        tuple(params) + (limit,),
    )


def mark_task_failed(task_id: int, error_message: str) -> dict[str, Any]:
    """Mark a research task as failed with a diagnostic message.

    This is the safe path — when a bridge is unavailable or a task cannot
    complete, we record the failure rather than crashing.
    """
    now = _now()
    diagnostic = {
        "level": "error",
        "message": error_message,
        "failed_at": now,
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {RESEARCH_TASKS_TABLE}
                SET status = 'failed', error_message = %s, completed_at = %s
                WHERE id = %s
                RETURNING id, task_type, status, completed_at
                """,
                (json.dumps(diagnostic, ensure_ascii=False), now, task_id),
            )
            row = cur.fetchone()
    if row:
        return {"ok": True, "task": dict(row), "diagnostic": diagnostic}
    return {"ok": False, "error": "task not found"}


def mark_task_completed(task_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """Mark a research task as completed with results."""
    now = _now()
    result_json = json.dumps(result, ensure_ascii=False, default=str)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {RESEARCH_TASKS_TABLE}
                SET status = 'completed', result = %s, completed_at = %s
                WHERE id = %s
                RETURNING id, task_type, status, completed_at
                """,
                (result_json, now, task_id),
            )
            row = cur.fetchone()
    if row:
        return {"ok": True, "task": dict(row)}
    return {"ok": False, "error": "task not found"}


def pending_tasks_diagnostic() -> dict[str, Any]:
    """Return a diagnostic summary of pending/failed tasks.

    Safe to call at any time — never crashes even if the table doesn't exist.
    """
    try:
        from .db import fetch_all

        pending = fetch_all(
            f"SELECT COUNT(*) as cnt FROM {RESEARCH_TASKS_TABLE} WHERE status = 'pending'"
        )
        failed = fetch_all(
            f"SELECT COUNT(*) as cnt FROM {RESEARCH_TASKS_TABLE} WHERE status = 'failed'"
        )
        recent_failed = fetch_all(
            f"SELECT id, task_type, error_message, created_at FROM {RESEARCH_TASKS_TABLE} WHERE status = 'failed' ORDER BY completed_at DESC NULLS LAST LIMIT 5"
        )
        return {
            "ok": True,
            "bridge_configured": _bridge_available(),
            "bridge_url": _env_bridge_url(),
            "pending_count": int(pending[0]["cnt"]) if pending else 0,
            "failed_count": int(failed[0]["cnt"]) if failed else 0,
            "recent_failures": recent_failed,
        }
    except Exception as exc:
        return {
            "ok": True,
            "bridge_configured": _bridge_available(),
            "bridge_url": _env_bridge_url(),
            "pending_count": 0,
            "failed_count": 0,
            "diagnostic": f"Could not query tasks table: {exc}",
        }
