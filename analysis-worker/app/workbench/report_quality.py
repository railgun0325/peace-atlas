"""Deterministic public-source inventory and limited reconciliation advisories.

Literal text mentions are not affiliations, reciprocal relations, identity
verification or semantic proof. No model/network/graph mutation occurs here.
"""
from __future__ import annotations

import copy
from collections import Counter
import re

from .social.common import SocialError, normalize_handle


_PROFILE_ROLES = {"public_profile", "visible_profile", "profile"}
_HANDLE = re.compile(r"(?<![A-Za-z0-9_@])@([A-Za-z0-9_]{1,15})(?![A-Za-z0-9_])")
_TWITTER_KEY = re.compile(r"twitter:([A-Za-z0-9_]{1,15})", re.I)
_NEGATIVE_MENTION = re.compile(
    r"(?:没有|并未|未曾|未见|未发现|未找到|未|无)[^。！？\n]{0,90}(?:提及|相互点名|互相点名)"
    r"|\b(?:no|not|never|neither)[^.!?\n]{0,70}\bmentions?\b"
    r"|言及(?:がない|していない|はない)", re.I)
_ADVISORY_LIMIT = 64


def _twitter_key(value):
    match = _TWITTER_KEY.fullmatch(str(value or ""))
    if not match:
        return None
    try:
        return "twitter:" + normalize_handle("twitter", match[1])
    except SocialError:
        return None


_HANDLE_NORMALIZATION = {
    "version": 1, "platform": "twitter", "canonical_key_prefix": "twitter:",
    "method": "social.common.normalize_handle", "ascii_handle_case_insensitive": True,
    "username_equivalence_only": True, "cross_platform_equivalence": False,
    "different_spelling_equivalence": False,
    "rules": ["X 用户名只差 ASCII 字母大小写或前导 @ 时为同一规范句柄，原样字面量仍保留。",
        "原样句柄和 canonical_handle_key 的相等仅是用户名等价，不证明组织运营身份、互认、隶属、背书或核验。",
        "不同拼写及跨平台句柄不能合并；未登记目标保留未绑定，旧号迁移自述仍须独立核验。"],
}


def evidence_note_inventory(inventory):
    """Keep protocol-1 evidence identity for the version-2 presentation hints.

    Compression requests never read the added canonical presentation fields.
    Original text, actual target binding, offsets and collection scope remain
    in the strict cache key. Unknown inventory contracts are not migrated.
    """
    result = copy.deepcopy(inventory)
    if (isinstance(result, dict) and result.get("version") == 2
            and result.get("handle_normalization") == _HANDLE_NORMALIZATION):
        result["version"] = 1
        result.pop("handle_normalization")
        for mention in result.get("profile_handle_mentions") or []:
            if isinstance(mention, dict):
                mention.pop("canonical_handle_key", None)
                mention.pop("username_equivalence_only", None)
    return result


def _source_key(item):
    metadata = item.get("metadata") or {}
    return (item.get("source_key") or metadata.get("source_key") or item.get("account_key")
            or metadata.get("account_key") or
            (item.get("source_name") if _twitter_key(item.get("source_name")) else None))


def _profile_role(item):
    metadata = item.get("metadata") or {}
    return any(item.get(field) in _PROFILE_ROLES or metadata.get(field) in _PROFILE_ROLES
               for field in ("research_report_source_role", "evidence_kind", "kind", "type"))


