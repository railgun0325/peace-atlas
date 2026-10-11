"""Explicit read-only conversion of the user's previous collectors SQLite file."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from . import pipeline
from .store import Store


def _list(value):
    try:
        decoded = json.loads(value or "[]")
        return decoded if isinstance(decoded, list) else []
    except (ValueError, TypeError):
        return []


def _legacy_utc(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def read_collectors(path: str | Path) -> dict:
    path = Path(path).resolve(strict=True)
    accounts, items, relations = [], [], []
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("旧采集数据库未通过完整性检查。")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"social_accounts", "social_posts", "social_relationships"}.issubset(tables):
            raise ValueError("所选文件不是受支持的旧 collectors 数据库。")
        key_map = {}
        for raw in connection.execute("SELECT * FROM social_accounts ORDER BY id"):
            row = dict(raw)
            key = Store.social_account_key(row["platform"], row["handle"])
            key_map[row["id"]] = key
            accounts.append({"account_key": key, "name": row["account_name"] or row["handle"],
                "handle": row["handle"], "platform": row["platform"], "url": row["url"],
                "organization": row["organization"], "tags": _list(row["tags"]),
                "metadata": {"import_source": "legacy_collectors", "legacy_id": row["id"],
                    "bio": row.get("bio", ""), "follower_count": row.get("follower_count"),
                    "legacy_note": row.get("note", ""), "legacy_priority": row.get("priority"),
                    "legacy_discovery_method": row.get("discovery_method"),
                    "legacy_status": row.get("status"), "original_created_at": row.get("created_at")}})
        for raw in connection.execute("SELECT * FROM social_posts ORDER BY id"):
            row = dict(raw)
            key = key_map.get(row["account_id"])
            name = key.split(":", 1)[-1] if key else "作者待确认"
            content = row["content_text"] or ""
            item = {"title": content[:120] or f"{row['platform']} 历史帖子",
                "content": content, "url": row["url"], "type": "social", "platform": row["platform"],
                "source_name": f"{'X' if row['platform'] == 'twitter' else 'Instagram'} / {name}",
                "published_at": row["published_at"], "account_key": key,
                "metadata": {"import_source": "legacy_collectors", "legacy_id": row["id"],
                    "content_kind": "body" if row["platform"] == "twitter" else "legacy_unverified",
                    "collection_method": "legacy_browser_capture", "collected_at": _legacy_utc(row["fetched_at"]),
                    "date_scope": "known" if row["published_at"] else "unknown",
                    "engagement": {"likes": None, "comments": None, "reposts": None},
                    "legacy_engagement": {"likes": row["like_count"], "comments": row["comment_count"], "reposts": row["repost_count"]},
                    "engagement_verified": False, "legacy_analysis_used": False}}
            items.append(pipeline.enrich_item(item))
        for raw in connection.execute("SELECT * FROM social_relationships ORDER BY id"):
            row = dict(raw)
            # The old SQLite schema used datetime('now'), which is UTC.
            stamp = _legacy_utc(row["discovered_at"])
            relations.append({"source_key": key_map.get(row["from_account_id"]),
                "target_key": key_map.get(row["to_account_id"]), "relation": row["rel_type"],
                "evidence_url": "", "observed_at": stamp})
    return {"items": items, "accounts": accounts, "relations": relations}


def migrate_collectors(source: str | Path, destination: Store) -> dict:
    batch = read_collectors(source)
    result = destination.import_social(**batch)
    return {**result, "source_posts": len(batch["items"]), "source_accounts": len(batch["accounts"]),
            "source_relations": len(batch["relations"]),
            "unknown_dates": sum(item["published_at"] is None for item in batch["items"]),
            "unlinked_posts": sum(not item.get("account_key") for item in batch["items"])}
