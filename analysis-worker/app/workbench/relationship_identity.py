"""Conservative identity grouping for public-source AI review candidates.

This is label/provenance housekeeping, never verification of affiliation.
The first label and ID remain stable. Original bounded model rows are retained
so a completed source-bound checkpoint can be reconstructed without a call.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

IDENTITY_VERSION = 1
# The repository's single-claim envelope is 256 KiB. Leave room for its
# relationship, subject, observation and human-review wrapper fields.
CANDIDATE_BYTE_LIMIT = 240 * 1024
_QUOTES = {'「': '」', '『': '』', '“': '”', '"': '"', "'": "'"}
# A spelling pair alone is not sufficient: the account's own cited excerpt
# must explicitly state the full-name/abbreviation relationship.
_ALIASES = {'全日本学生自治会総連合': '全学連', '現代革命労働者党': '現労党'}
_PARENS = re.compile(r'[（(]([^（）()]{1,100})[）)]')
_PROTECTED = re.compile(r'委員長|委员长|代表|会長|議長|支部|分会|地区|地域|派|系|関西|關西|关西|関東|旧|機関紙|机关报|機関誌|刊物|紹介|介绍|介紹|呼びかけ人|議員')


def _unquote(label: str) -> str:
    value = label.strip()
    while len(value) > 2 and value[0] in _QUOTES and value[-1] == _QUOTES[value[0]]:
        opener, closer = value[0], _QUOTES[value[0]]
        if opener == closer:
            if closer in value[1:-1]:
                break
        else:
            depth = 0
            for index, char in enumerate(value):
                depth += char == opener
                depth -= char == closer
                if depth == 0 and index != len(value) - 1:
                    break
            if depth != 0 or index != len(value) - 1:
                break
        value = value[1:-1].strip()
    return value


def _own_ids(row: dict, evidence: dict) -> set[str]:
    return {eid for eid in row.get('evidence_ids', []) if eid in evidence
            and evidence[eid].get('account_key') == row.get('account_key')
            and evidence[eid].get('type') != 'visible_follow_list'}


def _qualifiers(text: str) -> set[str]:
    """Retain explicit qualifiers; deliberately prefer false negatives."""
    result = set()
    for match in _PARENS.finditer(text):
        value = match[1].strip()
        if _PROTECTED.search(value):
            result.add('qualifier:' + value)
    # Names immediately adjoining a leadership title are kept only with an
    # explicit text boundary. Do not turn explanatory '保留矢嶋委員長限定'
    # into a different person from the source's '(矢嶋委員長)'.
    for match in re.finditer(r'(?:^|[（(「『“\s/:：、，。▶︎])([\w一-龥]{1,12})(委員長|代表|会長|議長)', text):
        if not match[1].startswith(('保留', '限定', '原文', '引用', '候选', '候補', '记为', '記為')):
            result.add('qualifier:' + match[1] + match[2])
    for match in re.finditer(r'(?:委員長|委员长|代表|会長|議長)[：:]\s*([\w一-龥]{1,12})', text):
        result.add('leader:' + match[1])
    for match in re.finditer(r'(?:保留|限定为|限定為|派系[：:]|职务[：:]|職務[：:])\s*([\w一-龥]{1,12}(?:委員長|委员长|代表|会長|議長|派))(?:限定|[，。；;\s]|$)', text):
        result.add('qualifier:' + match[1])
    # These markers distinguish content channels, offices and public roles.
    for marker in ('呼びかけ人', '議員', '機関紙', '机关报', '機関誌', '刊物', '紹介', '介绍', '介紹',
                   '関西', '關西', '关西', '関東', '支部', '分会'):
        if marker in text:
            result.add('marker:' + marker)
    return result


def _subject_names(label: str) -> set[str]:
    value = _unquote(label)
    names = {value, re.split(r'[（(]', value, maxsplit=1)[0].strip()}
    for full, short in _ALIASES.items():
        if value in {full, short, f'{full}（{short}）', f'{full}({short})'}:
            names.update((full, short))
    return {name for name in names if name}


def _subject_qualifiers(text: str, names: set[str]) -> set[str]:
    """Read qualifiers in the named subject's clause, not unrelated topics.

    Profiles separate roles with slashes; publication titles often place the
    signatory after a pipe. A quake called '関東大震災' before that pipe must
    not become the signatory organization's regional identity.
    """
    result = set()
    for name in sorted(names, key=len, reverse=True):
        for match in re.finditer(re.escape(name), text):
            suffix = re.split(r'[/|▶︎\n。；;，、]', text[match.end():match.end() + 80], maxsplit=1)[0]
            result.update(_qualifiers(name + suffix))
            prefix = text[max(0, match.start() - 16):match.start()]
            for region in ('関西', '關西', '关西', '関東'):
                if prefix.endswith(region):
                    result.add('marker:' + region)
            leader = re.search(r'([\w一-龥]{1,12}(?:委員長|代表|会長|議長))の$', prefix)
            if leader:
                result.add('qualifier:' + leader[1])
    return result


def qualifier_signature(row: dict, evidence: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    label = str(row.get('organization') or '')
    claim = str(row.get('model_claim') or row.get('claim') or '')
    names = _subject_names(label)
    explicit = _qualifiers(label) | _subject_qualifiers(claim, names)
    # Preserve explicit parenthesized leader/faction/region annotations even
    # when the model placed them later in its explanatory sentence. Generic
    # role/place words elsewhere in a multi-role quote are not subject proof.
    explicit.update(value for value in _qualifiers(claim) if value.startswith(('qualifier:', 'leader:')))
    for annotation in re.finditer(r'(?:保留|限定为|限定為|职务|職務|地区|派系).{0,24}', claim):
        explicit.update(_qualifiers(annotation[0]))
    source = set()
    for eid in _own_ids(row, evidence):
        source.update(_subject_qualifiers(str(evidence[eid].get('excerpt') or ''), names))
    return tuple(sorted(explicit)), tuple(sorted(source))


def _alias_identity(label: str, rows: list[dict], evidence: dict) -> tuple[str, set[str]]:
    value = _unquote(label)
    own = set().union(*(_own_ids(row, evidence) for row in rows))
    for full, short in _ALIASES.items():
        if value not in {full, short, f'{full}（{short}）', f'{full}({short})'}:
            continue
        pattern = re.compile(r'(?<![\w一-龥])' + re.escape(full) + r'[（(]\s*(?:略称[：:]\s*)?' + re.escape(short) + r'\s*[）)]')
        proof = {eid for eid in own if pattern.search(str(evidence[eid].get('excerpt') or ''))}
        if proof:
            return full, proof
    return value, set()


def identity_match(existing: dict, incoming: dict, evidence: dict) -> dict | None:
    """Return finite spelling proof only for compatible own-source subjects."""
    if existing.get('account_key') != incoming.get('account_key'):
        return None
    if any(row.get('method') != 'configured_ai' or row.get('status') != 'candidate' for row in (existing, incoming)):
        return None
    if qualifier_signature(existing, evidence) != qualifier_signature(incoming, evidence):
        return None
    left, right = existing['organization'], incoming['organization']
    own_left, own_right = _own_ids(existing, evidence), _own_ids(incoming, evidence)
    shared = own_left & own_right
    if not shared:
        return None
    if left == right:
        method, proof = 'same_label', set()
    elif _unquote(left) == _unquote(right):
        method, proof = 'decorative_outer_quotes', set()
    else:
        left_identity, left_proof = _alias_identity(left, [existing, incoming], evidence)
        right_identity, right_proof = _alias_identity(right, [existing, incoming], evidence)
        proof = left_proof | right_proof
        if left_identity != right_identity or not proof:
            return None
        method = 'explicit_source_alias'
    return {'method': method, 'shared_own_evidence_ids': sorted(shared),
            'alias_proof_evidence_ids': sorted(proof)}


def _variant(row: dict) -> dict:
    keys = ('id', 'account_key', 'organization', 'claim', 'evidence_ids', 'evidence_urls',
            'claim_corrections', 'support_kind')
    variant = {key: copy.deepcopy(row[key]) for key in keys if key in row}
    variant['model_claim'] = row.get('model_claim', row['claim'])
    variant['review_batch_ids'] = list(dict.fromkeys(row.get('review_batch_ids') or []))
    identity = {key: value for key, value in variant.items() if key != 'review_batch_ids'}
    variant['variant_id'] = 'ai-variant-' + hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()[:24]
    return variant


def prepare_candidate(row: dict, evidence: dict, *, review_batch_ids: list[str] | None = None) -> dict:
    """Attach one original bounded row, without truncating its provenance."""
    result = copy.deepcopy(row)
    if review_batch_ids is not None:
        result['review_batch_ids'] = list(dict.fromkeys(review_batch_ids))
    variant = _variant(result)
    result['identity_version'] = IDENTITY_VERSION
    result['identity_variants'] = [variant]
    result['identity_merge_audit'] = []
    explicit, source = qualifier_signature(result, evidence)
    result['identity_qualifiers'] = sorted(set(explicit) | set(source))
    return result


def merge_candidate_entries(existing: dict, incoming: dict, evidence_rows: dict) -> dict | None:
    """Keep the first representative and every original variant/source."""
    match = identity_match(existing, incoming, evidence_rows)
    if match is None:
        return None
    merged = copy.deepcopy(existing)
    merged.setdefault('identity_variants', [_variant(existing)])
    seen = {row['variant_id']: row for row in merged['identity_variants']}
    for variant in incoming.get('identity_variants') or [_variant(incoming)]:
        variant = copy.deepcopy(variant)
        previous = seen.get(variant['variant_id'])
        if previous:
            previous['review_batch_ids'] = list(dict.fromkeys(previous.get('review_batch_ids', []) + variant.get('review_batch_ids', [])))
            continue
        merged['identity_variants'].append(variant)
        seen[variant['variant_id']] = variant
        audit = {**match, 'representative_id': merged['id'], 'incoming_variant_id': variant['variant_id'],
                 'incoming_id': variant.get('id'), 'incoming_organization': variant['organization']}
        merged.setdefault('identity_merge_audit', []).append(audit)
    merged['identity_version'] = IDENTITY_VERSION
    merged['evidence_ids'] = list(dict.fromkeys(existing['evidence_ids'] + incoming['evidence_ids']))
    merged['evidence_urls'] = list(dict.fromkeys(existing['evidence_urls'] + incoming['evidence_urls']))
    merged['review_batch_ids'] = list(dict.fromkeys(existing.get('review_batch_ids', []) + incoming.get('review_batch_ids', [])))
    merged['identity_qualifiers'] = sorted(set(existing.get('identity_qualifiers', [])) | set(incoming.get('identity_qualifiers', [])))
    return merged
