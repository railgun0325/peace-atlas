"""Conservative text support gates for public relationship review candidates.

These functions establish a minimum text/subject binding. They do not verify
organization ownership, establish a person's political affiliation, or prove
that every sentence in a model's free-text explanation follows from a source.
No I/O, model calls, persistence, or account-status changes occur here.
"""
from __future__ import annotations

import re
import unicodedata


_CLAUSE_SPLIT = re.compile(r"[\n\r。；;|/]+")
_NEGATED = re.compile(
    r"では(?:あり)?ません|ではない|じゃない|でない|無所属|無関係|非公式|非公認|"
    r"并非|並非|不是|不属于|不屬於|不隶属|不隸屬|未加入|无隶属|無隸屬|"
    r"\bnot\s+(?:affiliated|official|a\s+member|associated)|\bno\s+(?:affiliation|connection)",
    re.I,
)
_HISTORICAL = re.compile(
    r"元\s*所属|旧\s*所属|舊\s*所屬|以前.{0,20}(?:所属|公式)|"
    r"かつて.{0,20}(?:所属|公式)|元.{0,20}公式アカウント|"
    r"以前.{0,20}(?:隶属|隸屬|所屬)|曾经.{0,20}(?:隶属|属于)|"
    r"曾經.{0,20}(?:隸屬|屬於)|\bformer\b|\bex[- ]member\b",
    re.I,
)
_REPORTED = re.compile(r"引用\s*[:：]|転載\s*[:：]|转述|轉述|他人|友人の|誰かの|\b(?:quote|retweet|someone)\b", re.I)
_LABEL = re.compile(r"^\s*(?:所属|所屬|隶属|隸屬|affiliation)\s*[:：]\s*(?P<organization>[^\n。；;|]{2,70})\s*$", re.I)
_OFFICIAL = re.compile(
    r"^\s*(?P<organization>[^\n。；;|]{2,55}?)(?:の)?公式アカウント"
    r"(?:です|でございます|になります)?[\s.!！]*$"
)
_DISAMBIGUATING_QUALIFIER = re.compile(r"委員長|代表|(?:^|[^機])派|系|支部|分会|分會|地区|地區|地域|関西|関東|旧|舊|新会|新會")
_GENERIC_LABELS = {"公式", "official", "officialaccount", "アカウント", "账号", "帳號", "候補", "候选", "候選"}
_ADMINISTRATIVE_UNIT = re.compile(r"^[一-龥々ぁ-んァ-ヶA-Za-z0-9]{1,24}(?:都|道|府|県|区|市|町|村|郡)$")
_ORGANIZATION_NOUN = re.compile(r"党|労組|労働|組合|団体|委員|同盟|協会|センター|会")
_REGION_LABEL_END = re.compile(r"(?:地域|地区|地方|北部|南部|東部|西部)$")


def _unsupported_context(clause: str) -> bool:
    return bool(_NEGATED.search(clause) or _HISTORICAL.search(clause) or _REPORTED.search(clause))


def _unquote_organization(value: str) -> str | None:
    value = value.strip(" ・,，：:")
    for left, right in (("「", "」"), ("『", "』"), ('"', '"'), ("'", "'")):
        if value.startswith(left) and value.endswith(right):
            value = value[len(left):-len(right)].strip()
    # A quote enclosing the whole declaration is not the subject declaring it.
    # Balanced quotes around an organization name above remain permitted.
    if any(char in value for char in "「」『』\"'"):
        return None
    return value if 2 <= len(value) <= 70 else None


def explicit_profile_claims(bio: str) -> list[tuple[str, str]]:
    """Extract only bounded, positive, current profile declarations.

    Accept direct affiliation labels and a complete Japanese official-account
    declaration. Reject the known negative, historical, and third-party/quoted
    counterexamples. Ambiguous prose is left for review instead of converted
    into a self_declared record. The caller retains the original profile.
    """
    if not isinstance(bio, str):
        return []
    claims: list[tuple[str, str]] = []
    seen: set[str] = set()
    for clause in _CLAUSE_SPLIT.split(bio):
        if _unsupported_context(clause):
            continue
        match = _LABEL.fullmatch(clause) or _OFFICIAL.fullmatch(clause)
        if match is None:
            continue
        organization = _unquote_organization(match["organization"])
        if organization and organization not in seen:
            seen.add(organization)
            claims.append((organization, match.group(0).strip()))
        if len(claims) >= 4:
            break
    return claims


def _text_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in normalized if not char.isspace() and not unicodedata.category(char).startswith(("P", "Z")))


