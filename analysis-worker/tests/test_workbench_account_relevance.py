"""Exact noise signals, manual intent and raw evidence preservation."""
from copy import deepcopy

import pytest

from app.workbench.account_relevance import GENERAL_PLATFORM_KEYS, RESERVED_METADATA, describe_relevance


def signal(account, **options):
    result = describe_relevance(account, **options)
    assert set(result) == {"research_relevance"}
    value = result["research_relevance"]
    assert set(value) == {"state", "default_hidden", "skip_expansion", "reason", "basis"}
    assert type(value["default_hidden"]) is bool and type(value["skip_expansion"]) is bool
    assert isinstance(value["reason"], str) and value["reason"]
    assert isinstance(value["basis"], str) and value["basis"]
    return value


@pytest.mark.parametrize("key", sorted(GENERAL_PLATFORM_KEYS))
def test_only_initial_exact_generic_keys_are_hidden_and_not_expanded(key):
    value = signal({"account_key": key, "name": "Official / 公式 / 官方"})
    assert value["state"] == "general_platform"
    assert value["default_hidden"] is value["skip_expansion"] is True
    assert value["basis"] == "exact_platform_handle" and key in value["reason"]


@pytest.mark.parametrize("account", [
    {"account_key": "twitter:youtube_japan", "name": "YouTube"},
    {"account_key": "twitter:youtubeJP", "name": "YouTube Japan Official"},
    {"account_key": "twitter:teamyoutube_help"},
    {"account_key": "twitter:mygoogle"},
    {"account_key": "twitter:xpeace"},
    {"account_key": "instagram:youtube"},
    {"account_key": "instagram:google"},
    {"account_key": "twitter:ordinary", "name": "YouTube 公式 / 视频平台 / 官方", "followers_count": 999999999},
    {"account_key": "twitter:ordinary", "following": ["twitter:youtube"], "followers": ["twitter:youtube"]},
    {"account_key": "twitter:ordinary", "bio": "和平、劳工、宗教与政治", "organization": "Google", "tags": ["official"]},
    {"account_key": "twitter:ordinary", "account_type": "organization", "account_status": "verified"},
])
def test_similar_handles_languages_followers_and_types_do_not_exclude_ordinary_accounts(account):
    value = signal(account)
    assert value["state"] == "unknown"
    assert value["default_hidden"] is value["skip_expansion"] is False


@pytest.mark.parametrize("account", [
    {"account_key": "twitter:YouTube", "platform": "twitter", "handle": "@YOUTUBE"},
    {"platform": "twitter", "handle": " @YouTube "},
])
def test_ascii_case_and_plain_handle_input_use_the_same_exact_identity(account):
    assert signal(account)["state"] == "general_platform"


@pytest.mark.parametrize("account", [
    {"account_key": "twitter:youtube", "platform": "instagram", "handle": "youtube"},
    {"account_key": "twitter:youtube", "platform": "twitter", "handle": "ordinary"},
    {"account_key": "twitter:youtube", "platform": "twitter"},
    {"account_key": "twitter:@youtube", "platform": "twitter", "handle": "youtube"},
    {"account_key": "twitter:ｙｏｕｔｕｂｅ"},
    {"account_key": "twitter:yоutube"},  # Cyrillic o is not the ASCII identity.
    {"account_key": "https://x.com/youtube"},
    {"account_key": ["twitter:youtube"]},
])
def test_malformed_or_conflicting_identity_is_retained_without_guessing(account):
    value = signal(account)
    assert value["state"] == "unknown" and value["basis"] == "identity_conflict"
    assert value["default_hidden"] is False


@pytest.mark.parametrize("key", sorted(GENERAL_PLATFORM_KEYS))
def test_proven_explicit_root_overrides_generic_default_without_changing_identity(key):
    value = signal({"account_key": key, "account_type": "unknown", "account_status": "candidate"}, explicit_root=True)
    assert value["state"] == "included" and value["basis"] == "explicit_root"
    assert value["default_hidden"] is value["skip_expansion"] is False


@pytest.mark.parametrize("flag", [1, "true", [True], None])
def test_truthy_untyped_root_flags_do_not_override_defaults(flag):
    assert signal({"account_key": "twitter:youtube"}, explicit_root=flag)["state"] == "general_platform"


@pytest.mark.parametrize("state", ["irrelevant", "excluded"])
def test_explicit_root_cannot_override_manual_metadata_exclusion(state):
    account = {"account_key": "twitter:youtube", "metadata": {"research_relevance_override": {
        "version": 1, "state": state, "basis": "manual_review", "reason": "本次任务无关"}}}
    for explicit in (False, True):
        value = signal(account, explicit_root=explicit)
        assert value["state"] == "excluded" and value["basis"] == "manual_review"
        assert value["default_hidden"] is value["skip_expansion"] is True
        assert "本次任务无关" in value["reason"]