def _profile_text(item):
    """Keep every saved character; disconnected excerpts remain separate."""
    content = item.get("content")
    if not isinstance(content, str):
        content = (item.get("profile") or {}).get("bio")
    if isinstance(content, str):
        return content, [{"start": 0, "end": len(content), "text": content}], True, len(content)
    segments = []
    for part in item.get("content_fragments") or []:
        start, end, text = part.get("start"), part.get("end"), part.get("text")
        if not isinstance(start, int) or not isinstance(end, int) or not isinstance(text, str) or start < 0 or end-start != len(text):
            raise ValueError("保存简介片段范围与文本不一致。")
        segments.append({"start": start, "end": end, "text": text})
    segments.sort(key=lambda part: part["start"])
    cursor, continuous = 0, True
    for part in segments:
        continuous = continuous and part["start"] == cursor
        cursor = part["end"]
    total = item.get("content_characters", cursor)
    complete = bool(segments) and continuous and cursor == total and item.get("complete_text_sent", True)
    text = "".join(part["text"] for part in segments) if complete else None
    return text, segments, complete, total


def _context_accounts(context):
    accounts = context.get("accounts") or []
    return list(accounts.values()) if isinstance(accounts, dict) else list(accounts)


def _saved_material_distribution(items):
    """Count explicit saved-material roles by recorded publisher, not identity."""
    groups, total, unknown = {}, 0, 0
    for index, item in enumerate(items):
        metadata = item.get("metadata") or {}
        role = metadata.get("research_report_source_role") or item.get("research_report_source_role")
        if role != "saved_public_material":
            continue
        total += 1
        publisher, basis = None, "unknown_per_source"
        for field in ("account_key", "source_key", "source_name"):
            value = item.get(field) or metadata.get(field)
            if isinstance(value, str) and value.strip():
                publisher, basis = (_twitter_key(value) or value), field
                break
        if publisher is None:
            unknown += 1
            publisher = "unknown_source:" + str(item.get("citation_id") or index)
        # A display label is not silently equated with a typed account key.
        namespace = "key" if basis in {"account_key", "source_key"} else basis
        identity = (namespace, publisher) if basis != "unknown_per_source" else (namespace, index)
        if identity not in groups:
            groups[identity] = {"publisher_key": publisher, "grouping_basis": basis,
                "grouping_bases": [], "count": 0, "citation_ids": [], "source_names": [],
                "identity_verified": False, "_source_names": set()}
        group = groups[identity]
        group["count"] += 1
        group["citation_ids"].append(item.get("citation_id"))
        if basis not in group["grouping_bases"]:
            group["grouping_bases"].append(basis)
        if basis == "account_key":
            group["grouping_basis"] = basis
        name = item.get("source_name") or metadata.get("source_name")
        if isinstance(name, str) and name not in group["_source_names"]:
            group["source_names"].append(name)
            group["_source_names"].add(name)
    ordered = sorted(groups.values(), key=lambda row: (-row["count"], row["publisher_key"], row["grouping_basis"]))
    for group in ordered:
        group.pop("_source_names")
    top = ordered[:4]
    top_count = sum(group["count"] for group in top)
    return {"basis": "saved_public_material_recorded_publisher_fields",
        "total_material_count": total, "groups": ordered,
        "unknown_publisher_material_count": unknown,
        "top4_publisher_keys": [group["publisher_key"] for group in top],
        "top4_count": top_count, "top4_share": top_count/total if total else None,
        "top4_scope": "four_largest_recorded_publisher_key_or_label_groups",
        "independence_established": False, "semantic_truth_established": False}


