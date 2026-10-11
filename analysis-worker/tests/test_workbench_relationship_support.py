"""Real counterexamples for public relationship evidence support gates."""
from __future__ import annotations

import pytest

from app.workbench.relationship_support import affiliation_support_error, explicit_profile_claims


def evidence(kind="profile", account="twitter:alpha", excerpt="所属：団体A。", **extra):
    return {"id":"known-source", "type":kind, "account_key":account, "excerpt":excerpt, **extra}


@pytest.mark.parametrize("bio", [
    "団体A公式アカウントではありません。個人です。",
    "所属：団体Aではありません。",
    "元所属：団体A。現在は無所属。",
    "引用：「団体A公式アカウント」。これは他人の自己紹介です。",
    "「団体A公式アカウント」",
    "団体A公式アカウントの投稿を読んでいます。",
    "元団体A公式アカウントです。",
    "affiliation: Former Organization A",
])
def test_negative_historical_and_reported_bios_are_not_self_declared(bio):
    assert explicit_profile_claims(bio) == []


@pytest.mark.parametrize(("bio", "organization"), [
    ("団体A公式アカウント。", "団体A"),
    ("団体Aの公式アカウントです。", "団体A"),
    ("「団体A」の公式アカウントです。", "団体A"),
    ("所属：団体A。", "団体A"),
    ("affiliation: Organization A", "Organization A"),
    ("団体A公式アカウント。差別を許さない。", "団体A"),
])
def test_bounded_positive_current_declarations_are_preserved(bio, organization):
    assert explicit_profile_claims(bio)[0][0] == organization


def test_rule_gate_does_not_discard_raw_bio_and_deduplicates_claims():
    bio = "所属：団体A。所属：団体A。お問い合わせはDMまで。"
    original = bio
    assert explicit_profile_claims(bio) == [("団体A", "所属：団体A")]
    assert bio == original


def test_same_subject_follow_is_not_organizational_text_support():
    rows = [evidence("visible_follow_list",excerpt="twitter:alpha → twitter:group_a")]
    assert "关注列表" in affiliation_support_error("twitter:alpha", "団体A", rows)


def test_own_unrelated_post_is_not_organizational_text_support():
    assert affiliation_support_error("twitter:alpha","団体A",[evidence("post",excerpt="今日は天気が良い。")])


def test_mixed_own_unrelated_and_other_profile_cannot_launder_subject():
    rows = [evidence("post",excerpt="今日は天気が良い。"), evidence(account="twitter:beta")]
    assert affiliation_support_error("twitter:alpha","団体A",rows)


def test_another_account_only_is_rejected_and_own_affirmative_text_can_pass():
    assert affiliation_support_error("twitter:alpha","団体A",[evidence(account="twitter:beta")])
    assert affiliation_support_error("twitter:alpha","団体A",[evidence()]) is None


def test_display_name_and_bio_topics_are_not_organizational_text_support():
    rows = [evidence(excerpt="闘う労働組合の全国ネットワークを茨城県から作り出そう。", display_name="茨城県労働組合交流センター")]
    assert affiliation_support_error("twitter:alpha","茨城県労働組合交流センター",rows)


@pytest.mark.parametrize(("organization","excerpt"),[
    ("現代革命労働者党（現労党）","全学連ならびに現労党として参加しました。"),
    ("全日本学生自治会総連合（全学連）","全日本学生自治会総連合として大会を開催します。"),
    ("Organization A (OAA)","Affiliation: OAA"),
    ("団体A（略称：DA）","DAとして案内します。"),
    ("改憲・戦争阻止！大行進","「改憲・戦争阻止！大行進」呼びかけ人"),
    ("広島大学学生自治会","広島大学の学生自治会です。"),
])
def test_explicit_parenthetical_aliases_and_typography_are_supported(organization, excerpt):
    assert affiliation_support_error("twitter:alpha",organization,[evidence(excerpt=excerpt)]) is None


