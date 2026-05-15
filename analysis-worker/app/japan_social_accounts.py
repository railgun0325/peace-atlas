from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .account_discovery import DEFAULT_PUBLIC_DISCOVERY_KEYWORDS, discover_public_candidates
from .db import fetch_all, fetch_one, get_conn

BASE_DIR = Path(__file__).resolve().parents[1]
SEED_CONFIG = BASE_DIR / "config" / "japan_social_accounts_seed.yml"

ALLOWED_PLATFORMS = {"twitter", "youtube", "reddit", "instagram", "mastodon", "rsshub", "website", "tiktok"}
ALLOWED_CATEGORIES = {"party", "citizen_group", "ngo", "media", "independent_media", "forum", "government", "academic", "other"}
ALLOWED_STATUSES = {"seed", "verified", "candidate", "rejected", "stale"}

DEFAULT_DISCOVERY_KEYWORDS = [
    "反戦",
    "戦争反対",
    "平和運動",
    "平和",
    "平和主義",
    "平和外交",
    "平和教育",
    "平和集会",
    "憲法九条",
    "憲法9条",
    "九条の会",
    "護憲",
    "改憲反対",
    "安保法制反対",
    "安全保障",
    "防衛",
    "防衛費",
    "沖縄基地",
    "沖縄",
    "辺野古",
    "普天間",
    "米軍基地",
    "自衛隊海外派遣反対",
    "軍拡反対",
    "武器輸出反対",
    "核兵器廃絶",
    "原水禁",
    "原水協",
    "被爆者",
    "核廃絶",
    "核軍縮",
    "核兵器禁止条約",
    "人権",
    "アムネスティ",
    "Human Rights",
    "広島 平和",
    "長崎 平和",
    "市民連合",
    "市民団体",
    "反基地",
    "日米安保",
    "地位協定",
    "Peace Boat",
    "Article 9",
]
DEFAULT_DISCOVERY_KEYWORDS = list(dict.fromkeys([
    *DEFAULT_DISCOVERY_KEYWORDS,
    *DEFAULT_PUBLIC_DISCOVERY_KEYWORDS,
    "日本 反戦 団体 Instagram",
    "日本 平和運動 Instagram",
    "憲法9条 団体 Instagram",
    "沖縄 基地反対 Instagram",
    "Peace Boat Instagram",
    "九条の会 Instagram",
    "反戦デモ Instagram",
    "平和団体 インスタ",
    "護憲 団体 インスタ",
    "Japan antiwar Instagram",
    "Japan peace movement Instagram",
    "Okinawa base protest Instagram",
]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _with_aliases(row: dict[str, Any]) -> dict[str, Any]:
    """Return a row with both canonical and frontend compatibility names."""
    result = dict(row)
    status = str(result.get("status") or "").lower()
    verified = status == "verified" or bool(result.get("verified"))
    is_candidate = status == "candidate" or bool(result.get("is_candidate"))
    result.setdefault("username", result.get("handle") or "")
    result.setdefault("display_name", result.get("account_name") or result.get("handle") or "")
    result.setdefault("account_url", result.get("url") or "")
    result.setdefault("bio", result.get("note") or "")
    result.setdefault("tags", [])
    follower_count = result.get("follower_count")
    if follower_count in (None, "", 0, "0"):
        result.pop("follower_count", None)
    result["verified"] = verified
    result["is_candidate"] = is_candidate
    result.setdefault("verification_evidence_url", result.get("evidence_url") or "")
    result.setdefault("added_by", result.get("discovery_method") or "")
    return result


def load_seed_accounts(path: Path | None = None) -> list[dict[str, Any]]:
    config_path = path or SEED_CONFIG
    if not config_path.exists():
        return []
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("accounts", [])


def seed_accounts(conn=None) -> dict[str, Any]:
    """Upsert seed accounts from YAML into japan_social_accounts table.

    Only 'seed' or 'verified' status entries are imported.
    Returns counts of inserted/updated/skipped.
    """
    seed = load_seed_accounts()
    inserted = 0
    updated = 0
    skipped = 0
    own_conn = conn is None
    if own_conn:
        conn_ctx = get_conn()
        conn = conn_ctx.__enter__()
    try:
        with conn.cursor() as cur:
            for account in seed:
                platform = str(account.get("platform", "")).strip().lower()
                handle = str(account.get("handle", "")).strip()
                status = str(account.get("status", "seed")).strip().lower()
                if platform not in ALLOWED_PLATFORMS or not handle:
                    skipped += 1
                    continue
                if status not in ("seed", "verified"):
                    skipped += 1
                    continue
                cur.execute(
                    "SELECT id FROM japan_social_accounts WHERE platform = %s AND handle = %s",
                    (platform, handle),
                )
                existing = cur.fetchone()
                account_name = str(account.get("account_name", "")).strip()
                organization = account.get("organization") or None
                category = str(account.get("category", "other")).strip().lower()
                url = str(account.get("url", "")).strip()
                language = str(account.get("language", "ja")).strip()
                note = str(account.get("note", "")).strip()
                if existing:
                    cur.execute(
                        """
                        UPDATE japan_social_accounts
                        SET account_name = %s, organization = %s, category = %s,
                            url = %s, language = %s, note = %s, status = %s, updated_at = %s
                        WHERE id = %s
                        """,
                        (account_name, organization, category, url, language, note, status, _now(), existing["id"]),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """
                    INSERT INTO japan_social_accounts
                      (platform, handle, account_name, organization, category,
                       url, language, note, status, verified, is_candidate,
                       discovered_at, updated_at, discovery_method,
                       username, display_name, account_url, added_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'seed',
                            %s, %s, %s, 'seed')
                    """,
                        (
                            platform, handle, account_name, organization, category, url, language,
                            note, status, status == "verified", status != "verified", _now(), _now(),
                            handle, account_name, url,
                        ),
                    )
                    inserted += 1
            if own_conn:
                conn.commit()
    finally:
        if own_conn:
            conn_ctx.__exit__(None, None, None)
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def upsert_account(
    platform: str,
    handle: str,
    *,
    account_name: str = "",
    organization: str | None = None,
    category: str = "other",
    url: str = "",
    language: str = "ja",
    note: str = "",
    status: str = "seed",
    discovery_method: str = "manual",
    evidence_url: str = "",
) -> dict[str, Any]:
    """Insert or update a single social account. Returns the account dict."""
    platform = platform.strip().lower()
    handle = handle.strip()
    if platform not in ALLOWED_PLATFORMS or not handle:
        return {"ok": False, "error": "invalid platform or empty handle"}
    if category not in ALLOWED_CATEGORIES:
        category = "other"
    if status not in ALLOWED_STATUSES:
        status = "candidate"
    now = _now()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, status, verified, is_candidate, evidence_url
                FROM japan_social_accounts
                WHERE platform = %s AND handle = %s
                """,
                (platform, handle),
            )
            existing = cur.fetchone()
            if existing:
                existing_status = str(existing.get("status") or "").lower()
                stored_status = status
                if existing_status == "verified" and status == "candidate":
                    stored_status = "verified"
                existing_verified = bool(existing.get("verified"))
                stored_verified = stored_status == "verified" or existing_verified
                stored_candidate = stored_status == "candidate" and not stored_verified
                stored_evidence_url = evidence_url or str(existing.get("evidence_url") or "")
                cur.execute(
                    """
                    UPDATE japan_social_accounts
                    SET platform = %s, handle = %s,
                        account_name = %s, organization = %s, category = %s,
                        url = %s, language = %s,
                        status = %s, updated_at = %s, evidence_url = %s, note = %s,
                        username = %s, display_name = %s, account_url = %s,
                        verified = %s, is_candidate = %s
                    WHERE id = %s
                    RETURNING id, platform, handle, account_name, organization, category,
                              url, language, status, discovered_at, updated_at, discovery_method, evidence_url
                    """,
                    (
                        platform, handle, account_name, organization, category, url, language,
                        stored_status, now, stored_evidence_url, note, handle, account_name, url,
                        stored_verified, stored_candidate, existing["id"],
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO japan_social_accounts
                      (platform, handle, account_name, organization, category, url, language,
                       status, discovered_at, updated_at, discovery_method, evidence_url, note,
                       username, display_name, account_url, verified, is_candidate, added_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, platform, handle, account_name, organization, category,
                              url, language, status, discovered_at, updated_at, discovery_method, evidence_url
                    """,
                    (
                        platform, handle, account_name, organization, category, url, language,
                        status, now, now, discovery_method, evidence_url, note, handle, account_name, url,
                        status == "verified", status == "candidate", discovery_method,
                    ),
                )
            row = dict(cur.fetchone())
    return {"ok": True, "account": _with_aliases(row)}


def discover_candidate(
    platform: str,
    handle: str,
    *,
    account_name: str = "",
    organization: str | None = None,
    category: str = "other",
    url: str = "",
    language: str = "ja",
    evidence_url: str = "",
    discovery_method: str = "auto",
    note: str = "",
) -> dict[str, Any]:
    """Register a candidate account discovered automatically.

    Always sets status='candidate'. Never promotes to 'verified'.
    evidence_url is required — must reference the public page where the
    account was discovered.
    """
    return upsert_account(
        platform=platform,
        handle=handle,
        account_name=account_name,
        organization=organization,
        category=category,
        url=url,
        language=language,
        note=note,
        status="candidate",
        discovery_method=discovery_method,
        evidence_url=evidence_url,
    )


def _save_account_source(account_id: int, source_type: str, source_detail: str) -> None:
    source_type = (source_type or "manual").strip() or "manual"
    source_detail = (source_detail or "").strip()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM japan_social_account_sources
                WHERE account_id = %s AND source_type = %s AND COALESCE(source_detail, '') = %s
                LIMIT 1
                """,
                (account_id, source_type, source_detail),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO japan_social_account_sources (account_id, source_type, source_detail, added_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (account_id, source_type, source_detail or None, _now()),
                )


def verify_account(account_id: int, verified: bool = True, evidence_url: str | None = None) -> dict[str, Any]:
    """Manually promote an account from candidate/seed to verified."""
    status = "verified" if verified else "rejected"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM japan_social_accounts WHERE id = %s", (account_id,))
            current = cur.fetchone() or {}
            if not current:
                return {"ok": False, "error": "account not found"}
            resolved_evidence = (evidence_url or current.get("evidence_url") or current.get("url") or "").strip()
            cur.execute(
                """
                UPDATE japan_social_accounts
                SET platform = %s,
                    handle = %s,
                    account_name = %s,
                    organization = %s,
                    category = %s,
                    url = %s,
                    language = %s,
                    status = %s,
                    verified = %s,
                    is_candidate = FALSE,
                    evidence_url = COALESCE(NULLIF(%s, ''), evidence_url),
                    verification_evidence_url = COALESCE(NULLIF(%s, ''), verification_evidence_url),
                    verification_date = CASE WHEN %s THEN %s ELSE verification_date END,
                    updated_at = %s
                WHERE id = %s
                RETURNING *
                """,
                (
                    current.get("platform", ""),
                    current.get("handle", ""),
                    current.get("account_name", ""),
                    current.get("organization"),
                    current.get("category", "other"),
                    current.get("url", ""),
                    current.get("language", "ja"),
                    status,
                    verified,
                    resolved_evidence,
                    resolved_evidence,
                    verified,
                    _now(),
                    _now(),
                    account_id,
                ),
            )
            row = cur.fetchone()
    if row:
        return {"ok": True, "account_id": account_id, "status": row["status"], "account": _with_aliases(dict(row))}
    return {"ok": False, "error": "account not found"}


def list_accounts(
    *,
    platform: str | None = None,
    category: str | None = None,
    status: str | None = None,
    organization: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = ["1=1"]
    params: list[Any] = []

    if platform:
        clauses.append("platform = %s")
        params.append(platform.strip().lower())
    if category:
        clauses.append("category = %s")
        params.append(category.strip().lower())
    if status:
        clauses.append("status = %s")
        params.append(status.strip().lower())
    if organization:
        clauses.append("organization ILIKE %s")
        params.append(f"%{organization.strip()}%")
    if search:
        clauses.append("(account_name ILIKE %s OR handle ILIKE %s OR note ILIKE %s)")
        pattern = f"%{search.strip()}%"
        params.extend([pattern, pattern, pattern])

    where = " AND ".join(clauses)
    return [
        _with_aliases(row)
        for row in fetch_all(
        f"""
        SELECT *
        FROM japan_social_accounts
        WHERE {where}
        ORDER BY status = 'verified' DESC, category, account_name
        LIMIT %s OFFSET %s
        """,
        tuple(params) + (limit, offset),
        )
    ]


def account_summary() -> dict[str, Any]:
    """Build a summary of accounts for the weekly report.

    Returns:
      - by_organization: dict org_name → list of up to 3 verified accounts
      - top_active: top 10 accounts by most recent update
      - candidate_samples: up to 3 candidate accounts with evidence_url
    """
    all_accounts = fetch_all(
        "SELECT * FROM japan_social_accounts ORDER BY updated_at DESC NULLS LAST"
    )
    verified = [a for a in all_accounts if a.get("status") == "verified"]
    candidates = [a for a in all_accounts if a.get("status") == "candidate"]

    by_org: dict[str, list[dict[str, Any]]] = {}
    for account in verified:
        org = account.get("organization") or "その他/未分類"
        by_org.setdefault(org, []).append(account)

    org_summary: dict[str, list[dict[str, Any]]] = {}
    for org, accounts in sorted(by_org.items()):
        org_summary[org] = accounts[:3]

    top_active = verified[:10]
    if len(top_active) < 10:
        top_active = (verified + all_accounts)[:10]

    candidate_samples = []
    for candidate in candidates:
        if candidate.get("evidence_url"):
            candidate_samples.append(candidate)
        if len(candidate_samples) >= 3:
            break

    return {
        "total_accounts": len(all_accounts),
        "verified_count": len(verified),
        "candidate_count": len(candidates),
        "by_organization": org_summary,
        "top_active": top_active,
        "candidate_samples": candidate_samples,
    }


async def discover_japan_social_accounts(
    platform: str | None = None,
    keywords: list[str] | None = None,
    category: str | None = None,
    max_accounts: int = 20,
) -> dict[str, Any]:
    """Discover candidate accounts from seed list plus public web sources."""
    discovered: list[dict[str, Any]] = []
    inserted_candidates = 0
    updated = 0
    diagnostics: list[dict[str, Any]] = []
    effective_keywords = [item for item in (keywords or []) if item] or DEFAULT_DISCOVERY_KEYWORDS
    needles = [item.lower() for item in effective_keywords if item]

    for account in load_seed_accounts():
        if platform and str(account.get("platform", "")).lower() != platform.lower():
            continue
        if category and str(account.get("category", "")).lower() != category.lower():
            continue
        text = " ".join(str(account.get(key, "")) for key in ["account_name", "organization", "note", "handle"]).lower()
        if needles and not any(needle in text for needle in needles):
            continue
        evidence_url = str(account.get("evidence_url") or account.get("url") or "").strip()
        if not evidence_url:
            diagnostics.append({"ok": False, "message": "skipped account without evidence_url", "handle": account.get("handle")})
            continue
        try:
            before = list_accounts(platform=account.get("platform"), search=account.get("handle"), limit=1)
            result = discover_candidate(
                platform=str(account.get("platform")),
                handle=str(account.get("handle")),
                account_name=str(account.get("account_name") or ""),
                organization=account.get("organization"),
                category=str(account.get("category") or "other"),
                url=str(account.get("url") or evidence_url),
                language=str(account.get("language") or "ja"),
                evidence_url=evidence_url,
                discovery_method="seed_discovery",
                note=str(account.get("note") or ""),
            )
        except Exception as exc:
            return {
                "ok": False,
                "discovered": len(discovered),
                "inserted_candidates": inserted_candidates,
                "updated": updated,
                "accounts": discovered,
                "diagnostics": [
                    *diagnostics,
                    {"ok": False, "source": "postgres", "message": str(exc)},
                ],
                "keywords_used": effective_keywords,
            }
        if result.get("ok"):
            discovered.append(result["account"])
            _save_account_source(result["account"]["id"], "seed_discovery", evidence_url)
            if before:
                updated += 1
            else:
                inserted_candidates += 1
        if len(discovered) >= max_accounts:
            break

    remaining_slots = max(0, max_accounts - len(discovered))
    public_accounts: list[dict[str, Any]] = []
    if remaining_slots > 0:
        public_candidates, public_diag = await discover_public_candidates(
            effective_keywords,
            max_requests=max(4, min(20, len(effective_keywords) * 2)),
        )
        diagnostics.extend(public_diag)
        for item in public_candidates:
            if len(public_accounts) >= remaining_slots:
                break
            before = list_accounts(platform=item.platform, search=item.handle, limit=1)
            if before:
                diagnostics.append(
                    {
                        "ok": True,
                        "stage": "dedupe",
                        "platform": item.platform,
                        "handle": item.handle,
                        "reason": "existing_account_will_be_updated",
                    }
                )
            result = discover_candidate(
                platform=item.platform,
                handle=item.handle,
                account_name=item.handle.lstrip("@"),
                category=item.category,
                url=item.profile_url,
                language="ja",
                evidence_url=item.evidence_url,
                discovery_method="public_discovery",
                note=f"verification_status={item.verification_status}",
            )
            if not result.get("ok"):
                diagnostics.append(
                    {
                        "ok": False,
                        "stage": "upsert",
                        "platform": item.platform,
                        "handle": item.handle,
                        "reason": result.get("error", "unknown"),
                    }
                )
                continue
            _save_account_source(result["account"]["id"], item.source_type, f"{item.source_detail} | {item.evidence_url}")
            public_accounts.append(result["account"])
            if before:
                updated += 1
            else:
                inserted_candidates += 1

    all_discovered = discovered + public_accounts

    return {
        "ok": True,
        "discovered": len(all_discovered),
        "inserted_candidates": inserted_candidates,
        "updated": updated,
        "accounts": all_discovered,
        "diagnostics": diagnostics,
        "keywords_used": effective_keywords,
        "source_write_contract": {
            "table": "japan_social_account_sources",
            "fields": ["account_id", "source_type", "source_detail", "added_at"],
            "dedupe_key": "account_id + source_type + source_detail",
        },
    }