def deterministic_report_inventory(items, research_context):
    """Inventory saved profile literals and raw collection diagnostics.

    Target binding is restricted to existing context Twitter accounts, in the
    same Twitter namespace as the source. Unknown handles remain unbound.
    Full saved bios and qualifiers are retained, with no 160-character cap.
    """
    context = research_context or {}
    known = {_twitter_key(row.get("account_key")) for row in _context_accounts(context) if isinstance(row, dict)} - {None}
    profiles, mentions = [], []
    for item in items:
        if not _profile_role(item):
            continue
        text, segments, complete, total = _profile_text(item)
        source = _source_key(item)
        entry = {"citation_id": item.get("citation_id"), "source_key": source,
                 "url": item.get("url"), "saved_profile_text": text,
                 "content_characters": total, "source_text_complete": complete,
                 "saved_text_scope": "all_saved_profile_text" if complete else "provided_profile_segments_only",
                 "original_document_completeness": (item.get("metadata") or {}).get("body_completeness", "not_established"),
                 "basis": "saved_profile_text", "profile_self_description_is_independent_verification": False}
        # Complete text is stored once, rather than copying it into a second
        # full segment table. Gaps retain their actual text and source offsets.
        entry["saved_segments"] = ([{"start": 0, "end": len(text)}] if text is not None else copy.deepcopy(segments))
        profiles.append(entry)
        scan_segments = [{"start": 0, "text": text}] if text is not None else segments
        for segment in scan_segments:
            for match in _HANDLE.finditer(segment["text"]):
                canonical = _twitter_key("twitter:" + match[1]) if _twitter_key(source) else None
                target = canonical if canonical in known else None
                mentions.append({"citation_id": item.get("citation_id"), "source_key": source,
                    "target_handle": match[0], "target_key": target, "quote": match[0],
                    "canonical_handle_key": canonical, "username_equivalence_only": True,
                    "start": segment["start"] + match.start(), "end": segment["start"] + match.end(),
                    "basis": "saved_profile_text_mention", "source_text_complete": complete,
                    "target_binding": "existing_same_platform_context_account" if target else "unresolved_handle_literal"})
    diagnostics = context.get("diagnostics") or []
    following = [copy.deepcopy(row) for row in diagnostics if isinstance(row, dict) and
                 (re.search(r"follow|relation", str(row.get("stage") or row.get("mode") or ""), re.I)
                  or re.search(r"/following(?:[/?#]|$)", str(row.get("source_url") or ""), re.I))]
    summary = context.get("summary") or {}
    return {"version": 2, "method": "saved_profile_ascii_handle_literals_and_raw_collection_scope",
        "handle_normalization": copy.deepcopy(_HANDLE_NORMALIZATION),
        "profile_sources": profiles, "profile_handle_mentions": mentions,
        "following_diagnostics": following, "collection_diagnostics": copy.deepcopy(diagnostics),
        "saved_public_material_distribution": _saved_material_distribution(items),
        "collection_scope": {"run_id": context.get("run_id"), "status": context.get("status"),
            "visited_count": summary.get("visited_count"), "account_budget_skipped": summary.get("account_budget_skipped"),
            "budget_skip_count_not_unique_accounts": True,
            "account_budget_skipped_uniqueness": "uniqueness_not_established",
            "account_budget_skipped_count_unit": "admission_attempts",
            "summary": copy.deepcopy(summary)},
        "boundaries": ["简介中的原样句柄提及只证明保存文本出现该字面量，不证明双向提及、隶属、支持、政治归属或账号核验。",
            "地区、领导/职务、党与刊物限定保留在原始简介；不能据名称相似合并主体。",
            "采集原始扫描数、访问数和预算跳过数是采集范围，不等于完整平台网络。",
            "预算跳过数是准入拒绝次数，唯一账号数未建立，不能称为相同数量的不同账号或已恢复URL。",
            "材料数量及前四发布来源占比仅描述保存样本；不证明报道独立性、账号身份或组织隶属。"],
        "semantic_truth_established": False}


_HANDLE_PAIR = re.compile(
    r"(?<![A-Za-z0-9_@])(?P<left>@[A-Za-z0-9_]{1,15})(?![A-Za-z0-9_])"
    r"\s*(?:与|和|及|、)\s*(?P<right>@?[A-Za-z0-9_]{1,15})(?![A-Za-z0-9_@])")
_USERNAME_CONFLICT = re.compile(
    r"\s*(?:(?:是|为|属于)(?:两个)?不同(?:的)?(?:账号|用户名|句柄)"
    r"|(?:不是|并非)同一(?:个)?(?:账号|用户名|句柄)"
    r"|的(?:用户名|句柄)?对应关系(?:也|仍|尚|还)*未(?:经|独立)?(?:核验|验证|确认))")
