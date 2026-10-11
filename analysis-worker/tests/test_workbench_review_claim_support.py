"""Finite qualifier support for checked independent claim evidence, without I/O.

This gate checks protected identity qualifiers, not arbitrary natural-language
entailment or a person's political identity. Candidate mention support remains
the separate relationship_support contract.
"""
import copy

import pytest

from app.workbench import review_claim_support as support


CODES = {
    "marker_support_missing", "qualifier_support_missing",
    "leader_support_missing", "unknown_claim_qualifier",
}


def record(*qualifiers, object_label="全学連", **extra):
    return {"object_label": object_label, "identity_qualifiers": list(qualifiers), **extra}


def proof(quote, *, independent=True, valid=True, **extra):
    return {"quote": quote, "independent": independent, "valid": valid, **extra}


def missing(claim, proofs):
    value = support.missing_claim_qualifiers(claim, proofs)
    assert isinstance(value, list) and set(value) <= CODES
    assert len(value) == len(set(value))
    return set(value)


def test_audited_kansai_information_account_does_not_prove_the_saved_newspaper_role():
    # Exact saved qualifiers/object from owned, public QA. The compact quote
    # models its documented account designation, rather than claiming to be
    # a verbatim reproduction of the complete page capture.
    # final-delivery/real-review-failed144-confirmed11-triple-audit.json rows[1]
    claim = record("marker:关西", "marker:机关报", "marker:関西",
                   id="1b312a90a203fc0dfe505aae14b612de448b8552a8739e72022c33c022dfac75",
                   subject_key="twitter:zengakuren_west", relationship="affiliation")
    proofs = [proof("全学連関西通信のX https://x.com/Zengakuren_west")]
    assert missing(claim, proofs) == {"marker_support_missing"}


@pytest.mark.parametrize("qualifier", ["marker:機関紙", "marker:机关报", "marker:機關報"])
@pytest.mark.parametrize("word", ["機関紙", "机关报", "機關報"])
def test_newspaper_role_uses_a_finite_japanese_chinese_equivalence(qualifier, word):
    assert missing(record(qualifier), [proof(f"全学連の{word}「関西通信」です。")]) == set()


def test_nfkc_typography_does_not_remove_a_real_publication_qualifier():
    claim = record("marker:机关报", object_label="ＡＢＣ学生会")
    assert missing(claim, [proof("ABC学生会の機関紙『通信』です。")]) == set()


@pytest.mark.parametrize("quote", [
    "全学連関西通信 第12号を掲載しました。", "全学連の通信を発行しています。",
    "全学連の刊物を紹介します。", "全学連の機関誌を掲載しました。",
])
def test_general_newsletter_publication_or_magazine_is_not_promoted_to_newspaper(quote):
    assert missing(record("marker:機関紙"), [proof(quote)]) == {"marker_support_missing"}


@pytest.mark.parametrize("quote", [
    "全学連の大会を紹介します。他団体の機関紙は別ページです。",
    "他団体の機関紙を読みました。全学連は大会を開催します。",
    "機関紙の記事一覧 / 全学連の大会を紹介します。",
])
def test_publication_word_in_a_different_subject_or_clause_is_not_qualifier_support(quote):
    assert missing(record("marker:機関紙"), [proof(quote)]) == {"marker_support_missing"}


@pytest.mark.parametrize("region", ["関西", "關西", "关西"])
def test_region_synonym_is_supported_when_it_modifies_the_target_organization(region):
    assert missing(record("marker:" + region), [proof("全学連関西支部の公式案内です。")]) == set()


@pytest.mark.parametrize("quote", [
    "全学連の大会を開催します。関西の天気をお知らせします。",
    "全学連関東支部の案内です。関西は他の団体です。",
    "関西の学生会。全学連の大会。", "全学連の案内 / 関西の別団体の案内",
])
def test_region_elsewhere_or_on_another_branch_does_not_support_target_qualifier(quote):
    assert missing(record("marker:関西"), [proof(quote)]) == {"marker_support_missing"}


@pytest.mark.parametrize("quote", [
    "全学連（関西支部）の案内です。", "全学連の関西支部の案内です。",
])
def test_composite_regional_qualifier_requires_the_same_explicit_target_context(quote):
    assert missing(record("qualifier:関西支部"), [proof(quote)]) == set()


def test_a_different_branch_does_not_support_composite_regional_qualifier():
    assert missing(record("qualifier:関西支部"), [proof("全学連（関東支部）の案内です。")]) == {"qualifier_support_missing"}


@pytest.mark.parametrize("quote", [
    "全学連（矢嶋委員長）の公式サイトです。", "全学連の委員長は矢嶋です。",
    "矢嶋委員長の全学連です。",
])
def test_leader_name_requires_an_explicit_role_for_the_target(quote):
    claim = record("leader:矢嶋", "qualifier:矢嶋委員長")
    assert missing(claim, [proof(quote)]) == set()


@pytest.mark.parametrize("quote", [
    "矢嶋氏が参加しました。全学連の委員長は田中です。",
    "全学連の大会。別団体の矢嶋委員長が参加しました。",
    "全学連から田中委員長が参加。矢嶋は別組織です。",
    "全学連の案内 / 別団体の委員長は矢嶋",
])
def test_leader_cooccurrence_is_not_a_target_leadership_relationship(quote):
    assert missing(record("leader:矢嶋"), [proof(quote)]) == {"leader_support_missing"}


