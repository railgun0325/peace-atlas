"""Source coverage, resumable reading and bounded report provenance checks.

Exact quote/citation checks do not establish truth or validate an inference.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import inspect
import json
import re
import time
import unicodedata

from .pipeline import PipelineError
from .query_plan import configured_ai_json


READ_BATCH_SOURCES = 12
READ_BATCH_CHARACTERS = 40000
READ_FRAGMENT_CHARACTERS = 12000
QUOTE_REPAIR_LIMIT = 12
_REVIEW_FIELDS = {"citation_id", "quote", "reading", "limitations"}
_ID_BRACKET = re.compile(r"\[(S\d{3,}(?:\s*[,，;；、]\s*S\d{3,})*)\]")
_ANY_ID = re.compile(r"(?<![A-Za-z0-9])S\d+(?![A-Za-z0-9])")
_ID_RANGE = re.compile(r"(S\d{3,})\s*(?:-|–|—|~|～|〜|至|到)\s*(S\d{3,})")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _normalized_text(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def _evidence_fields(item):
    metadata = item.get("metadata") or {}
    result = {field: copy.deepcopy(item.get(field, metadata.get(field))) for field in
              ("source_key", "target_key", "relation_type", "observed_at", "research_report_source_role")
              if item.get(field, metadata.get(field)) is not None}
    kind = item.get("evidence_kind") or item.get("kind") or metadata.get("evidence_kind") or metadata.get("kind")
    if not kind and metadata.get("research_report_source_role") in {"visible_follow_list", "public_profile", "visible_profile", "profile"}:
        kind = metadata["research_report_source_role"]
    if kind:
        result["evidence_kind"] = str(kind)
    return result


def source_dependencies(items):
    """Flag evidenced overlap; publisher names/distinct hashes do not prove independence."""
    identities = [row["citation_id"] for row in items]
    parents = list(range(len(items)))
    reasons = {identity: set() for identity in identities}
    seen = {}

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, item in enumerate(items):
        metadata = item.get("metadata") or {}
        body = _normalized_text(item.get("content"))
        typed = _evidence_fields(item)
        observable = typed.get("evidence_kind") == "visible_follow_list" or bool(typed.get("relation_type") and typed.get("source_key") and typed.get("target_key"))
        # Feed/search URLs are acquisition routes, never an original-document
        # dependency. Only saved document URLs and explicit reprint origins count.
        keys = [("saved_text", body)] if body and not observable else []
        if observable:
            # A shared following page is one acquisition surface with multiple
            # directed observations, not a shared document supporting one fact.
            fact = tuple(typed.get(field) for field in ("source_key", "target_key", "relation_type", "observed_at"))
            keys = [("observed_relationship", fact)] if all(fact) else []
        if item.get("url") and not observable:
            keys.append(("document", str(item["url"]).strip().rstrip("/")))
        for field in ("body_source_url", "original_url", "original_source_url", "reprint_of_url", "syndication_source_url"):
            if metadata.get(field) and not observable:
                keys.append(("document", str(metadata[field]).strip().rstrip("/")))
        for field in ("wire_story_id", "original_document_id"):
            if metadata.get(field) and not observable:
                keys.append((field, str(metadata[field])))
        for key in keys:
            if key in seen:
                other = seen[key]
                if other == index:
                    continue
                parents[root(index)] = root(other)
                label = "identical_observed_relationship" if key[0] == "observed_relationship" else "identical_saved_text" if key[0] == "saved_text" else "shared_document_or_explicit_reprint"
                reasons[identities[index]].add(label)
                reasons[identities[other]].add(label)
            else:
                seen[key] = index
    groups = {}
    for index, identity in enumerate(identities):
        groups.setdefault(root(index), []).append(identity)
    result = {}
    for members in groups.values():
        group_id = "D-" + _digest(sorted(members))[:16]
        all_reasons = sorted(set().union(*(reasons[identity] for identity in members)))
        for identity in members:
            result[identity] = {"support_group_id": group_id, "overlapping_citation_ids": members,
                                "overlap_count": len(members),
                                "overlap_basis": all_reasons,
                                "independence": "known_overlap" if len(members) > 1 else "not_established"}
    return result


def source_samples(items):
    sources, coverage = [], []
    dependencies = source_dependencies(items)
    bodies = [str(item.get("content") or "") for item in items]
    emitted_groups = set()
    for index, item in enumerate(items):
        source = {key: copy.deepcopy(item[key]) for key in (
            "citation_id", "title", "source_name", "source_type", "type", "published_at", "publication_day",
            "account_status", "account_key", "topics", "entities", "locations") if key in item}
        metadata = item.get("metadata") or {}
        source["metadata"] = {key: copy.deepcopy(value) for key, value in metadata.items() if key in {
            "content_kind", "collection_method", "collected_at", "content_group_hash", "body_completeness",
            "body_captured_at", "body_capture_method", "publication_date_precision", "body_published_at",
            "body_publication_day", "body_publication_precision", "body_publication_differs_from_feed",
            "kind", "evidence_kind", "source_key", "target_key", "relation_type", "observed_at", "research_report_source_role", "evidence_id"}}
        source.update(_evidence_fields(item))
        body = bodies[index]
        fragments = [{"start": start, "end": min(start + READ_FRAGMENT_CHARACTERS, len(body)),
                      "text": body[start:start + READ_FRAGMENT_CHARACTERS]}
                     for start in range(0, len(body), READ_FRAGMENT_CHARACTERS)] or [{"start": 0, "end": 0, "text": ""}]
        source["content_fragments"] = fragments
        source["content_characters"] = len(body)
        source["complete_text_sent"] = True
        dependency = dependencies[item["citation_id"]]
        source["source_dependency"] = {key: copy.deepcopy(value) for key, value in dependency.items() if key != "overlapping_citation_ids"}
        if dependency["support_group_id"] not in emitted_groups:
            source["source_dependency"]["overlapping_citation_ids"] = list(dependency["overlapping_citation_ids"])
            emitted_groups.add(dependency["support_group_id"])
        source["saved_text_coverage_basis"] = "all_contiguous_fragments_planned_for_reading_not_yet_model_reviewed"
        source["evidence_boundary"] = {
            "material_kind": metadata.get("content_kind", "unspecified"),
            "original_document_completeness": metadata.get("body_completeness", "not_established"),
            "publication_date_is_event_date": False,
            "account_verification_is_claim_verification": False,
            "rule_labels_are_source_facts": False,
            "observed_follow_is_affiliation_or_endorsement": False,
            "observable_relation_time_scope": source.get("observed_at"),
            "profile_self_description_is_independent_verification": False}
        sources.append(source)
        coverage.append({"citation_id": item["citation_id"], "content_kind": metadata.get("content_kind", "unspecified"),
            "available_characters": len(body), "sent_characters": sum(len(part["text"]) for part in fragments),
            "complete_text_sent": source["complete_text_sent"],
            "sent_ranges": [{"start": part["start"], "end": part["end"]} for part in fragments if part["text"]],
            "omitted_ranges": [], "fragment_count": len(fragments),
            "coverage_basis": "all_saved_text_planned_for_fragment_reading",
            "original_document_completeness": source["evidence_boundary"]["original_document_completeness"],
            "support_group_id": source["source_dependency"]["support_group_id"]})
    return sources, coverage


def report_context(sources, coverage, research_context=None):
    """Deterministic synthesis context; research data is supplied by the caller."""
    groups = {}
    for source in sources:
        dependency = source.get("source_dependency") or {}
        if len(dependency.get("overlapping_citation_ids", [])) > 1:
            groups[dependency["support_group_id"]] = copy.deepcopy(dependency)
    return {
        "research": copy.deepcopy(research_context or {}),
        "source_record_count": len(sources),
        "publisher_name_count": len({row.get("source_name") for row in sources if row.get("source_name")}),
        "overlapping_support_groups": list(groups.values()),
        "independent_source_count": None,
        "input_coverage": copy.deepcopy(coverage),
        "required_distinctions": ["可直接核对的输入信息", "来源主张", "模型推断与前提", "不足以判断"],
        "analysis_questions": [
            "先回答用户研究目标；无法回答的部分写明缺口，不以身份候选替代研究结论。",
            "比较各主体诉求、证据与行动机制，检视可支持的竞争解释、反证和分歧。",
            "没有相反立场的材料时说明覆盖不足，不补造对方观点；缺少报道不等于没有事件。",
            "同正文、同原文或明确转载按共享支持处理；不同媒体名、不同指纹不证明独立核验。",
            "发布日期、采集日期、正文所述活动日期和计划日期分别表达。",
            "发送全部保存文本不等于获得完整原文；未发送区间不算已阅读。"]}


def synthesis_sources(sources):
    """Keep every body/date/citation; dependency membership is sent once in context."""
    fields = {"citation_id", "title", "source_name", "source_type", "type", "published_at", "publication_day",
              "account_status", "account_key", "content_fragments", "content_characters", "complete_text_sent", "evidence_boundary",
              "evidence_kind", "source_key", "target_key", "relation_type", "observed_at", "research_report_source_role"}
    metadata_fields = {"content_kind", "collected_at", "body_completeness", "publication_date_precision",
                       "body_published_at", "body_publication_day", "body_publication_precision",
                       "body_publication_differs_from_feed"}
    result = []
    for row in sources:
        projected = {key: copy.deepcopy(value) for key, value in row.items() if key in fields}
        projected["metadata"] = {key: copy.deepcopy(value) for key, value in (row.get("metadata") or {}).items() if key in metadata_fields}
        dependency = row.get("source_dependency") or {}
        projected["source_dependency"] = {key: copy.deepcopy(dependency[key]) for key in
            ("support_group_id", "overlap_basis", "independence") if key in dependency}
        result.append(projected)
    return result


def _prose_blocks(markdown):
    for paragraph in re.split(r"\n\s*\n", markdown):
        lines = [line for line in paragraph.splitlines()
                 if line.strip() and not re.match(r"^\s*(?:#{1,6}\s|[-*_]{3,}\s*$|\|?\s*[-:]+\s*\|)", line)
                 and not re.fullmatch(r"\s*[一二三四五六七八九十]+、[^。！？；;\n]{1,120}\s*", line)]
        if lines:
            yield "\n".join(lines).strip()


def _date_terminology_only(text):
    """Recognize exact date/metadata/identity method sentences, not assertions."""
    compact = re.sub(r"\s+", "", text)
    method_sentences = {
        "以下区分发布日期、文本所述活动日期、观察日期和计划日期。",
        "以下区分发布日、文本所述活动日、观察日和计划日。",
        "发布日优先使用publication_day的JST日期，不把活动日充作发布日，也不直接截取published_at的UTC日期。",
        "工作台自动关键词标签只是研究上下文，本报告未将其作为活动地点或事实；规则误匹配不能表述为原始报道矛盾。",
        "不从个人账号关注或提及推断政治归属；普通个人政治身份不在本报告推断范围内。",
    }
    sentences = re.findall(r"[^。]+。", compact)
    if sentences and "".join(sentences) == compact and all(sentence in method_sentences for sentence in sentences):
        return True
    value = compact.removesuffix("。").removeprefix("以下")
    value = value.removesuffix("，均不预测是否实际举行")
    definitions = {"发布日期": {"帖子发布时间", "材料发布时间", "来源发布时间"},
                   "活动时间": {"帖子所述已发生或正在发生的行动时间", "正文所述活动时间"},
                   "计划时间": {"帖子号召的未来安排", "正文所述未来安排"}}
    parts = re.split(r"[；;]", value)
    for part in parts:
        match = re.fullmatch(r'[“"](发布日期|活动时间|计划时间)[”"](?:指|表示)(.+)', part)
        if not match or match[2] not in definitions[match[1]]:
            return False
    return bool(parts)


def _normalize_citations(markdown, allowed):
    """Normalize explicit citation lists/ranges and labeled source indexes.

    A known naked ID in an assertion is not automatically a citation. Bare
    ranges are accepted only as a clearly labeled source/index description.
    Every endpoint and every expanded member must exist exactly as supplied.
    """
    issues, changes = [], []

    def expand_range(match):
        first, last = match[1], match[2]
        start, end = int(first[1:]), int(last[1:])
        ids = [f"S{index:03}" for index in range(start, end + 1)] if 0 < start <= end and end-start < len(allowed) else []
        if first not in allowed or last not in allowed or not ids or any(identity not in allowed for identity in ids):
            issues.append({"code": "invalid_citation_range", "blocking": True,
                           "range": match[0], "citation_ids": [first, last]})
            return match[0]
        return ", ".join(ids)

    def bracket(match):
        inner = match[1]
        atom = r"S\d{3,}(?:\s*(?:-|–|—|~|～|〜|至|到)\s*S\d{3,})?"
        grammar = atom + r"(?:\s*[,，;；、]\s*" + atom + r")*"
        if not re.fullmatch(grammar, inner.strip()):
            return match[0]
        expanded = _ID_RANGE.sub(expand_range, inner.strip())
        identities = re.findall(r"S\d{3,}", expanded)
        if any(identity not in allowed for identity in identities):
            issues.append({"code": "unknown_citation", "blocking": True,
                           "citation_ids": sorted(set(identities) - allowed)})
        normalized = "[" + ", ".join(dict.fromkeys(identities)) + "]"
        if normalized != match[0]:
            changes.append({"original": match[0], "normalized": normalized})
        return normalized

    value = re.sub(r"\[([^\]\n]+)\]", bracket, markdown)
    # Bare labeled source indexes are deliberately narrower than arbitrary
    # naked citation IDs. A source ID embedded in a factual assertion stays
    # unresolved and is rejected by the audit below.
    expression = r"S\d{3,}(?:\s*(?:[,，;；、]|-|–|—|~|～|〜|至|到)\s*S\d{3,})*"
    labeled = re.compile(r"((?:资料|材料|来源|证据|索引|样本|同支持组重叠的|sources?|citations?|IDs?)\s*)" + r"(" + expression + r")", re.I)
    def source_index(match):
        normalized = bracket(re.match(r"\[([^\]\n]+)\]", "[" + match[2] + "]"))
        changes.append({"original": match[2], "normalized": normalized, "basis": "labeled_source_index"})
        return match[1] + normalized
    value = labeled.sub(source_index, value)
    # Saved reports also describe a source catalog with a following predicate,
    # e.g. "S001 至 S080 主要是已保存公开帖文". Only material-type
    # predicates qualify; a naked ID next to an event/person claim still fails.
    catalog = re.compile(r"(?<![A-Za-z0-9])(" + expression + r")(?=\s*(?:主要)?(?:是|为)\s*"
                         r"(?:(?:已保存|保存)?(?:公开|可见)?(?:帖文|帖子|简介|关系列表|关注列表|材料|来源|证据)"
                         r"|(?:saved_public_material|public_profile|visible_follow_list)(?![A-Za-z0-9_])))")
    def catalog_index(match):
        normalized = bracket(re.match(r"\[([^\]\n]+)\]", "[" + match[1] + "]"))
        changes.append({"original": match[1], "normalized": normalized, "basis": "source_catalog_type_predicate"})
        return normalized
    value = catalog.sub(catalog_index, value)
    # A source-name mention in a sentence already carrying the same explicit
    # citation is presentation duplication, not an uncited assertion. Never
    # borrow a citation from another sentence or resolve an unknown naked ID.
    mention = re.compile(_ID_BRACKET.pattern + "|" + _ANY_ID.pattern)
    def cited_sentence(match):
        sentence = match[0]
        cited = {identity for citation in _ID_BRACKET.finditer(sentence)
                 for identity in re.findall(r"S\d{3,}", citation[1])} & allowed
        def source_mention(item):
            identity = item[0]
            if identity.startswith("[") or identity not in cited:
                return identity
            normalized = "[" + identity + "]"
            changes.append({"original": identity, "normalized": normalized,
                            "basis": "same_sentence_explicit_citation"})
            return normalized
        return mention.sub(source_mention, sentence)
    value = re.sub(r"[^。！？；;\n]+", cited_sentence, value)
    return value, issues, changes


def report_audit(markdown, items):
    """Audit explicit citation/dependence boundaries, not semantic truth."""
    if not isinstance(markdown, str):
        return {"method": "citation_and_known_overlap_checks", "issues": [{"code": "non_text", "blocking": True}]}
    allowed = {row["citation_id"] for row in items}
    dependencies = source_dependencies(items)
    issues = []
    # A following-page URL can support several distinct saved observations.
    # Current rendered labels identify the exact record; the destination must
    # agree with it. A historical URL-only label is resolvable only if unique.
    by_id = {row["citation_id"]: row for row in items}
    by_url = {}
    for row in items:
        by_url.setdefault(row.get("url"), set()).add(row["citation_id"])
    link_issues = []
    inline = re.compile(r"\[((?:\\.|[^\]\\\n])+)\]\(([^)\n]*)\)")
    def saved_citation(match):
        target = html.unescape(match[2].strip().strip("<>"))
        label = html.unescape(match[1])
        explicit = re.match(r"^(S\d{3,})(?=\s|[·:：]|$)", label)
        if explicit:
            identity = explicit[1]
            if identity in by_id and by_id[identity].get("url") != target:
                link_issues.append({"code": "citation_destination_mismatch", "blocking": True,
                    "citation_ids": [identity]})
            return f"[{identity}]"
        candidates = by_url.get(target, set())
        if len(candidates) == 1:
            return f"[{next(iter(candidates))}]"
        if len(candidates) > 1:
            link_issues.append({"code": "ambiguous_saved_citation", "blocking": True,
                "citation_ids": sorted(candidates)})
        return match[0]
    raw, issues, normalizations = _normalize_citations(inline.sub(saved_citation, markdown), allowed)
    issues.extend(link_issues)
    references = set()
    for match in _ID_BRACKET.finditer(raw):
        references.update(re.findall(r"S\d{3,}", match[1]))
    unknown = references - allowed
    if unknown:
        issues.append({"code": "unknown_citation", "blocking": True, "citation_ids": sorted(unknown)})
    leftovers = _ANY_ID.findall(_ID_BRACKET.sub("", raw))
    if leftovers:
        issues.append({"code": "unresolved_citation_format", "blocking": True, "citation_ids": sorted(set(leftovers))})
    factual = re.compile(r"举行|发布|宣布|达成|通过|决定|废止|设立|取消|参与|支持率|社会支持|趋势|报道|来源称|\d+\s*(?:人|名|票|%)")
    boundary = re.compile(r"^(?:[-*]\s*)?(?:研究范围|报告范围|本报告依据|本报告覆盖|发布日期范围|资料不足|材料不足|不足以判断|证据缺口|后续核验|待核验|建议核验|无法判断|不能判断|(?:目前|现有|本次|当前)?(?:材料|来源|证据|资料|信息|样本)(?:仍|尚)?(?:不足|缺少|不够|有限|不完整|未覆盖))")
    independent = re.compile(r"独立(?:地|的)?(?:交叉)?(?:核验|核实|证实|印证|确认|信源|来源)|独立交叉(?:验证|支持)")
    negative = re.compile(r"不能|并非|不代表|不足以|尚未|未完成|未能|不是|不可|不构成|无法|不得|未(?:被)?独立")
    for index, paragraph in enumerate(_prose_blocks(raw), 1):
        cited = {identity for match in _ID_BRACKET.finditer(paragraph) for identity in re.findall(r"S\d{3,}", match[1])}
        text = _ID_BRACKET.sub("", paragraph).strip()
        if not cited and factual.search(text) and len(text) >= 8 and not boundary.search(text) and not _date_terminology_only(text):
            issues.append({"code": "uncited_assertion_paragraph", "blocking": True,
                           "paragraph": index, "excerpt": text[:180]})
        for clause in re.split(r"[。！？；;\n]|[，,](?![^\[]*\])", paragraph):
            # An unrelated insufficiency elsewhere in a paragraph must not
            # exempt an affirmative independence claim in this clause.
            plain_clause = _ID_BRACKET.sub("", clause)
            if independent.search(plain_clause) and not negative.search(plain_clause):
                clause_ids = {identity for match in _ID_BRACKET.finditer(clause)
                              for identity in re.findall(r"S\d{3,}", match[1])} or cited
                known = {dependencies[identity]["support_group_id"] for identity in clause_ids if identity in dependencies}
                if len(clause_ids) > 1 and len(known) == 1:
                    issues.append({"code": "known_overlap_claimed_independent", "blocking": True,
                                   "paragraph": index, "citation_ids": sorted(clause_ids), "excerpt": plain_clause[:180]})
        if re.search(r"可能|或许|意味着|推断|解释为|表明.*趋势", text) and not re.search(r"模型(?:推断|判断|分析)|分析判断|假设|竞争解释|来源(?:称|认为|分析)", text):
            issues.append({"code": "inference_label_review", "blocking": False,
                           "paragraph": index, "excerpt": text[:180]})
    return {"method": "citation_and_known_overlap_checks", "issues": issues,
            "referenced_citation_ids": sorted(references), "input_citation_count": len(items),
            "citation_normalizations": normalizations,
            "semantic_support_validated": False}


def render_citations(markdown, items):
    if not isinstance(markdown, str) or re.search(r"https?://", markdown):
        raise PipelineError("AI 只能引用提供的证据 ID，不能生成来源地址；未保存报告。")
    audit = report_audit(markdown, items)
    blocking = [row for row in audit["issues"] if row["blocking"]]
    if blocking:
        messages = {"unknown_citation": "AI 引用了输入范围外的证据 ID",
                    "unresolved_citation_format": "AI 证据 ID 引用格式无效或未解析",
                    "invalid_citation_range": "AI 证据范围含未知、缺失或逆序 ID",
                    "uncited_assertion_paragraph": "AI 实质判断段落缺少证据 ID 引用",
                    "known_overlap_claimed_independent": "AI 将相同正文或同原文转载表述为独立核验"}
        raise PipelineError(messages.get(blocking[0]["code"], "AI 报告引用核对失败") + "；未保存报告。")
    allowed = {row["citation_id"]: row for row in items}
    from .pipeline import _md, _validate_ai_citations

    def citation(match):
        identities = re.findall(r"S\d{3,}", match[1])
        return " ".join(f'[{identity} · {_md(allowed[identity]["source_name"])}]({allowed[identity]["url"]})' for identity in identities)

    normalized, _, _ = _normalize_citations(markdown, set(allowed))
    rendered = _ID_BRACKET.sub(citation, normalized)
    _validate_ai_citations(rendered, items)
    return rendered


def validate_source_reviews(result, sources):
    if not isinstance(result, dict) or set(result) != {"source_reviews"} or not isinstance(result["source_reviews"], list):
        raise PipelineError("AI 来源阅读没有返回完整核对格式；未生成报告。")
    supplied = {row["citation_id"]: row for row in sources}
    accepted = {}
    for row in result["source_reviews"]:
        if not isinstance(row, dict) or set(row) != _REVIEW_FIELDS:
            raise PipelineError("AI 来源阅读字段无效；未生成报告。")
        identity = row["citation_id"]
        if not isinstance(identity, str) or identity not in supplied or identity in accepted:
            raise PipelineError("AI 来源阅读引用了未知或重复证据；未生成报告。")
        if any(not isinstance(row[key], str) or not 1 <= len(row[key].strip()) <= size for key, size in
               (("quote", 220), ("reading", 400), ("limitations", 400))):
            raise PipelineError("AI 来源阅读摘录或说明无效；未生成报告。")
        source = supplied[identity]
        fragments = [part for part in source["content_fragments"] if part["text"].strip()]
        headline = (source.get("metadata") or {}).get("content_kind") == "headline_only"
        texts = [source["title"]] if headline or not fragments else [part["text"] for part in fragments]
        meaningful_length = min(8, max(len(text.strip()) for text in texts))
        if len(row["quote"].strip()) < meaningful_length or not any(row["quote"] in text for text in texts):
            raise PipelineError("AI 来源摘录不属于提供的正文片段；有正文时不能只摘标题，未生成报告。")
        if any(re.search(r"https?://|<[^>]+>|```", row[key]) for key in ("reading", "limitations")):
            raise PipelineError("AI 来源说明包含额外地址或标记；未生成报告。")
        accepted[identity] = copy.deepcopy(row)
        accepted[identity]["quote_basis"] = "headline" if headline or not fragments else "sent_body_fragment"
        accepted[identity]["quote_verification_scope"] = "exact_quote_match_only"
    if set(accepted) != set(supplied):
        raise PipelineError("AI 来源阅读遗漏了输入材料；未生成报告。")
    return [accepted[row["citation_id"]] for row in sources]


_READ_INSTRUCTION = (
    '逐条阅读公开材料，输出 {"source_reviews":[{"citation_id":"输入ID","quote":"正文片段中的原样短摘录",'
    '"reading":"该材料明确说了什么，来源说法不得升级为独立事实","limitations":"缺少什么、只能支持到什么范围"}]}。'
    '必须对每条输入返回且只返回一条，不能遗漏或补造ID。quote不得拼接不同片段、改写、加省略号或翻译。'
    '有非空正文片段时quote必须取正文，不能只摘标题；仅headline_only或空正文可以取标题，并明确只有标题。'
    'quote应选择与研究判断有关的陈述，尤其更正、反证、计划/已发生差别；不能凭一条摘录声称核验了所有细节。'
    'rss_summary/headline_only是检索摘要/标题，不是新闻全文；不能补入输入之外的人数、时间、地点、引文、组织或案文。'
    '先围绕research_context.research_goal阅读。每条可能只是该来源的连续片段；不得声称已经读完其他尚未输入片段。'
    '区分已发生、计划、主张和推断；发布日期不能当作活动日期。公开观察关系要按evidence_kind、source_key、target_key、relation_type与observed_at表达。'
    'visible_follow_list且明确follows是所记观察日公开页面上的有方向关注，不需第三方组织官网才构成此观察；公开关注不代表隶属、支持或政治归属。'
    'profile简介属于本人公开自述，不能自动升级为正式隶属；账号核验只核验账号身份，不核验每项说法。'
    '不同账号句柄分别处理，旧句柄无法登录不代表当前另一句柄不可用，不能跨账号或跨观察日概括。'
    '按照source_dependency说明已有同文/转载依赖，不能推定独立交叉核验；不同出版方也不自动代表独立信息链。'
    'topics/entities/locations是规则标签，不作为事实。缺少不同立场或反证时写明缺口，不补造对方观点。不要输出URL。')


def _quote_support_rejections(result, sources):
    """Classify only a complete, structurally valid response with unsupported quotes.

    The ordinary exact validator stays authoritative. Diagnostics retain only
    supplied IDs and a hash/length of the rejected quote, never rejected text.
    """
    if (not isinstance(result, dict) or set(result) != {"source_reviews"}
            or not isinstance(result["source_reviews"], list)):
        return None
    supplied = {row["citation_id"]: row for row in sources}
    rows = result["source_reviews"]
    if len(rows) != len(supplied):
        return None
    seen, rejections = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _REVIEW_FIELDS:
            return None
        identity = row["citation_id"]
        if not isinstance(identity, str) or identity not in supplied or identity in seen:
            return None
        seen.add(identity)
        if any(not isinstance(row[key], str) or not 1 <= len(row[key].strip()) <= size
               for key, size in (("quote", 220), ("reading", 400), ("limitations", 400))):
            return None
        if any(re.search(r"https?://|<[^>]+>|```", row[key]) for key in ("reading", "limitations")):
            return None
        source = supplied[identity]
        fragments = [part for part in source["content_fragments"] if part["text"].strip()]
        headline = (source.get("metadata") or {}).get("content_kind") == "headline_only"
        texts = [source["title"]] if headline or not fragments else [part["text"] for part in fragments]
        meaningful_length = min(8, max(len(text.strip()) for text in texts))
        quote = row["quote"]
        if len(quote.strip()) < meaningful_length or not any(quote in text for text in texts):
            reason = ("too_short" if len(quote.strip()) < meaningful_length else
                      "title_only_with_body" if not headline and fragments and quote in source["title"] else
                      "not_exact_body_excerpt" if not headline and fragments else "not_exact_headline_excerpt")
            rejections.append({"citation_id": identity, "reason": reason, "quote_length": len(quote),
                               "quote_sha256": hashlib.sha256(quote.encode("utf-8", errors="surrogatepass")).hexdigest(),
                               "quote_hash_is_source_proof": False})
    return rejections if seen == set(supplied) and rejections else None


def _reading_context(research_context):
    context = research_context or {}
    params = context.get("research_params") or context.get("params") or context.get("parameters") or {}
    return {"research_goal": context.get("research_goal") or "",
            "date_scope": {key: copy.deepcopy(context.get(key, params.get(key))) for key in ("start_date", "end_date")
                           if context.get(key, params.get(key)) is not None},
            "time_rules": "发布日期、活动日、采集日和关系观察日分开；日本日期按Asia/Tokyo(JST)表达，UTC偏移时间先换算JST再写观察日。关系只覆盖所记observed_at，不跨账号句柄或时间推断。"}


def _reading_units(sources):
    units, by_source, complete = {}, {}, {}
    for source in sources:
        identity = source["citation_id"]
        if identity in by_source:
            raise PipelineError("来源阅读出现重复材料ID。")
        pieces = []
        for part in source.get("content_fragments") or []:
            start, end, text = part.get("start"), part.get("end"), part.get("text")
            if not isinstance(start, int) or not isinstance(end, int) or not isinstance(text, str) or start < 0 or end-start != len(text):
                raise PipelineError("来源连续分片范围与文本不一致。")
            pieces.extend({"start": start+offset, "end": start+min(offset+READ_FRAGMENT_CHARACTERS,len(text)),
                           "text": text[offset:offset+READ_FRAGMENT_CHARACTERS]}
                          for offset in range(0,len(text),READ_FRAGMENT_CHARACTERS))
        if not pieces:
            pieces = [{"start": 0, "end": 0, "text": ""}]
        total, cursor = source.get("content_characters", sum(len(part["text"]) for part in pieces)), 0
        contiguous = True
        for part in pieces:
            contiguous = contiguous and part["start"] == cursor
            cursor = part["end"]
        complete[identity] = contiguous and cursor == total and source.get("complete_text_sent", True)
        by_source[identity] = []
        for index, part in enumerate(pieces):
            unit_id = identity if len(pieces)==1 else f"{identity}~P{index+1:06}"
            unit = {key: copy.deepcopy(value) for key,value in source.items() if key not in {"content_fragments", "source_dependency"}}
            unit.update(citation_id=unit_id, original_citation_id=identity, content_fragments=[part],
                        reading_unit={"unit_id":unit_id,"start":part["start"],"end":part["end"],
                                      "fragment_index":index+1,"fragment_count":len(pieces)})
            unit["source_dependency"] = {key:copy.deepcopy(value) for key,value in (source.get("source_dependency") or {}).items()
                                         if key != "overlapping_citation_ids"}
            units[unit_id] = unit
            by_source[identity].append(unit_id)
    return units, by_source, complete


async def read_sources(sources, *, previous_review=None, checkpoint=None, time_budget_seconds=None, return_state=False,
                       research_context=None):
    """Read every saved-text fragment, preserving bounded quotes and checkpoints.

    Unit IDs are local to reading; aggregate reviews retain original citation
    IDs for synthesis. Exact quotes establish no general semantic truth.
    """
    context = _reading_context(research_context)
    context_hash = _digest(context)
    source_hashes = {row["citation_id"]:_digest(row) for row in sources}
    units, by_source, complete_source = _reading_units(sources)
    fingerprints = {identity:_digest(row) for identity,row in units.items()}
    accepted = {}
    previous = previous_review if isinstance(previous_review,dict) else {}
    saved_hashes = previous.get("reviewed_fragment_fingerprints") or {}
    if previous.get("version")==3 and previous.get("research_context_fingerprint")==context_hash:
        for row in previous.get("fragment_reviews") or []:
            if not isinstance(row,dict):continue
            identity = row.get("unit_id")
            if identity not in units or saved_hashes.get(identity)!=fingerprints[identity]:continue
            raw = {key:row.get(key) for key in _REVIEW_FIELDS}
            raw["citation_id"] = identity
            try:
                accepted[identity] = validate_source_reviews({"source_reviews":[raw]},[units[identity]])[0]
            except PipelineError:continue
    resumed_sources = sum(all(uid in accepted for uid in ids) and complete_source[identity] for identity,ids in by_source.items())
    prior_repairs = previous.get("quote_repairs_used", 0)
    if isinstance(prior_repairs, bool) or not isinstance(prior_repairs, int) or not 0 <= prior_repairs <= QUOTE_REPAIR_LIMIT:
        raise PipelineError("来源阅读纠正预算记录无效；未生成报告。")
    state = {"version":3,"method":"all_saved_text_fragment_exact_quote_check","source_reviews":[],"fragment_reviews":[],
             "batches":[],"complete":False,"status":"pending","error":None,"resumed_source_count":resumed_sources,
             "resumed_fragment_count":len(accepted),"research_context_fingerprint":context_hash,
             "research_context":context,"batch_source_limit":READ_BATCH_SOURCES,"batch_input_character_limit":READ_BATCH_CHARACTERS,
             "fragment_character_limit":READ_FRAGMENT_CHARACTERS,"quote_verification_scope":"exact_quote_match_only",
             "quote_repair_limit":QUOTE_REPAIR_LIMIT,"quote_repairs_used":prior_repairs}

    def fragment_review(identity):
        result = copy.deepcopy(accepted[identity])
        unit = units[identity]
        result.update(citation_id=unit["original_citation_id"],unit_id=identity,
                      start=unit["reading_unit"]["start"],end=unit["reading_unit"]["end"])
        return result

    def refresh():
        state["fragment_reviews"] = [fragment_review(identity) for identity in units if identity in accepted]
        aggregates = []
        for identity,ids in by_source.items():
            if not complete_source[identity] or not all(uid in accepted for uid in ids):continue
            fragments = [fragment_review(uid) for uid in ids]
            review = {key:copy.deepcopy(value) for key,value in fragments[0].items() if key not in {"unit_id","start","end"}}
            review.update(fragment_reviews=fragments,quotes=[row["quote"] for row in fragments],
                          reviewed_ranges=[{"start":row["start"],"end":row["end"]} for row in fragments],
                          complete_saved_text_reviewed=True,reviewed_fragment_count=len(fragments),
                          aggregation_scope="leading_fields_are_first_fragment; all_fragment_reviews_retained")
            aggregates.append(review)
        state["source_reviews"] = aggregates
        reviewed = {row["citation_id"] for row in aggregates}
        state["reviewed_source_fingerprints"] = {identity:source_hashes[identity] for identity in reviewed}
        state["reviewed_fragment_fingerprints"] = {identity:fingerprints[identity] for identity in accepted}
        state["pending_citation_ids"] = [identity for identity in by_source if identity not in reviewed]
        state["pending_fragment_ids"] = [identity for identity in units if identity not in accepted]
        state["coverage_progress"] = {"input_source_count":len(sources),"reviewed_source_count":len(reviewed),
                                      "pending_source_count":len(sources)-len(reviewed)}
        state["fragment_coverage_progress"] = {"input_fragment_count":len(units),"reviewed_fragment_count":len(accepted),
            "pending_fragment_count":len(units)-len(accepted),
            "reviewed_characters":sum(units[uid]["reading_unit"]["end"]-units[uid]["reading_unit"]["start"] for uid in accepted)}
        state["complete"] = not state["pending_citation_ids"]
        state["status"] = "completed" if state["complete"] else "partial" if accepted else "pending"

    async def save():
        refresh()
        if checkpoint is not None:
            result = checkpoint(copy.deepcopy(state))
            if inspect.isawaitable(result):await result

    async def fail(message,reason):
        state.update(error=message,stop_reason=reason)
        await save()
        if return_state:return state
        error = PipelineError(message)
        error.source_review_state = copy.deepcopy(state)
        raise error

    started = time.monotonic()
    batches,batch,size = [],[],0
    for identity,unit in units.items():
        if identity in accepted:continue
        chars = len(json.dumps(unit,ensure_ascii=False,separators=(",",":")))
        if batch and (len(batch)>=READ_BATCH_SOURCES or size+chars>READ_BATCH_CHARACTERS):
            batches.append(batch);batch,size=[],0
        batch.append(unit);size+=chars
    if batch:batches.append(batch)
    for batch in batches:
        audit = {"citation_ids":list(dict.fromkeys(row["original_citation_id"] for row in batch)),
                 "unit_ids":[row["citation_id"] for row in batch],"status":"running","attempts":[]}
        result = None
        remaining = None if time_budget_seconds is None else time_budget_seconds-(time.monotonic()-started)
        if remaining is not None and remaining<=0:
            return await fail("来源阅读达到本次时间预算，尚有材料或片段待审。","time_budget")
        try:
            async with asyncio.timeout(remaining):
                result = await configured_ai_json(_READ_INSTRUCTION,{"research_context":context,"sources":batch},
                    max_tokens=16384,timeout_limit=min(75,remaining) if remaining is not None else 75,
                    reasoning_effort="high",max_input_chars=None)
            try:
                reviews = validate_source_reviews(result,batch)
            except PipelineError:
                rejected = _quote_support_rejections(result,batch)
                if not rejected:
                    raise
                audit["attempts"].append({"status":"rejected","failure_kind":"quote_support",
                                           "rejections":rejected})
                if state["quote_repairs_used"] >= QUOTE_REPAIR_LIMIT:
                    audit["quote_repair_refused"] = "cumulative_limit"
                    raise
                # Reserve this bounded correction and persist the failed attempt
                # before another request; a cancel never rolls back good batches.
                state["quote_repairs_used"] += 1
                audit["status"] = "quote_correction_pending"
                state["batches"].append(audit)
                await save()
                remaining = None if time_budget_seconds is None else time_budget_seconds-(time.monotonic()-started)
                if remaining is not None and remaining <= 0:
                    raise TimeoutError()
                async with asyncio.timeout(remaining):
                    result = await configured_ai_json(
                        _READ_INSTRUCTION + ' 上次完整响应仅摘录支持校验失败。重新阅读本批原文，原样复制正文中连续短句；不得沿用未获支持的摘录。仍完整返回本批全部ID，其他字段也重新依据原文填写。',
                        {"research_context":context,"sources":batch,
                         "quote_correction":{"failure_kind":"quote_support","rejections":rejected}},
                        max_tokens=16384,timeout_limit=min(75,remaining) if remaining is not None else 75,
                        reasoning_effort="high",max_input_chars=None)
                try:
                    reviews = validate_source_reviews(result,batch)
                except PipelineError:
                    audit["attempts"].append({"status":"rejected","failure_kind":"quote_correction_rejected",
                                               "rejections":_quote_support_rejections(result,batch) or []})
                    raise
                audit["attempts"].append({"status":"accepted","method":"same_exact_validator"})
                audit["quote_corrected"] = True
        except (PipelineError,TimeoutError) as exc:
            audit["status"] = "failed"
            if not any(row is audit for row in state["batches"]):
                state["batches"].append(audit)
            return await fail(str(exc) if isinstance(exc,PipelineError) else "来源阅读达到本次时间预算，未完成片段待审。",
                              "source_read_error" if isinstance(exc,PipelineError) else "time_budget")
        accepted.update({row["citation_id"]:row for row in reviews})
        audit["status"] = "completed"
        if not any(row is audit for row in state["batches"]):
            state["batches"].append(audit)
        await save()
    await save()
    if not state["complete"]:
        return await fail("输入来源仍有未提供正文区间，不能标为完整来源阅读。","source_text_incomplete")
    return state if return_state else state["source_reviews"]
