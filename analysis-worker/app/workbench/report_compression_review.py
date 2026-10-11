"""Structural support and bounded model-consistency checks for compression.

An exact quote proves correspondence with saved text. A second model task
checks consistency; neither check independently establishes external truth.
"""
from __future__ import annotations

import copy
import re

from .pipeline import PipelineError


COMPRESSION_REVIEW_PROMPT = (
    "审阅公开材料压缩笔记，只根据本批records和evidence_units的原始保存正文、阅读与元数据核对，不能联网。"
    '返回JSON {"accepted":true,"issues":[{"code":"source_support|unit_coverage|negation|date|scope|identity|citation",'
    '"explanation":"具体问题","citation_ids":["S001"],"unit_ids":["S001~P1"],"finding_indexes":[0]}],'
    '"reviewed_finding_indexes":[0],"reviewed_unit_ids":["S001~P1"]}。'
    "finding索引从0开始。逐条检查全部findings，逐片检查全部evidence_units；两个reviewed列表必须各列一次且覆盖本批全部。"
    "只有没有须修问题才accepted=true且issues为空。合法ID或逐字quote不证明statement受支持；"
    "一条泛化结论挂全ID也不证明各来源、每片或重要限定已被表示。"
    "逐statement逐source核对support_refs：每条引用是否真的支持该说法，不允许以一条支持整体；"
    "检查否定、取消、撤回、更正、反证、不同主体/派别、领导和刊物限定、计划/已发生、数值、发布时间/JST/活动日/观察日。"
    "每片会改变研究判断的说法和限制必须保留；材料无关或不足也需对应uncertainty，不得被其他来源替代。"
    "同句和同URL不能合成独立支持；不同following目标/时间/方向分别核对，关注不证明背书、隶属、互认或持续关系。"
    "简介和帖子自称不是独立身份核验。上层records是此前AI笔记，不能充作原始来源；必须回查evidence_units。"
    "最多20项具体issues；原样quote核对和本模型审阅都不是独立事实核验。"
)


def _unknown_ids(text, allowed):
    return any(identity not in allowed for identity in re.findall(r"(?<![A-Za-z0-9])S\d+(?![A-Za-z0-9])", text))


def _valid_review_issue(issue, identities, indexes, evidence_units):
    return (isinstance(issue, dict) and set(issue) == {"code", "explanation", "citation_ids", "unit_ids", "finding_indexes"}
        and isinstance(issue["code"], str) and issue["code"] in {"source_support", "unit_coverage", "negation", "date", "scope", "identity", "citation"}
        and isinstance(issue["explanation"], str) and 1 <= len(issue["explanation"].strip()) <= 1000
        and isinstance(issue["citation_ids"], list)
        and all(isinstance(identity, str) and identity in identities for identity in issue["citation_ids"])
        and isinstance(issue["unit_ids"], list)
        and all(isinstance(identity, str) and identity in evidence_units for identity in issue["unit_ids"])
        and isinstance(issue["finding_indexes"], list)
        and all(isinstance(index, int) and not isinstance(index, bool) and index in indexes for index in issue["finding_indexes"]))