def test_top_level_and_nested_saved_qualifiers_form_a_union():
    claim = record("marker:機関紙", metadata={"identity_qualifiers": ["marker:关西", "marker:機関紙"]})
    assert missing(claim, [proof("全学連関西支部の機関紙『通信』です。")]) == set()
    assert missing(claim, [proof("全学連関西通信のXです。")]) == {"marker_support_missing"}


@pytest.mark.parametrize("independent,valid", [(False, True), (True, False), (False, False)])
def test_only_checked_independent_quotes_can_support_a_saved_qualifier(independent, valid):
    proofs = [proof("全学連の機関紙『通信』です。", independent=independent, valid=valid)]
    assert missing(record("marker:機関紙"), proofs) == {"marker_support_missing"}


def test_self_quote_cannot_borrow_an_unrelated_independent_sources_flag():
    proofs = [proof("全学連の機関紙『通信』です。", independent=False),
              proof("全学連の大会を紹介します。")]
    assert missing(record("marker:機関紙"), proofs) == {"marker_support_missing"}


def test_separate_quotes_cannot_be_concatenated_into_a_missing_subject_role():
    proofs = [proof("全学連の大会です。"), proof("他団体の機関紙を紹介します。")]
    assert missing(record("marker:機関紙"), proofs) == {"marker_support_missing"}


def test_unchecked_body_title_or_statement_fields_cannot_supply_the_qualifier():
    affirmative = "全学連の機関紙『通信』です。"
    claim = record("marker:機関紙", statement=affirmative, title=affirmative)
    proofs = [proof("全学連の大会を紹介します。", body=affirmative, text=affirmative, title=affirmative,
                    read_saved=affirmative)]
    assert missing(claim, proofs) == {"marker_support_missing"}


@pytest.mark.parametrize("qualifier", ["marker:未知区分", "qualifier:未知限定", "unrecognized:value"])
def test_unknown_qualifier_is_a_conservative_fixed_gap(qualifier):
    assert missing(record(qualifier), [proof("全学連の公式サイトです。")]) == {"unknown_claim_qualifier"}


def test_no_qualifiers_are_not_replaced_with_inferred_roles_or_mutated_inputs():
    claim = record(statement="全学連の機関紙かもしれない。")
    proofs = [proof("全学連の大会。矢嶋氏が参加。関西の天気。")]
    before = copy.deepcopy((claim, proofs))
    assert missing(claim, proofs) == set()
    assert (claim, proofs) == before


@pytest.mark.parametrize("claim", [
    None, [], "全学連", {"object_label": []}, {"object_label": {}}, {"object_label": 3},
    record("marker:機関紙", metadata=[]), record("marker:機関紙", metadata="invalid"),
    record("marker:機関紙", metadata=None),
    {"object_label": "全学連", "identity_qualifiers": None},
    {"object_label": "全学連", "identity_qualifiers": "marker:機関紙"},
    {"object_label": "全学連", "identity_qualifiers": {}},
    {"object_label": "全学連", "identity_qualifiers": [3]},
    {"object_label": "全学連", "identity_qualifiers": [{"marker": "機関紙"}]},
    record("marker:機関紙", metadata={"identity_qualifiers": "marker:关西"}),
    record("marker:機関紙", metadata={"identity_qualifiers": [False]}),
])
def test_malformed_claim_schema_raises_instead_of_becoming_a_semantic_gap(claim):
    with pytest.raises(support.ClaimSupportSchemaError):
        support.missing_claim_qualifiers(claim, [proof("全学連の機関紙『通信』です。")])


@pytest.mark.parametrize("proofs", [
    None, {}, "raw page text", [None], [[]], ["untyped excerpt"],
    [proof(3)], [proof([])], [proof({})],
    [proof("全学連の機関紙です。", independent="true")],
    [proof("全学連の機関紙です。", independent=1)],
    [proof("全学連の機関紙です。", valid="true")],
    [proof("全学連の機関紙です。", valid=1)],
])
def test_malformed_proof_schema_raises_instead_of_becoming_a_semantic_gap(proofs):
    with pytest.raises(support.ClaimSupportSchemaError):
        support.missing_claim_qualifiers(record("marker:機関紙"), proofs)


def test_malformed_message_cannot_echo_untrusted_payload():
    marker = "fixture-secret-must-not-be-disclosed"
    with pytest.raises(support.ClaimSupportSchemaError) as caught:
        support.missing_claim_qualifiers(record("marker:機関紙"), [proof({"untrusted": marker})])
    assert marker not in str(caught.value)
    assert isinstance(caught.value, ValueError)


def test_quote_beyond_the_existing_two_thousand_character_limit_is_schema_error():
    with pytest.raises(support.ClaimSupportSchemaError):
        support.missing_claim_qualifiers(record("marker:機関紙"), [proof("証" * 2001)])


def test_more_than_three_checked_proofs_is_schema_error():
    proofs = [proof("全学連の機関紙『通信』です。") for _ in range(4)]
    with pytest.raises(support.ClaimSupportSchemaError):
        support.missing_claim_qualifiers(record("marker:機関紙"), proofs)


def test_leader_name_prefix_cannot_support_a_different_complete_name():
    assert missing(record("leader:矢嶋"), [proof("全学連の委員長は矢嶋太郎です。")]) == {"leader_support_missing"}