def test_parenthetical_faction_or_region_is_not_silently_dropped():
    assert affiliation_support_error("twitter:alpha","全学連（矢嶋委員長）",[evidence(excerpt="全学連（別の委員長）です。")])
    assert affiliation_support_error("twitter:alpha","団体A（関西支部）",[evidence(excerpt="団体A（関東支部）です。")])


@pytest.mark.parametrize("excerpt",[
    "所属：団体Aではありません。",
    "元所属：団体A。現在は無所属。",
    "引用：団体A公式アカウントは別の人です。",
])
def test_known_negation_historical_or_reported_hits_cannot_pass_support(excerpt):
    assert affiliation_support_error("twitter:alpha","団体A",[evidence(excerpt=excerpt)])


def test_generic_parenthetical_label_is_not_an_organization_alias():
    assert affiliation_support_error("twitter:alpha","団体A（公式）",[evidence(excerpt="個人の公式アカウントです。")])


def test_latin_alias_and_name_prefix_cannot_match_different_words_or_entities():
    assert affiliation_support_error("twitter:alpha","Organization A",[evidence(excerpt="Affiliation: Organization AB")])
    assert affiliation_support_error("twitter:alpha","団体A（略称：DA）",[evidence(excerpt="TODAY is sunny")])
    assert affiliation_support_error("twitter:alpha","団体A",[evidence(excerpt="団体ABです。")])


def test_text_support_is_not_a_claim_entailment_or_identity_verification():
    # A public topical mention can pass this bounded gate. It cannot be called
    # verified affiliation or support for every model-generated claim sentence.
    assert affiliation_support_error("twitter:alpha","団体A",[evidence(excerpt="団体Aの機関紙を紹介しています。")]) is None


def test_bad_input_types_fail_closed_without_io():
    assert explicit_profile_claims(None) == []
    assert affiliation_support_error([],"団体A",[]) is not None
    assert affiliation_support_error("twitter:alpha",[],[]) is not None
    assert affiliation_support_error("twitter:alpha","団体A",None) is not None


_REAL_SWU_PROFILE = (
    "東京北部地域（豊島区・板橋区・練馬区・北区・文京区）の労組交流センター"
    "【戦争絶対反対を軸とした闘う労働運動】Ｘに再編成。\n"
    "東京北部ユニオンの労働相談は下記のサイトを参照の上、０３（６９１４）０４８７へ。"
)


@pytest.mark.parametrize("organization",[
    "東京北部地域の労組交流センター",
    "東京北部地域労組交流センター",
])
def test_real_swu_regional_identity_with_inserted_administrative_list(organization):
    # Actual Flash batches 1/2/5 rejected this own-profile identity because
    # the administrative detail interrupts the complete organization phrase.
    rows=[evidence(account="twitter:swu_kawa",excerpt=_REAL_SWU_PROFILE)]
    assert affiliation_support_error("twitter:swu_kawa",organization,rows) is None


def test_a_bare_region_is_not_an_organization_identity():
    rows=[evidence(account="twitter:swu_kawa",excerpt=_REAL_SWU_PROFILE)]
    assert affiliation_support_error("twitter:swu_kawa","東京北部地域",rows)


@pytest.mark.parametrize("excerpt",[
    "東京南部地域（豊島区・板橋区）の労組交流センターです。",
    "東京北部地域（矢嶋委員長）の労組交流センターです。",
    "東京北部地域（別の労組）の労組交流センターを紹介します。",
    "東京北部地域（文京区・他の団体）の労組交流センターを紹介します。",
    "東京北部地域（豊島区・板橋区）の他団体『労組交流センター』を紹介します。",
])
def test_regional_detail_rule_does_not_drop_other_identity_qualifiers(excerpt):
    rows=[evidence(account="twitter:swu_kawa",excerpt=excerpt)]
    assert affiliation_support_error("twitter:swu_kawa","東京北部地域の労組交流センター",rows)


def test_regional_profile_cannot_supply_another_subjects_missing_support():
    rows=[evidence("post",account="twitter:alpha",excerpt="今日は晴れです。"),
          evidence(account="twitter:swu_kawa",excerpt=_REAL_SWU_PROFILE)]
    assert affiliation_support_error("twitter:alpha","東京北部地域の労組交流センター",rows)
