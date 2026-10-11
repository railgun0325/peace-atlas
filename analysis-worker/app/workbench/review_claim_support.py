"""Minimum independent text support for a saved claim's explicit qualifiers.

This pure gate does not establish affiliation, membership, account ownership or
the truth of a candidate's explanation. The caller must strictly validate the
decision, citations and complete batch before applying a fixed evidence gap.
Only checked independent quote fields supply support; never the saved statement.
"""
from __future__ import annotations

import re
import unicodedata

from .relationship_support import _unsupported_context

CLAIM_QUALIFIER_GAP = '保存主张的明确身份限定缺少已核对的独立摘录支持；不能确认包含该限定的整条主张。'
CLAIM_QUALIFIER_SCHEMA_ERROR = '保存主张限定或已核对依据的字段格式无效。'
_ALIASES = {
    '關西': '関西', '关西': '関西', '關東': '関東', '关东': '関東',
    '機關報': '機関紙', '机关报': '機関紙', '機關紙': '機関紙',
    '介紹': '紹介', '介绍': '紹介', '委员长': '委員長', '委員長': '委員長',
    '会长': '会長', '议长': '議長', '分會': '分会',
}
# Finite existing repository spelling pairs; no general name translation.
_ORGANIZATION_ALIASES = {'全日本学生自治会総連合': '全学連', '現代革命労働者党': '現労党'}
_MARKERS = {'呼びかけ人', '議員', '機関紙', '機関誌', '刊物', '紹介', '関西', '関東', '支部', '分会'}
_REGIONS = {'関西', '関東'}
_ROLES = ('委員長', '代表', '会長', '議長')
_CLAUSES = re.compile(r'[\n\r。；;|/、,，!?！？]+')
_SPACING = r'[\s「」『』“”"\'()\[\]・]{0,8}'
_JOIN = _SPACING + r'(?:(?:の|的)' + _SPACING + r')?'
_UNCERTAIN = re.compile(r'ですか|でしょうか|かもしれ|未確認|未证实|未證實|不明|候補|候选|候選')
_PERSON = re.compile(r'^[\w一-龥々ぁ-んァ-ヶ][\w一-龥々ぁ-んァ-ヶ ・]{0,39}$')


class ClaimSupportSchemaError(ValueError):
    """Fixed host schema error; must not be treated as semantic evidence gap."""


def _invalid():
    raise ClaimSupportSchemaError(CLAIM_QUALIFIER_SCHEMA_ERROR)


def _normalize(text):
    value = unicodedata.normalize('NFKC', text)
    for original in sorted(_ALIASES, key=len, reverse=True):
        value = value.replace(original, _ALIASES[original])
    return value.casefold()


def _qualified_values(record):
    if not isinstance(record, dict):
        _invalid()
    metadata = record.get('metadata', {})
    if not isinstance(metadata, dict):
        _invalid()
    result = []
    for container in (record, metadata):
        if 'identity_qualifiers' not in container:
            continue
        values = container['identity_qualifiers']
        if not isinstance(values, list) or len(values) > 64:
            _invalid()
        for value in values:
            if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
                _invalid()
            value = _normalize(value).strip()
            if ':' not in value:
                _invalid()
            kind, detail = value.split(':', 1)
            kind, detail = kind.strip(), detail.strip()
            if not kind or not detail:
                _invalid()
            pair = kind, detail
            if pair not in result:
                result.append(pair)
    return result


def _checked_clauses(proofs):
    if not isinstance(proofs, list) or len(proofs) > 3:
        _invalid()
    clauses = []
    for proof in proofs:
        if (not isinstance(proof, dict) or not isinstance(proof.get('quote'), str)
                or not 1 <= len(proof['quote']) <= 2000
                or type(proof.get('independent')) is not bool or type(proof.get('valid')) is not bool
                or any(ord(c) < 32 and not c.isspace() for c in proof['quote'])):
            _invalid()
        if proof['valid'] is not True or proof['independent'] is not True:
            continue
        for clause in _CLAUSES.split(_normalize(proof['quote'])):
            # Quotes are considered separately, with no cross-proof stitching.
            if clause.strip() and len(clause) <= 600 and not _unsupported_context(clause) and not _UNCERTAIN.search(clause):
                clauses.append(clause.strip())
    return clauses


