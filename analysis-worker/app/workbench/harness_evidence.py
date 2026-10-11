"""Bounded, pure evidence tools for a Harness session bound to a report snapshot.

There is deliberately no repository, URL fetch, filesystem, SQL or shell adapter
here. All answers are projections of private copies of the supplied JSON data.
Exact source quotations establish provenance, not truth or account affiliation.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import deque
from urllib.parse import urlsplit


_TOOL_FAILURE_MESSAGES = {
    'invalid_tool_request': '工具名、参数或证据范围无效。',
    'public_source_unavailable': '此公开来源本次无法读取；请尝试其他公开出处，或说明访问限制与证据缺口。',
    'unsupported_public_source': '此来源格式暂不支持正文提取；请寻找同一出版方的 HTML 出处，或保留证据不足。',
    'public_network_disabled': '本任务没有开启公开网络；请使用已保存原文并说明取证范围。',
}


class EvidenceGatewayError(ValueError):
    """A contract failure with an optional fixed, host-defined diagnostic code."""
    def __init__(self, message, *, code='invalid_tool_request'):
        super().__init__(message)
        self.code = code if isinstance(code, str) and code in _TOOL_FAILURE_MESSAGES else 'invalid_tool_request'


def tool_failure_diagnostic(error):
    """Never return exception text, arbitrary codes, model arguments or external content."""
    code = getattr(error, 'code', 'invalid_tool_request')
    if not isinstance(code, str) or code not in _TOOL_FAILURE_MESSAGES:
        code = 'invalid_tool_request'
    return {'error': code, 'message': _TOOL_FAILURE_MESSAGES[code]}


MAX_ROWS = 50
MAX_TEXT = 12000
_BOUNDARY = (
    "仅限本报告已保存的公开资料；逐字匹配不等于事实核验。关注、提及、共同链接和"
    "账号自述不证明组织隶属、政治立场、指挥或协同行动。"
)
_TEXT_FIELDS = {
    "citation_id": 128, "title": 1000, "source_name": 300,
    "type": 100, "source_type": 100, "published_at": 100,
    "publication_day": 100, "account_key": 256, "account_status": 100,
    "evidence_kind": 100, "source_key": 256, "target_key": 256,
    "relation_type": 100, "observed_at": 100, "research_report_source_role": 100,
}
_META_TEXT_FIELDS = {
    "content_kind": 100, "collection_method": 100, "collected_at": 100,
    "body_completeness": 100, "body_captured_at": 100,
    "body_capture_method": 100, "publication_date_precision": 100,
    "body_published_at": 100, "body_publication_day": 100,
    "body_publication_precision": 100, "kind": 100, "evidence_kind": 100,
    "source_key": 256, "target_key": 256, "relation_type": 100,
    "observed_at": 100, "research_report_source_role": 100, "evidence_id": 256,
}
_META_BOOL_FIELDS = {"body_publication_differs_from_feed", "saved_excerpt_truncated"}
_META_INT_FIELDS = {"original_source_char_count", "source_char_count"}


def _json_copy(value):
    def inspect(item):
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) is list:
            for child in item:
                inspect(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                inspect(child)
            return
        raise EvidenceGatewayError("快照只能包含 JSON 字段，不能包含对象或可执行能力。")
    try:
        inspect(value)
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise EvidenceGatewayError("快照必须是有限、有效的 JSON 数据。") from exc


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key(value):
    return (type(value) is str and 0 < len(value) <= 256
        and ":" in value and not any(ord(char) < 32 for char in value))


def _url(value):
    """Display a saved ordinary public URL only; this function never opens it."""
    if type(value) is not str or len(value) > 4096 or any(ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def _scalar_projection(row, fields):
    result, truncated = {}, []
    for field, limit in fields.items():
        value = row.get(field)
        if value is None and field in row:
            result[field] = None
        elif type(value) is str:
            result[field] = value[:limit]
            if len(value) > limit:
                truncated.append(field)
    if truncated:
        result["projection_truncated_fields"] = truncated
    return result


def _typed(row, field):
    metadata = row.get("metadata") if type(row.get("metadata")) is dict else {}
    values = [container[field] for container in (row, metadata)
        if container.get(field) is not None]
    return values[0] if values and all(type(value) is str and value == values[0] for value in values) else None


def _observation(row):
    """Require one unambiguous saved visible-follow observation and direction."""
    metadata = row.get("metadata") if type(row.get("metadata")) is dict else {}
    kinds = [container[field] for container in (row, metadata)
        for field in ("kind", "evidence_kind") if container.get(field) is not None]
    role = _typed(row, "research_report_source_role")
    if role in {"visible_follow_list", "public_profile", "visible_profile", "profile"}:
        kinds.append(role)
    if row.get("type") == "visible_follow_list":
        kinds.append(row["type"])
    if not kinds or any(kind != "visible_follow_list" for kind in kinds):
        return None
    source, target = _typed(row, "source_key"), _typed(row, "target_key")
    if not _key(source) or not _key(target) or source == target:
        return None
    if row.get("account_key") is not None and row["account_key"] != source:
        return None
    relation = _typed(row, "relation_type")
    if relation != "follows":
        # Earlier collector records used an exact caption before relation_type
        # was introduced. Partial or conflicting typed fields are not repaired.
        if any("relation_type" in container and container["relation_type"] is not None for container in (row, metadata)):
            return None
        if row.get("content") != f"公开可见列表：{source} → {target}":
            return None
    return source, target


def _string_schema(maximum, minimum=1):
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def _integer_schema(minimum, maximum, default=None):
    value = {"type": "integer", "minimum": minimum, "maximum": maximum}
    if default is not None:
        value["default"] = default
    return value


def _tool(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}}}


class EvidenceGateway:
    """Seven synchronous tools operating only on the supplied saved snapshot.

    ``tools`` contains ordinary OpenAI function definitions; ``call`` returns a
    JSON object or raises ``EvidenceGatewayError``. Manifest, IDs and tool schema
    accessors do not expose mutable internal state or arbitrary configuration.
    Source text offsets count Python Unicode characters, never encoded bytes.
    """

    def __init__(self, items, research_context=None, source_reviews=None):
        if (type(items) is not list or research_context is not None and type(research_context) is not dict
                or source_reviews is not None and type(source_reviews) is not list):
            raise EvidenceGatewayError("材料、研究上下文和来源审阅的容器类型无效。")
        self._items = _json_copy(items)
        self._context = _json_copy(research_context if research_context is not None else {})
        self._reviews = _json_copy(source_reviews if source_reviews is not None else [])
        self._by_id, self._aliases, self._hashes = {}, {}, {}
        for row in self._items:
            if type(row) is not dict:
                raise EvidenceGatewayError("每条报告材料必须为对象。")
            identity = row.get("citation_id")
            if type(identity) is not str or not 0 < len(identity) <= 128 or identity.strip() != identity:
                raise EvidenceGatewayError("每条报告材料需要有效的原有引用编号。")
            if identity in self._by_id:
                raise EvidenceGatewayError("报告材料引用编号重复，不能建立证据工具。")
            if "content" in row and type(row["content"]) is not str:
                raise EvidenceGatewayError("保存正文必须为文本，不能隐式转换。")
            row.setdefault("content", "")
            self._by_id[identity] = row
            self._hashes[identity] = _text_hash(row["content"])
            metadata = row.get("metadata") if type(row.get("metadata")) is dict else {}
            for alias in (identity, row.get("evidence_id"), metadata.get("evidence_id")):
                if type(alias) is str and alias:
                    self._aliases.setdefault(alias, set()).add(identity)
        self._accounts = self._known_accounts()
        self._observed_edges = self._saved_observed_edges()
        self._account_citations = {key: [] for key in self._accounts}
        for row in self._items:
            keys = {row.get("account_key")} if type(row.get("account_key")) is str else set()
            observation = _observation(row)
            if observation:
                keys.update(observation)
            for key in keys & self._accounts.keys():
                self._account_citations[key].append(row["citation_id"])
        self._review_by_id = {}
        for review in self._reviews:
            if (type(review) is not dict or type(review.get("citation_id")) is not str
                    or review["citation_id"] not in self._by_id):
                raise EvidenceGatewayError("来源审阅必须对应本快照的引用编号。")
            identity = review["citation_id"]
            if identity in self._review_by_id:
                raise EvidenceGatewayError("来源审阅的引用编号重复。")
            self._review_by_id[identity] = review
        self._schemas = self._tool_schemas()
        # The hash binds all saved text and every supplied context/review field.
        # It is never derived from a selected display projection or an API key.
        self._manifest = {"version": 1, "mode": "saved_report_snapshot_only",
            "input_fingerprint": _digest({"items": self._items, "research_context": self._context, "source_reviews": self._reviews}),
            "material_sha256": _digest(self._items), "research_context_sha256": _digest(self._context),
            "source_reviews_sha256": _digest(self._reviews), "evidence_count": len(self._items),
            "account_count": len(self._accounts), "observed_follow_count": len(self._observed_edges),
            "tool_names": list(self._schemas), "boundary": _BOUNDARY}

    @property
    def tools(self):
        return copy.deepcopy(list(self._schemas.values()))

    @property
    def manifest(self):
        return copy.deepcopy(self._manifest)

    @property
    def citation_ids(self):
        return tuple(self._by_id)

    @property
    def account_keys(self):
        return tuple(self._accounts)

    def _known_accounts(self):
        result = {}
        for field in ("accounts", "nodes"):
            rows = self._context.get(field)
            for row in rows if type(rows) is list else []:
                if type(row) is dict and _key(row.get("account_key")):
                    key = row["account_key"]
                    result[key] = {**result.get(key, {}), **row, "_saved_node": True}
        for edge in self._relationships():
            for field in ("source_key", "target_key"):
                key = edge.get(field)
                if _key(key):
                    result.setdefault(key, {"account_key": key, "_saved_node": True})
        deferred = self._context.get("deferred_frontier")
        for row in deferred if type(deferred) is list else []:
            if type(row) is dict and _key(row.get("account_key")):
                key = row["account_key"]
                account = row.get("account") if type(row.get("account")) is dict else {}
                result[key] = {**account, **result.get(key, {}), "account_key": key, "_deferred": row}
        return result

    def _relationships(self):
        rows = []
        for field in ("relationships", "edges"):
            values = self._context.get(field)
            if type(values) is list:
                rows.extend(row for row in values if type(row) is dict)
        return rows

    def _saved_observed_edges(self):
        result = {}
        for edge in self._relationships():
            source, target = edge.get("source_key"), edge.get("target_key")
            if (edge.get("basis") != "observed" or not _key(source) or not _key(target)
                    or source not in self._accounts or target not in self._accounts or source == target):
                continue
            relationships = [edge[field] for field in ("relationship", "relation") if edge.get(field) is not None]
            if not relationships or any(value != "follows" for value in relationships):
                continue
            if "evidence_ids" in edge and (type(edge["evidence_ids"]) is not list or any(type(value) is not str for value in edge["evidence_ids"])):
                continue
            references = [*([edge["evidence_id"]] if edge.get("evidence_id") is not None else []), *(edge.get("evidence_ids") or [])]
            if not references or any(type(value) is not str or value not in self._aliases for value in references):
                continue
            identities = set().union(*(self._aliases[value] for value in references))
            if any(_observation(self._by_id[identity]) != (source, target) for identity in identities):
                continue
            key = (source, target)
            saved = result.setdefault(key, {"source_key": source, "target_key": target,
                "relationship": "follows", "basis": "observed", "citation_ids": []})
            saved["citation_ids"] = sorted(set(saved["citation_ids"]) | identities)
        return list(result.values())

    def _tool_schemas(self):
        page = {"offset": _integer_schema(0, 1000000, 0), "limit": _integer_schema(1, MAX_ROWS, 10)}
        identity = _string_schema(128)
        account = _string_schema(256)
        tools = [
            _tool("list_evidence", "分页检索本报告已保存证据。type/account 必须属于快照；query 仅匹配保存标题或正文。不会扩展到外部检索。",
                {**page, "type": _string_schema(100), "account": account, "query": _string_schema(500)}),
            _tool("read_evidence", "按 Unicode 字符偏移读取保存的逐字正文，单次最多 12000 字；返回完整正文长度与 SHA-256。不是外部网页读取。",
                {"citation_id": identity, "offset": _integer_schema(0, 100000000, 0), "limit": _integer_schema(1, MAX_TEXT, MAX_TEXT)}, ("citation_id",)),
            _tool("inspect_account", "检查已保存账号节点的类型、核验状态、材料及有引用的观察关系。类型与核验分开；不推断隶属或个人政治身份。",
                {"account_key": account, **page}, ("account_key",)),
            _tool("trace_observed_path", "沿已保存且引用方向匹配的 observed follows 边寻找最多三跳的有向路径；不会反向、补线或把关注当隶属。",
                {"source_key": account, "target_key": account, "max_hops": _integer_schema(1, 3, 3), "limit": _integer_schema(1, 10, 10)}, ("source_key", "target_key")),
            _tool("prioritize_frontier", "只对快照内的已知节点和已保存待继续候选做结构优先排序。理由来自材料覆盖和保存的观察边，结果仍是研究候选，不提升核验或隶属。", page),
            _tool("compare_evidence", "比较 2–8 条保存材料的实际文本、逐字摘录与已有来源审阅，返回待核对的差异候选；不判定外部真伪。总正文摘录不超过 12000 字。",
                {"citation_ids": {"type": "array", "items": identity, "minItems": 2, "maxItems": 8, "uniqueItems": True},
                    "excerpt_limit": _integer_schema(1, 1500, 1000)}, ("citation_ids",)),
            _tool("check_citation", "在指定保存正文中逐字检查 exact_quote；返回 Unicode 偏移和全文/摘录 hash。不会做翻译、空白归一化或替换引用来源。",
                {"citation_id": identity, "exact_quote": _string_schema(MAX_TEXT), "offset": _integer_schema(0, 100000000)}, ("citation_id", "exact_quote")),
        ]
        return {tool["function"]["name"]: tool for tool in tools}

    def _validate(self, name, args):
        if type(name) is not str or name not in self._schemas:
            raise EvidenceGatewayError("未授权的工具；只能调用保存证据工具。")
        if type(args) is not dict:
            raise EvidenceGatewayError("工具参数必须为对象。")
        schema = self._schemas[name]["function"]["parameters"]
        if set(args) - schema["properties"].keys() or not set(schema["required"]) <= args.keys():
            raise EvidenceGatewayError("工具参数缺失或含未授权字段。")
        for field, value in args.items():
            expected = schema["properties"][field]
            kind = expected["type"]
            if kind == "integer":
                valid = type(value) is int and expected["minimum"] <= value <= expected["maximum"]
            elif kind == "string":
                valid = type(value) is str and expected["minLength"] <= len(value) <= expected["maxLength"]
            else:
                valid = (type(value) is list and expected["minItems"] <= len(value) <= expected["maxItems"]
                    and all(type(child) is str and expected["items"]["minLength"] <= len(child) <= expected["items"]["maxLength"] for child in value)
                    and len(set(value)) == len(value))
            if not valid:
                raise EvidenceGatewayError(f"工具参数 {field} 的类型或范围无效。")
        values = {field: value["default"] for field, value in schema["properties"].items() if "default" in value}
        values.update(args)
        for field in ("citation_id",):
            if field in values and values[field] not in self._by_id:
                raise EvidenceGatewayError("引用编号不在本报告保存的材料中。")
        for identity in values.get("citation_ids", []):
            if identity not in self._by_id:
                raise EvidenceGatewayError("引用编号不在本报告保存的材料中。")
        for field in ("account_key", "source_key", "target_key", "account"):
            if field in values and values[field] not in self._accounts:
                raise EvidenceGatewayError("账号不在本快照已保存的节点或待继续候选中。")
        if "type" in values and values["type"] not in {row.get("type") for row in self._items if type(row.get("type")) is str}:
            raise EvidenceGatewayError("材料类型不在本快照目录中。")
        return values

    def call(self, name, args):
        values = self._validate(name, args)
        result = getattr(self, "_call_" + name)(**values)
        return copy.deepcopy({"tool": name, "input_fingerprint": self._manifest["input_fingerprint"],
            "boundary": _BOUNDARY, **result})

    def _material(self, row):
        result = _scalar_projection(row, _TEXT_FIELDS)
        url = _url(row.get("url"))
        if url is not None:
            result["url"] = url
        metadata = row.get("metadata") if type(row.get("metadata")) is dict else {}
        result["metadata"] = _scalar_projection(metadata, _META_TEXT_FIELDS)
        for field in _META_BOOL_FIELDS:
            if type(metadata.get(field)) is bool:
                result["metadata"][field] = metadata[field]
        for field in _META_INT_FIELDS:
            if type(metadata.get(field)) is int and 0 <= metadata[field] <= 100000000:
                result["metadata"][field] = metadata[field]
        result.update(total_characters=len(row["content"]), source_sha256=self._hashes[row["citation_id"]])
        return result

    def _call_list_evidence(self, offset, limit, type=None, account=None, query=None):
        allowed = set(self._account_citations.get(account, [])) if account is not None else None
        matches = [row for row in self._items if (type is None or row.get("type") == type)
            and (allowed is None or row["citation_id"] in allowed)
            and (query is None or query.casefold() in (str(row.get("title") or "") + "\n" + row["content"]).casefold())]
        rows = [self._material(row) for row in matches[offset:offset + limit]]
        return {"rows": rows, "total": len(matches), "offset": offset, "limit": limit,
            "next_offset": offset + len(rows) if offset + len(rows) < len(matches) else None}

    def _call_read_evidence(self, citation_id, offset, limit):
        row = self._by_id[citation_id]
        total = len(row["content"])
        if offset > total:
            raise EvidenceGatewayError("正文偏移超过保存文字长度。")
        text = row["content"][offset:offset + limit]
        end = offset + len(text)
        return {**self._material(row), "text": text, "offset": offset, "end": end,
            "returned_characters": len(text), "fragment_sha256": _text_hash(text),
            "next_offset": end if end < total else None, "saved_text_complete": end == total and offset == 0}

    def _account(self, key):
        row = self._accounts[key]
        result = _scalar_projection(row, {"account_key": 256, "name": 300,
            "platform": 100, "handle": 256, "depth": 100})
        result["account_key"] = key
        status = row.get("status", row.get("account_status", "unknown"))
        result["verification_status"] = status if type(status) is str and status in {"candidate", "verified", "rejected", "stale", "excluded", "seed"} else "unknown"
        result["verified"] = result["verification_status"] == "verified"
        metadata = row.get("metadata") if type(row.get("metadata")) is dict else {}
        identity = metadata.get("account_identity")
        accepted = (type(identity) is dict and type(identity.get("version")) is int and identity["version"] == 1
            and type(identity.get("type")) is str and identity["type"] in {"organization", "individual", "unknown"}
            and type(identity.get("basis")) is str and identity["basis"] in {"manual_review", "registered_source_backlink"})
        result["account_type"] = identity["type"] if accepted else "unknown"
        result["account_type_basis"] = identity["basis"] if accepted else "insufficient_saved_identity_evidence"
        result["account_type_is_verification"] = False
        result["affiliation_established"] = False
        result["saved_deferred_candidate"] = "_deferred" in row
        for field in ("url", "evidence_url"):
            url = _url(row.get(field))
            if url is not None:
                result[field] = url
        return result

    def _edge(self, row):
        return {**row, "citation_ids": row["citation_ids"][:MAX_ROWS],
            "citation_count": len(row["citation_ids"]), "citations_truncated": len(row["citation_ids"]) > MAX_ROWS}

    def _call_inspect_account(self, account_key, offset, limit):
        edges = [self._edge(row) for row in self._observed_edges if account_key in {row["source_key"], row["target_key"]}]
        citations = self._account_citations[account_key]
        return {"account": self._account(account_key), "citation_ids": citations[offset:offset + limit],
            "evidence_count": len(citations), "evidence_next_offset": offset + min(limit, max(0, len(citations) - offset)) if offset + limit < len(citations) else None,
            "observed_relationships": edges[offset:offset + limit], "observed_relationship_count": len(edges),
            "relationship_next_offset": offset + min(limit, max(0, len(edges) - offset)) if offset + limit < len(edges) else None,
            "offset": offset, "limit": limit}

    def _call_trace_observed_path(self, source_key, target_key, max_hops, limit):
        adjacency = {}
        for row in self._observed_edges:
            adjacency.setdefault(row["source_key"], []).append(row)
        queue = deque([(source_key, [source_key], [])])
        paths, scanned = [], 0
        scan_limit = 10000
        while queue and len(paths) < limit and scanned < scan_limit:
            current, nodes, edges = queue.popleft()
            if len(edges) >= max_hops:
                continue
            for edge in adjacency.get(current, []):
                if scanned >= scan_limit or len(paths) >= limit:
                    break
                scanned += 1
                next_key = edge["target_key"]
                if next_key in nodes:
                    continue
                next_edges, next_nodes = [*edges, self._edge(edge)], [*nodes, next_key]
                if next_key == target_key:
                    paths.append({"account_keys": next_nodes, "edges": next_edges,
                        "hops": len(next_edges), "citation_ids": sorted({identity for row in next_edges for identity in row["citation_ids"]})[:MAX_ROWS]})
                elif len(next_edges) < max_hops:
                    queue.append((next_key, next_nodes, next_edges))
        limited = bool(queue) or scanned >= scan_limit or len(paths) >= limit
        return {"source_key": source_key, "target_key": target_key, "max_hops": max_hops,
            "paths": paths, "returned_paths": len(paths), "search_limited": limited,
            "absence_is_global_absence": False, "scanned_edges": scanned}

    def _call_prioritize_frontier(self, offset, limit):
        rows = []
        incoming_counts, outgoing_counts = {}, {}
        for edge in self._observed_edges:
            incoming_counts[edge["target_key"]] = incoming_counts.get(edge["target_key"], 0) + 1
            outgoing_counts[edge["source_key"]] = outgoing_counts.get(edge["source_key"], 0) + 1
        visited = self._context.get("visited")
        visited = {value for value in visited if type(value) is str} if type(visited) is list else None
        for key, account in self._accounts.items():
            incoming = incoming_counts.get(key, 0)
            outgoing = outgoing_counts.get(key, 0)
            citations = self._account_citations[key]
            reasons, score = [], 0
            deferred = account.get("_deferred")
            if type(deferred) is dict:
                score += 40
                reasons.append("saved_deferred_work")
            if visited is not None and key not in visited:
                score += 20
                reasons.append("not_in_saved_visited_keys")
            if not citations:
                score += 10
                reasons.append("no_main_report_material")
            if incoming:
                score += min(incoming, 10) * 2
                reasons.append("supported_saved_incoming_follows")
            if not reasons:
                reasons.append("known_saved_node_for_coverage_review")
            pending_items = deferred.get("items") if type(deferred) is dict else None
            rows.append({"account_key": key, "candidate_state": "saved_deferred" if type(deferred) is dict else "saved_known_node",
                "priority_score": score, "ranking_basis": "saved_structure_and_coverage_only", "reasons": reasons,
                "saved_evidence_count": len(citations), "citation_ids": citations[:10],
                "observed_in_count": incoming, "observed_out_count": outgoing,
                "pending_record_count": len(pending_items) if type(pending_items) is list else 0,
                "verification_promoted": False, "affiliation_established": False})
        rows.sort(key=lambda row: (-row["priority_score"], row["account_key"]))
        page = rows[offset:offset + limit]
        return {"rows": page, "total": len(rows), "offset": offset, "limit": limit,
            "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
            "pending_material_is_main_report_evidence": False}

    def _review(self, identity):
        raw = self._review_by_id.get(identity)
        if raw is None:
            return None
        result = _scalar_projection(raw, {"reading": 1000, "limitations": 500})
        quote = raw.get("quote")
        if type(quote) is str and quote and quote in self._by_id[identity]["content"]:
            # Preserve an exact prefix; never normalize a saved quotation.
            quote = quote[:220]
            result.update(quote=quote, quote_exact_match=True,
                quote_offset=self._by_id[identity]["content"].find(quote), quote_sha256=_text_hash(quote))
        else:
            result.update(quote=None, quote_exact_match=False)
        result["review_is_source_truth"] = False
        return result

    def _call_compare_evidence(self, citation_ids, excerpt_limit):
        sources = []
        for identity in citation_ids:
            row = self._by_id[identity]
            text = row["content"][:excerpt_limit]
            sources.append({**self._material(row), "text": text, "offset": 0,
                "end": len(text), "text_truncated": len(text) < len(row["content"]),
                "fragment_sha256": _text_hash(text), "source_review": self._review(identity)})
        candidates = []
        for first, left in enumerate(sources):
            for right in sources[first + 1:]:
                if self._by_id[left["citation_id"]]["content"] != self._by_id[right["citation_id"]]["content"]:
                    candidates.append({"citation_ids": [left["citation_id"], right["citation_id"]],
                        "kind": "different_saved_text_requires_comparison",
                        "contradiction_established": False, "external_truth_checked": False})
        return {"sources": sources, "comparison_candidates": candidates,
            "returned_text_characters": sum(len(row["text"]) for row in sources),
            "contradiction_established": False, "external_truth_checked": False}

    def _call_check_citation(self, citation_id, exact_quote, offset=None):
        text = self._by_id[citation_id]["content"]
        position = text.find(exact_quote) if offset is None else offset
        valid = position >= 0 and text[position:position + len(exact_quote)] == exact_quote
        return {"citation_id": citation_id, "exact_match": valid, "valid": valid,
            "offset": position if valid else None, "end": position + len(exact_quote) if valid else None,
            "source_sha256": self._hashes[citation_id], "quote_sha256": _text_hash(exact_quote),
            "total_characters": len(text), "quote_characters": len(exact_quote),
            "truth_verified": False}