def _organization_text_forms(organization: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", organization).strip()
    parentheticals = re.findall(r"\(([^()]*)\)", normalized)
    # A faction, regional branch, or named leader is a qualifier, not an alias.
    # Do not silently discard it and let a different faction match a bare name.
    if any(_DISAMBIGUATING_QUALIFIER.search(value) for value in parentheticals):
        forms = [normalized]
    else:
        forms = [normalized, re.sub(r"\([^()]*\)", "", normalized)]
        for value in parentheticals:
            forms.append(re.sub(r"^(?:略称|略稱|通称|通稱|別称|別稱|以下|aka)\s*[:：]?\s*", "", value, flags=re.I))
    # A university student body's direct self-description may use the Japanese
    # possessive particle: 広島大学学生自治会 / 広島大学の学生自治会. Keep
    # this equivalence bounded to institution/student-body nouns, not arbitrary
    # particle deletion that could merge different organization names.
    for value in list(forms):
        possessive = re.sub(r"(大学院|大学|高等学校|高校|学校|学園|学院)(学生自治会|自治会|学生会|生徒会)", r"\1の\2", value)
        if possessive != value:
            forms.append(possessive)
        regional = re.sub(r"地域(労組交流センター)$", r"地域の\1", value)
        if regional != value:
            forms.append(regional)
    keys = []
    for value in forms:
        key = _text_key(value)
        # Avoid generic qualifiers becoming support for an unrelated group.
        if len(key) >= 2 and key not in _GENERIC_LABELS and key not in keys:
            keys.append(key)
    return keys


def _regional_text_variant(form: str, clause: str) -> str:
    """Remove only an inserted administrative list in the complete identity.

    東京北部地域(豊島区・板橋区...)の労組交流センター remains the same
    regional-center self-description. Preserve its region prefix and complete
    organization noun. Named leaders, factions, branches, or other entities in
    parentheses never qualify as an administrative list.
    """
    if "地域" not in form or not form.endswith("労組交流センター"):
        return clause
    def replace(match):
        detail = match[1]
        places = re.split(r"[・、,，]", detail)
        if not 1 <= len(places) <= 20 or _DISAMBIGUATING_QUALIFIER.search(detail) or _ORGANIZATION_NOUN.search(detail):
            return match[0]
        if not all(_ADMINISTRATIVE_UNIT.fullmatch(place.strip()) for place in places):
            return match[0]
        return ""
    return re.sub(r"(?<=地域)\(([^()]*)\)", replace, clause)


def _form_matches(form: str, clause: str) -> bool:
    # Keep Latin names/abbreviations from matching inside a different word or
    # handle (DA in TODAY, or Organization A in Organization AB). Punctuation
    # and spacing differences remain allowed between the name's characters.
    left = r"(?<![a-z0-9_])" if form[0].isascii() and form[0].isalnum() else ""
    right = r"(?![a-z0-9_])" if form[-1].isascii() and form[-1].isalnum() else ""
    body = r"[\s\W_]*".join(re.escape(char) for char in form)
    normalized = unicodedata.normalize("NFKC", clause).casefold()
    if re.search(left + body + right, normalized) is not None:
        return True
    regional = _regional_text_variant(form, normalized)
    return regional != normalized and re.search(left + body + right, regional) is not None


def affiliation_support_error(account_key: str, organization: str, evidence_rows: list[dict]) -> str | None:
    """Return an explainable reason for clearly unsupported model candidates.

    Same-subject profile/post text must mention the organization or a bounded
    parenthetical alias. Follows and display names cannot provide this support,
    nor can another account's profile fill a subject's missing support.
    A successful result is minimum text support only, still a review candidate.
    """
    if not isinstance(account_key, str) or not isinstance(organization, str):
        return "候选账号或组织名不是有效文本。"
    label = unicodedata.normalize("NFKC", organization).strip()
    if _REGION_LABEL_END.search(label) and not _ORGANIZATION_NOUN.search(label):
        return "候选标签只有地区名称，缺少可核对的组织身份；地区提及不能作为组织隶属支持。"
    rows = evidence_rows if isinstance(evidence_rows, list) else []
    own_text = [row for row in rows if isinstance(row, dict) and row.get("account_key") == account_key
                and row.get("type") in {"profile", "post"} and isinstance(row.get("excerpt"), str)
                and row["excerpt"].strip()]
    if not own_text:
        return "缺少该账号自己的公开简介或原帖文本支持；关注列表、显示名和他人简介不能支持组织隶属候选。"
    forms = _organization_text_forms(organization)
    if not forms:
        return "组织名没有可核对的具体文本或别名。"
    found_unsupported = False
    for row in own_text:
        for clause in _CLAUSE_SPLIT.split(row["excerpt"]):
            if not any(_form_matches(form, clause) for form in forms):
                continue
            if _unsupported_context(clause):
                found_unsupported = True
                continue
            return None
    if found_unsupported:
        return "该账号匹配组织名的文本属于否定、过去身份或转述，不能支持当前本人组织关系候选。"
    return "该账号自己的引用文本未出现该组织名或明确别名；不能用无关本人材料、显示名或他人组织简介补足支持。"