def note_structure_issues(result, allowed_ids, evidence_units):
    """Bounded repair feedback; never normalize a quote into a false match."""
    issues, included, cited = [], set(), set()
    def add(code, explanation, **fields):
        if len(issues) < 20:
            issues.append({"code": code, "explanation": explanation, **fields})
    if (not isinstance(result, dict) or set(result) != {"findings", "limitations"}
            or not isinstance(result.get("findings"), list) or not result["findings"]
            or not isinstance(result.get("limitations"), list)):
        add("note_schema", "必须返回仅含 findings 非空数组与 limitations 字符串数组的完整 JSON。")
        return issues
    for index, finding in enumerate(result["findings"]):
        if not isinstance(finding, dict):
            add("finding_schema", "finding 必须为对象。", finding_index=index)
            continue
        ids = finding.get("citation_ids")
        valid_ids = isinstance(ids, list) and all(isinstance(identity, str) and identity in allowed_ids for identity in ids)
        if (set(finding) != {"statement", "citation_ids", "kind", "support_refs"}
                or not isinstance(finding.get("statement"), str) or not finding["statement"].strip()
                or not isinstance(finding.get("kind"), str) or finding["kind"] not in {"source_claim", "observation", "inference", "uncertainty"}
                or not valid_ids or not ids or (valid_ids and len(ids) != len(set(ids)))):
            add("finding_schema", "完整 finding 须有非空 statement、合法 kind、已知且不重复的 citation_ids 与 support_refs。", finding_index=index)
        references = finding.get("support_refs")
        if not isinstance(references, list) or not references:
            add("support_schema", "每条 finding 须有原始片段的 support_refs。", finding_index=index)
            continue
        supported, seen = set(), set()
        for ref_index, reference in enumerate(references):
            location = {"finding_index": index, "support_ref_index": ref_index}
            if (not isinstance(reference, dict) or set(reference) != {"unit_id", "citation_id", "quote"}
                    or not isinstance(reference.get("unit_id"), str) or reference["unit_id"] not in evidence_units
                    or not isinstance(reference.get("citation_id"), str) or reference["citation_id"] not in allowed_ids
                    or not isinstance(reference.get("quote"), str) or not reference["quote"].strip() or not 1 <= len(reference["quote"]) <= 220):
                add("support_schema", "support_ref 必须为已知 unit_id、对应 citation_id 与 1 至 220 字非空逐字 quote。", **location)
                continue
            unit = evidence_units[reference["unit_id"]]
            signature = (reference["unit_id"], reference["citation_id"], reference["quote"])
            if signature in seen:
                add("duplicate_support", "同一 finding 不得重复相同的 support_ref。", **location)
            seen.add(signature)
            supported.add(reference["citation_id"])
            included.add(reference["unit_id"])
            if reference["citation_id"] != unit["citation_id"] or reference["quote"] not in unit["text"]:
                example = ((unit.get("reading") or {}).get("quote") or unit["text"])[:220]
                add("exact_quote", "引用须为指定原片段中的连续逐字子串；不得省略中间行、拼接、不标示替换或借用另一来源。", **location,
                    unit_id=reference["unit_id"], citation_id=unit["citation_id"], supplied_quote=reference["quote"],
                    allowed_quote_examples=[example], original_fragment_sha256=unit.get("fragment_sha256"))
        if valid_ids:
            cited.update(ids)
            if set(ids) != supported:
                add("citation_support", "citation_ids 须与本条 support_refs 的实际来源集合完全一致。", finding_index=index)
        if isinstance(finding.get("statement"), str) and _unknown_ids(finding["statement"], allowed_ids):
            add("unknown_citation", "statement 不能包含本批之外的来源 ID。", finding_index=index)
    if cited != allowed_ids or included != set(evidence_units):
        add("unit_coverage", "全部原始来源和每个阅读片段必须被实际 support_ref 表示。",
            missing_citation_ids=sorted(allowed_ids-cited), missing_unit_ids=sorted(set(evidence_units)-included))
    if any(not isinstance(value, str) or _unknown_ids(value, allowed_ids) for value in result["limitations"]):
        add("limitations_schema", "limitations 只能为字符串且不能引用本批未知 ID。")
    return issues


def review_structure_issues(result, note, evidence_units):
    """Explain invalid review fields without filling or deleting model output."""
    issues = []
    def add(code, explanation, **fields):
        if len(issues) < 20:
            issues.append({"code": code, "explanation": explanation, **fields})
    if not isinstance(result, dict):
        return [{"code": "review_schema", "explanation": "审核须返回完整 JSON 对象，不能返回其他类型。"}]
    fields = {"accepted", "issues", "reviewed_finding_indexes", "reviewed_unit_ids"}
    if set(result) != fields:
        add("review_schema", "审核须且只能返回四个规定字段。", missing_fields=sorted(fields-set(result)), extra_fields=sorted(set(result)-fields))
    expected_indexes, expected_units = set(range(len(note["findings"]))), set(evidence_units)
    for field, expected, item_type in (("reviewed_finding_indexes", expected_indexes, int), ("reviewed_unit_ids", expected_units, str)):
        actual = result.get(field)
        if not isinstance(actual, list) or any(not isinstance(value, item_type) or (item_type is int and isinstance(value, bool)) for value in actual):
            add("review_coverage_type", "覆盖字段须为完整且不重复的规定类型数组。", field=field)
            continue
        if set(actual) != expected or len(actual) != len(expected):
            seen, duplicates = set(), []
            for value in actual:
                if value in seen:
                    duplicates.append(value)
                seen.add(value)
            add("review_coverage", "finding 索引从0开始，只能列实际 findings；逐片 ID 须覆盖本批全部，不能多列、漏列或重复。",
                field=field, expected_count=len(expected), actual_count=len(actual),
                missing_values=sorted(expected-set(actual)), extra_values=sorted(set(actual)-expected), duplicate_values=duplicates)
    if (not isinstance(result.get("accepted"), bool) or not isinstance(result.get("issues"), list)
            or (isinstance(result.get("issues"), list) and (len(result["issues"]) > 20 or result.get("accepted") != (not result["issues"])))):
        add("review_decision", "accepted 必须为布尔值，只有 issues 为空才 true；具体问题最多20项，不得删掉真实问题来通过格式。")
    if isinstance(result.get("issues"), list):
        identities = {unit["citation_id"] for unit in evidence_units.values()}
        for index, issue in enumerate(result["issues"]):
            if not _valid_review_issue(issue, identities, expected_indexes, evidence_units):
                add("review_issue_fields", "具体 issue 的 code、说明、来源/片段 ID 和 finding 索引须符合规定类型且属于本批。", issue_index=index)
    return issues