_COLLECTIVE_CONFLICT = re.compile(
    r"\s*(?:写法不同[，,]|[、，,])\s*@?[A-Za-z0-9_]{1,15}\s*(?:与|和)\s*"
    r"@?[A-Za-z0-9_]{1,15}(?![A-Za-z0-9_])\s*"
    r"(?:不能混同[，,]\s*均需另行核验|的(?:用户名|句柄)?对应关系(?:也|仍|尚|还)*未(?:经|独立)?(?:核验|验证|确认))")


def report_handle_identity_issues(markdown, items, context):
    """Block only explicit same-X-username contradictions in analysis prose.

    Rebuild authority from saved profile text and registered same-platform
    targets. Supplied inventory annotations cannot create this gate. Quoted
    text, different spellings, platform ambiguity and organization identity
    are outside it; this is not an external account or affiliation verifier.
    The collective templates retain the two captured report counterexamples.
    """
    if not isinstance(markdown, str):
        return []
    inventory = deterministic_report_inventory(items, context)
    evidence = {}
    for mention in inventory["profile_handle_mentions"]:
        if mention["target_binding"] == "existing_same_platform_context_account":
            evidence.setdefault(_twitter_key(mention["target_key"]), []).append(mention)
    lines, fenced = [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and not line.lstrip().startswith(">"):
            lines.append(line)
    # A source quotation, or an explicitly rejected quotation, is not the
    # report's own assertion. Raw source text is never rewritten.
    prose = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|"[^"\n]*"|`[^`\n]*`',
        lambda match: "\ufffc" * len(match[0]), "\n".join(lines))
    issues = []
    for sentence in re.split(r"[。！？\n]", prose):
        if re.search(r"(?<![A-Za-z0-9_])(?:instagram|mastodon|facebook|threads|bluesky)(?![A-Za-z0-9_])", sentence, re.I):
            continue
        for pair in _HANDLE_PAIR.finditer(sentence):
            key = _twitter_key("twitter:" + pair["left"][1:])
            right = _twitter_key("twitter:" + pair["right"].lstrip("@"))
            if key != right or key not in evidence:
                continue
            prefix, tail = sentence[:pair.start()], sentence[pair.end():]
            if re.search(r"(?:不能|不得|不应)(?:说|称|认为|将)|(?:并)?不是说"
                    r"|(?:没有|尚无|缺乏)证据(?:表明|显示|证明|支持)|误称|错误地(?:说|认为)", prefix):
                continue
            if not (_USERNAME_CONFLICT.match(tail) or _COLLECTIVE_CONFLICT.match(tail)):
                continue
            issues.append({"code": "canonical_handle_identity_conflict", "blocking": True,
                "canonical_handle_key": key, "literal_handles": [pair["left"], pair["right"]],
                "citation_ids": sorted({row["citation_id"] for row in evidence[key]}),
                "excerpt": sentence[:600],
                "explanation": "这两个 X 用户名仅大小写或前导 @ 不同，规范化为同一句柄。纠正不同账号或用户名对应关系待核验的断言；仍保留组织主体、互认、隶属与不同拼写旧号的核验边界。",
                "semantic_truth_established": False})
            if len(issues) >= _ADVISORY_LIMIT:
                return issues
    return issues


def _actor_present(paragraph, key, names):
    canonical = _twitter_key(key)
    if not canonical:
        return False
    handle = canonical.split(":", 1)[1]
    if re.search(r"(?<![A-Za-z0-9_])" + re.escape(handle) + r"(?![A-Za-z0-9_])", paragraph, re.I):
        return True
    return any(name in paragraph for name in names.get(canonical, ()))