def _subject_forms(record):
    label = record.get('object_label', record.get('organization'))
    if not isinstance(label, str) or not 2 <= len(label) <= 500 or any(ord(c) < 32 for c in label):
        _invalid()
    value = _normalize(label).strip(' 「」『』"\'')
    base = re.sub(r'\([^()]{1,100}\)', '', value).strip()
    forms = [value, base]
    for full, short in _ORGANIZATION_ALIASES.items():
        if base in {full, short}:
            forms.extend((full, short))
    result = []
    for form in forms:
        if len(form) < 2:
            continue
        left = r'(?<![a-z0-9_])' if form[0].isascii() and form[0].isalnum() else ''
        right = r'(?![a-z0-9_])' if form[-1].isascii() and form[-1].isalnum() else ''
        pattern = left + re.escape(form) + right
        if pattern not in result:
            result.append(pattern)
    return result


def _descriptor_supported(subjects, clauses, value):
    # Adjacent descriptors only. "Organisation has a meeting; regional weather"
    # and "Organisation discusses another publication" cannot fill this gate.
    if value in _REGIONS:
        ending = r'(?=通信|支部|本部|地区|地域|地方|分会|委員会|$|[\s)\]」』:])'
    elif value in {'機関紙', '機関誌', '刊物'}:
        ending = r'(?=$|[\s「『"()\[\]]|の|は|です|である|アカウント|账号|帳號)'
    else:
        ending = r'(?=$|[\s「『"()\[\]]|の|は|です|である|通信|アカウント|账号|帳號)'
    descriptor = re.escape(value)
    region = r'(?:関西|関東)'
    unit = r'(?:支部|本部|地区|地域|地方|分会|通信)'
    # A directly named regional unit remains a subject modifier, rather than
    # arbitrary words between the organisation and the publication role.
    prefix = (r'(?:' + region + unit + _JOIN + r')?' if value in {'機関紙', '機関誌', '刊物'}
              else r'(?:' + region + _SPACING + r')?' if value in {'支部', '分会'} else '')
    for clause in clauses:
        for subject in subjects:
            if re.search(subject + _JOIN + prefix + descriptor + ending, clause):
                return True
            if re.search(descriptor + _JOIN + subject, clause):
                return True
    return False


def _leader_supported(subjects, clauses, person, role=None):
    if not _PERSON.fullmatch(person):
        return False
    roles = [role] if role else list(_ROLES)
    name = re.escape(person)
    boundary = r'(?:(?![\w一-龥々ぁ-んァ-ヶ])|(?=です(?:$|[\s)」』])|である(?:$|[\s)」』])))'
    for clause in clauses:
        for subject in subjects:
            for title in roles:
                office = re.escape(title)
                patterns = (
                    subject + _JOIN + name + _SPACING + office,
                    subject + _JOIN + office + _SPACING + r'(?:は|:|=)?' + _SPACING + name + boundary,
                    r'(?<![\w一-龥々ぁ-んァ-ヶ])' + name + _SPACING + office + _JOIN + subject,
                )
                if any(re.search(pattern, clause) for pattern in patterns):
                    return True
    return False


def missing_claim_qualifiers(record, resolved_independent_proofs):
    """Return fixed missing-support codes for every saved explicit qualifier.

    Top-level and metadata qualifiers are combined. Shape errors use a fixed
    ClaimSupportSchemaError and remain distinct from evidence insufficiency.
    Unknown well-formed qualifiers fail conservatively. Empty output is only a
    minimum text gate, never a standalone positive relationship decision.
    """
    values = _qualified_values(record)
    clauses = _checked_clauses(resolved_independent_proofs)
    subjects = _subject_forms(record)
    if not values:
        return []
    missing = []
    for kind, value in values:
        if kind == 'marker' and value in _MARKERS:
            supported = _descriptor_supported(subjects, clauses, value)
            code = 'marker_support_missing'
        elif kind == 'leader' and _PERSON.fullmatch(value):
            supported = _leader_supported(subjects, clauses, value)
            code = 'leader_support_missing'
        elif kind == 'qualifier':
            leader = re.fullmatch(r'(.{1,40}?)(委員長|代表|会長|議長)', value)
            if leader and _PERSON.fullmatch(leader[1]):
                supported = _leader_supported(subjects, clauses, leader[1], leader[2])
            elif value in _MARKERS or re.fullmatch(r'(?:関西|関東)(?:支部|本部|地区|地域|地方|分会)', value):
                supported = _descriptor_supported(subjects, clauses, value)
            else:
                supported = False
                code = 'unknown_claim_qualifier'
                if code not in missing:
                    missing.append(code)
                continue
            code = 'qualifier_support_missing'
        else:
            supported = False
            code = 'unknown_claim_qualifier'
        if not supported and code not in missing:
            missing.append(code)
    return missing
