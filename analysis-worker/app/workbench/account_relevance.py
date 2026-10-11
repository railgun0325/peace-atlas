"""Pure, reversible account noise policy; never identity or affiliation evidence.

The small exact-key list is a display/expansion default, not a global account
registry. Source import must strip RESERVED_METADATA; only an explicit manual
command may write this override. Derived graph fields are never authority.
"""
from __future__ import annotations

import re


GENERAL_PLATFORM_KEYS = frozenset({"twitter:youtube", "twitter:teamyoutube", "twitter:google",
                                   "twitter:instagram", "twitter:x", "instagram:instagram"})
RESERVED_METADATA = frozenset({"research_relevance_override"})
_HANDLES = {"twitter": re.compile(r"[A-Za-z0-9_]{1,15}\Z"),
            "instagram": re.compile(r"[A-Za-z0-9_.]{1,30}\Z")}


def _handle(platform, value):
    if not isinstance(platform, str) or platform not in _HANDLES or not isinstance(value, str):
        return None
    value = value.strip().lstrip("@")
    return value.lower() if _HANDLES[platform].fullmatch(value) else None


def _account_key(account):
    raw = account.get("account_key")
    fields_present = account.get("platform") is not None or account.get("handle") is not None
    field_key = None
    if fields_present:
        platform = account.get("platform")
        handle = _handle(platform, account.get("handle"))
        if handle is not None:
            field_key = platform + ":" + handle
    if raw is None:
        return field_key, False
    if not isinstance(raw, str) or raw.count(":") != 1:
        return None, True
    platform, handle = raw.split(":")
    # A saved key must already be a bare platform identity, not a URL or @alias.
    if platform not in _HANDLES or not _HANDLES[platform].fullmatch(handle):
        return None, True
    key = platform + ":" + handle.lower()
    if fields_present and field_key != key:
        return None, True
    return key, False


def _manual_override(account):
    metadata = account.get("metadata")
    raw = metadata.get("research_relevance_override") if isinstance(metadata, dict) else None
    if (not isinstance(raw, dict) or type(raw.get("version")) is not int or raw.get("version") != 1
            or raw.get("basis") != "manual_review" or not isinstance(raw.get("state"), str)
            or raw.get("state") not in {"included", "irrelevant", "excluded"}):
        return None
    return raw


def _result(state, hidden, reason, basis):
    return {"research_relevance": {"state": state, "default_hidden": hidden, "skip_expansion": hidden,
                                   "reason": reason, "basis": basis}}


def describe_relevance(account, explicit_root=False):
    """Describe defaults without mutating raw sources, status or account type.

    Only the caller's proven explicit seed flag overrides the generic default.
    Selecting a displayed node, its following/follower count, bio, type, badge,
    verification, model output or a derived relevance field cannot do so.
    """
    account = account if isinstance(account, dict) else {}
    if account.get("account_status") == "excluded" or account.get("status") == "excluded":
        return _result("excluded", True, "保留人工排除决定；默认不显示、不自动展开，原始来源仍保留。", "manual_status")
    manual = _manual_override(account)
    if manual is not None:
        excluded = manual["state"] in {"irrelevant", "excluded"}
        reason = "人工标记为无关或排除；显式起点也不覆盖此决定。" if excluded else "人工明确纳入本次研究；覆盖通用平台默认筛选。"
        if isinstance(manual.get("reason"), str) and manual["reason"].strip():
            reason += " " + manual["reason"].strip()[:400]
        return _result("excluded" if excluded else "included", excluded, reason + " 原始来源仍保留。", "manual_review")
    if explicit_root is True:
        return _result("included", False, "用户明确指定为研究起点；覆盖通用默认筛选，不代表主题相关、身份核验或组织隶属已获证明。", "explicit_root")
    key, conflict = _account_key(account)
    if key in GENERAL_PLATFORM_KEYS:
        return _result("general_platform", True,
                       f"精确账号 {key} 位于初始通用平台名单；默认隐藏并停止自动展开，原始证据仍保留。此规则不判定账号类型、核验或隶属。",
                       "exact_platform_handle")
    if conflict:
        return _result("unknown", False, "账号键与平台/用户名无效或不一致；未做自动排除，仍保留原始来源供核对。", "identity_conflict")
    return _result("unknown", False, "未命中精确通用名单，也没有明确人工决定；默认保留，不按名称、语言、粉丝量或关注关系猜测相关性。", "no_explicit_signal")
