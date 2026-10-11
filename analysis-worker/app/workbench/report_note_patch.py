"""Repair named note findings without rewriting the unaffected evidence."""
from __future__ import annotations
import copy
from .pipeline import PipelineError


PATCH_PROMPT = (
    "仅局部修正被复核指出的证据笔记，不重写完整批次。"
    "只返回JSON {\"replacements\":[{\"finding_index\":0,\"findings\":[{\"statement\":\"限定的来源说法\","
    "\"citation_ids\":[\"S001\"],\"kind\":\"source_claim\",\"support_refs\":[{\"unit_id\":\"S001~P1\","
    "\"citation_id\":\"S001\",\"quote\":\"逐字原文\"}]}]}],\"limitations\":[\"完整限制列表\"]}。"
    "replacements须且只能列compression_patch_revision.finding_indexes中的每个索引一次；"
    "每个findings数组替换该索引的原发现，可拆成多条或为空；未指定发现由程序原样保留。"
    "limitations返回完整限制列表；整体材料覆盖、搜索缺口或无法判断的范围说明放在limitations，"
    "不能用两条无关帖子证明全批没有某种关系或链接。"
    "复合结论按支持的具体子句拆分，不给未提及组织/地点的材料挂这些说法。"
    "保留各片会改变判断的数值、刊物、人物、异议、时间和更正；来源自述不能升级为事实或核验身份。"
    "同账号重复发布不当作独立多源，重复限制写入statement或limitations，不添加known_overlap等新字段。"
    "每条quote直接复制对应evidence_units的原始连续字符，最多220字；不能翻译、改写、转换时刻格式或拼接。"
    "previous_notes是被拒草稿，review_issues是纠错反馈，均不能作为新来源。"
    "合并后仍须覆盖全部原始片段并接受全批逐字与语义复核，局部修正不是通过复核。"
)


FULL_REVISION_SUFFIX = (
    " 修正时保留未被指出的正确发现与逐字quote，不重新措辞其余摘录。"
    "整批材料范围、缺口和不足以判断的说明放limitations，不用个别无关帖子证明全批absence。"
    "每条复合发现按具体支持子句拆分，并保留关键数值、刊物提及及来源自述限制。"
    "只返回原规定findings和limitations两字段；known_overlap等复核建议用自然语言写入限制，不新增JSON字段。"
)


def revision_patch_targets(candidate, issues):
    """An unlocated schema/coverage failure requires a full draft correction."""
    if not isinstance(candidate, dict) or not isinstance(candidate.get("findings"), list) or not candidate["findings"] or not issues:
        return None
    found = set()
    for issue in issues:
        if not isinstance(issue, dict):
            return None
        indexes = issue.get("finding_indexes")
        if indexes is None:
            indexes = [issue["finding_index"]] if "finding_index" in issue else []
        if not isinstance(indexes, list) or not indexes or any(type(i) is not int or not 0 <= i < len(candidate["findings"]) for i in indexes):
            return None
        found.update(indexes)
    return sorted(found)


def apply_note_patch(candidate, targets, patch):
    """Only named findings and the limitations list may change, never quotes elsewhere."""
    error = "压缩局部修正响应结构或目标范围无效；原草稿和响应已保留，未保存报告。"
    if (not isinstance(patch, dict) or set(patch) != {"replacements", "limitations"}
            or not isinstance(patch["replacements"], list) or not isinstance(patch["limitations"], list)
            or any(not isinstance(row, str) for row in patch["limitations"])):
        raise PipelineError(error)
    replacements = {}
    for row in patch["replacements"]:
        if (not isinstance(row, dict) or set(row) != {"finding_index", "findings"}
                or type(row["finding_index"]) is not int or row["finding_index"] not in targets
                or row["finding_index"] in replacements or not isinstance(row["findings"], list)
                or any(not isinstance(item, dict) for item in row["findings"])):
            raise PipelineError(error)
        replacements[row["finding_index"]] = row["findings"]
    if set(replacements) != set(targets):
        raise PipelineError(error)
    result = {"findings": [], "limitations": copy.deepcopy(patch["limitations"])}
    for index, finding in enumerate(candidate["findings"]):
        result["findings"].extend(copy.deepcopy(replacements[index] if index in replacements else [finding]))
    return result