def report_quality_issues(markdown, items, context, inventory):
    """Flag narrow conflicts for review; never certify absence or full truth.

    A denial may concern posts, reciprocity or a different date. The advisory
    therefore requests reconciliation with the literal bio, not an automatic
    truth replacement. Citation/body/actor checks constrain inventory evidence.
    """
    if not isinstance(markdown, str):
        return [{"code": "quality_non_text", "needs_reconciliation": True, "blocking": False,
                 "semantic_truth_established": False}]
    context = context or {}
    inventory = inventory if isinstance(inventory, dict) else deterministic_report_inventory(items, context)
    accounts = [row for row in _context_accounts(context) if isinstance(row, dict)]
    known = {_twitter_key(row.get("account_key")) for row in accounts} - {None}
    counts = Counter(str(row.get("name") or "") for row in accounts)
    names = {}
    for account in accounts:
        key, name = _twitter_key(account.get("account_key")), str(account.get("name") or "")
        if key and len(name) >= 2 and counts[name] == 1:
            names.setdefault(key, []).append(name)
    sources = {row.get("citation_id"): row for row in items if _profile_role(row)}
    source_texts = {identity: _profile_text(item) for identity, item in sources.items()}
    valid_mentions = []
    for mention in inventory.get("profile_handle_mentions") or []:
        if not isinstance(mention, dict) or mention.get("citation_id") not in sources:
            continue
        if not isinstance(mention.get("citation_id"), str):
            continue
        item = sources[mention["citation_id"]]
        text, segments, _, _ = source_texts[mention["citation_id"]]
        quote, start, end = mention.get("quote"), mention.get("start"), mention.get("end")
        if not isinstance(quote, str) or not _HANDLE.fullmatch(quote) or not isinstance(start, int) or not isinstance(end, int):
            continue
        exact = text[start:end] == quote if text is not None and 0 <= start <= end <= len(text) else any(
            part["start"] <= start <= end <= part["end"] and
            part["text"][start-part["start"]:end-part["start"]] == quote for part in segments)
        source = _source_key(item)
        target = "twitter:" + quote[1:].lower()
        if (not exact or not _twitter_key(source) or mention.get("source_key") != source
                or mention.get("target_key") != target or target not in known
                or mention.get("target_handle") != quote or mention.get("basis") != "saved_profile_text_mention"):
            continue
        if _twitter_key(source) == target:
            continue  # A self mention is not evidence of another actor.
        valid_mentions.append(mention)
    issues, total = [], 0
    seen = set()
    for index, paragraph in enumerate(re.split(r"\n\s*\n", markdown), 1):
        if not _NEGATIVE_MENTION.search(paragraph):
            continue
        actor_cache = {}
        def present(key):
            if key not in actor_cache:
                actor_cache[key] = _actor_present(paragraph, key, names)
            return actor_cache[key]
        for mention in valid_mentions:
            if not (present(mention["source_key"]) and present(mention["target_key"])):
                continue
            identity = (index, mention["citation_id"], mention["source_key"], mention["target_key"])
            if identity in seen:
                continue
            seen.add(identity)
            total += 1
            if len(issues) >= _ADVISORY_LIMIT:
                continue
            issues.append({"code": "negative_profile_mention_needs_reconciliation", "needs_reconciliation": True,
                "blocking": False, "paragraph": index, "excerpt": paragraph[:600],
                "citation_id": mention["citation_id"], "source_key": mention["source_key"],
                "target_key": mention["target_key"], "target_handle": mention["target_handle"],
                "quote": mention["quote"], "basis": mention["basis"], "start": mention["start"], "end": mention["end"],
                "reason": "同段否定提及但保存简介有原样句柄。请区分简介/帖子、单向/双向和观察时间，核对原句范围；不得升级隶属或核验。",
                "semantic_truth_established": False})
    if total > len(issues):
        issues.append({"code": "quality_advisory_limit", "needs_reconciliation": True, "blocking": False,
                       "omitted_count": total-len(issues), "total_advisory_count": total,
                       "semantic_truth_established": False})
    return issues