@pytest.mark.parametrize("field", ["status", "account_status"])
def test_manual_account_exclusion_dominates_root_and_manual_inclusion(field):
    account = {"account_key": "twitter:youtube", field: "excluded", "metadata": {"research_relevance_override": {
        "version": 1, "state": "included", "basis": "manual_review"}}}
    value = signal(account, explicit_root=True)
    assert value["state"] == "excluded" and value["basis"] == "manual_status"
    assert value["skip_expansion"] is True


def test_manual_inclusion_can_restore_generic_default_but_is_not_verification():
    account = {"account_key": "twitter:youtube", "status": "candidate", "account_type": "unknown",
               "metadata": {"research_relevance_override": {"version": 1, "state": "included", "basis": "manual_review"}}}
    before = deepcopy(account)
    value = signal(account)
    assert value["state"] == "included" and value["default_hidden"] is False
    assert account == before


@pytest.mark.parametrize("raw", [None, "excluded", ["excluded"], True,
    {"version": True, "state": "excluded", "basis": "manual_review"},
    {"version": 2, "state": "excluded", "basis": "manual_review"},
    {"version": 1, "state": "excluded", "basis": "feed"},
    {"version": 1, "state": "excluded", "basis": "configured_ai"},
    {"version": 1, "state": "general_platform", "basis": "manual_review"},
])
def test_untyped_or_nonmanual_override_cannot_exclude_an_ordinary_follower(raw):
    value = signal({"account_key": "twitter:ordinary", "metadata": {"research_relevance_override": raw}})
    assert value["state"] == "unknown" and value["skip_expansion"] is False


def test_reserved_derived_fields_and_root_claims_are_not_input_authority():
    account = {"account_key": "twitter:ordinary", "research_relevance": {"state": "excluded", "default_hidden": True},
               "research_relevance_override": {"version": 1, "state": "excluded", "basis": "manual_review"},
               "metadata": {"research_relevance": {"state": "excluded", "basis": "manual_review"},
                            "explicit_root": True, "account_key": "twitter:youtube"}}
    assert signal(account)["state"] == "unknown"
    assert RESERVED_METADATA == {"research_relevance_override"}
    generic = {"account_key": "twitter:youtube", "metadata": {"explicit_root": True},
               "research_relevance": {"state": "included", "default_hidden": False}}
    assert signal(generic)["state"] == "general_platform"


@pytest.mark.parametrize("raw", [None, "legacy", [], True])
def test_old_or_bad_metadata_keeps_an_unknown_account(raw):
    assert signal({"account_key": "twitter:ordinary", "metadata": raw})["state"] == "unknown"


def test_projection_is_pure_and_never_removes_saved_sources_or_followers():
    account = {"account_key": "twitter:youtube", "platform": "twitter", "handle": "youtube", "status": "verified",
               "account_type": "organization", "metadata": {"profile": {"bio": "Original source"}},
               "evidence_urls": ["https://x.com/alpha/following/"], "followers": ["twitter:alpha", "twitter:beta"],
               "url": "https://x.com/youtube/", "content": "Original saved body"}
    before = deepcopy(account)
    first = describe_relevance(account)
    first["research_relevance"]["state"] = "included"
    assert describe_relevance(account)["research_relevance"]["state"] == "general_platform"
    assert account == before


@pytest.mark.parametrize("raw", [
    {"version": 1.0, "state": "excluded", "basis": "manual_review"},
    {"version": 1, "state": [], "basis": "manual_review"},
    {"version": 1, "state": {}, "basis": "manual_review"},
])
def test_legacy_json_override_shape_cannot_crash_or_acquire_manual_authority(raw):
    account = {"account_key": "twitter:ordinary", "metadata": {"research_relevance_override": raw}}
    before = deepcopy(account)
    value = signal(account)
    assert value["state"] == "unknown" and value["skip_expansion"] is False
    assert account == before


@pytest.mark.parametrize("platform", [[], {}, True])
def test_bad_legacy_platform_keeps_conflicting_identity_without_exception(platform):
    value = signal({"account_key": "twitter:youtube", "platform": platform, "handle": "youtube"})
    assert value["state"] == "unknown" and value["basis"] == "identity_conflict"
    assert value["default_hidden"] is False


@pytest.mark.parametrize("account", [None, [], "twitter:youtube", True])
def test_nonobject_account_keeps_unknown_instead_of_excluding_or_crashing(account):
    value = signal(account)
    assert value["state"] == "unknown" and value["skip_expansion"] is False