def validate_note(result, allowed_ids, evidence_units):
    if (not isinstance(result, dict) or set(result) != {"findings", "limitations"}
            or not isinstance(result["findings"], list) or not result["findings"]
            or not isinstance(result["limitations"], list)):
        raise PipelineError("AI 大材料压缩格式无效；原文和阅读进度已保留，未保存报告。")
    included_ids, included_units = set(), set()
    for finding in result["findings"]:
        if (not isinstance(finding, dict) or set(finding) != {"statement", "citation_ids", "kind", "support_refs"}
                or not isinstance(finding["statement"], str) or not finding["statement"].strip()
                or not isinstance(finding["kind"], str)
                or finding["kind"] not in {"source_claim", "observation", "inference", "uncertainty"}
                or not isinstance(finding["citation_ids"], list) or not finding["citation_ids"]
                or any(not isinstance(identity, str) or identity not in allowed_ids for identity in finding["citation_ids"])
                or len(finding["citation_ids"]) != len(set(finding["citation_ids"]))
                or not isinstance(finding["support_refs"], list) or not finding["support_refs"]):
            raise PipelineError("AI 大材料压缩字段、引用或片段支持无效；未保存报告。")
        supported_ids, seen_refs = set(), set()
        for reference in finding["support_refs"]:
            if (not isinstance(reference, dict) or set(reference) != {"unit_id", "citation_id", "quote"}
                    or not isinstance(reference["unit_id"], str) or reference["unit_id"] not in evidence_units
                    or not isinstance(reference["citation_id"], str) or reference["citation_id"] not in allowed_ids
                    or not isinstance(reference["quote"], str) or not reference["quote"].strip()
                    or not 1 <= len(reference["quote"]) <= 220):
                raise PipelineError("AI 大材料压缩片段支持字段无效；未保存报告。")
            unit = evidence_units[reference["unit_id"]]
            if reference["citation_id"] != unit["citation_id"] or reference["quote"] not in unit["text"]:
                raise PipelineError("AI 大材料压缩支持摘录不属于对应原始来源片段；未保存报告。")
            signature = (reference["unit_id"], reference["citation_id"], reference["quote"])
            if signature in seen_refs:
                raise PipelineError("AI 大材料压缩片段支持重复；未保存报告。")
            seen_refs.add(signature)
            supported_ids.add(reference["citation_id"])
            included_units.add(reference["unit_id"])
        if set(finding["citation_ids"]) != supported_ids or _unknown_ids(finding["statement"], allowed_ids):
            raise PipelineError("AI 大材料压缩说法引用与原始片段支持不一致；未保存报告。")
        included_ids.update(finding["citation_ids"])
    if any(not isinstance(value, str) or _unknown_ids(value, allowed_ids) for value in result["limitations"]):
        raise PipelineError("AI 大材料压缩限制说明无效或包含未知引用；未保存报告。")
    if included_ids != allowed_ids or included_units != set(evidence_units):
        raise PipelineError("AI 大材料压缩遗漏来源或阅读片段；该批未完成，已保留成功进度。")
    return copy.deepcopy(result)


def validate_review(result, note, evidence_units):
    identities = {unit["citation_id"] for unit in evidence_units.values()}
    indexes = set(range(len(note["findings"])))
    if (not isinstance(result, dict) or set(result) != {"accepted", "issues", "reviewed_finding_indexes", "reviewed_unit_ids"}
            or not isinstance(result["accepted"], bool) or not isinstance(result["issues"], list)
            or len(result["issues"]) > 20 or result["accepted"] != (not result["issues"])
            or not isinstance(result["reviewed_finding_indexes"], list)
            or any(isinstance(index, bool) or not isinstance(index, int) for index in result["reviewed_finding_indexes"])
            or len(result["reviewed_finding_indexes"]) != len(indexes) or set(result["reviewed_finding_indexes"]) != indexes
            or not isinstance(result["reviewed_unit_ids"], list)
            or any(not isinstance(identity, str) for identity in result["reviewed_unit_ids"])
            or len(result["reviewed_unit_ids"]) != len(evidence_units) or set(result["reviewed_unit_ids"]) != set(evidence_units)):
        raise PipelineError("AI 压缩证据复核格式或逐片覆盖无效；压缩草稿和成功进度已保留。")
    for issue in result["issues"]:
        if not _valid_review_issue(issue, identities, indexes, evidence_units):
            raise PipelineError("AI 压缩证据复核问题字段无效；压缩草稿和成功进度已保留。")
    return copy.deepcopy(result)
