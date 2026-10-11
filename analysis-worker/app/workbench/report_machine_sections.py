"""Separate one exact machine-owned runtime slot without accepting its prose.

Removed text remains fully auditable. The caller renders the real run receipt
from saved data, validates the remaining body and retains audits on resume.
This module does not create citations or verify any removed source assertion.
"""
from __future__ import annotations

import hashlib
import re


RUNTIME_HEADING = "## 运行元数据〔程序生成〕"
_LINES = re.compile(r"[^\r\n]*(?:\r\n|\r|\n|$)")
_ATX = re.compile(r" {0,3}(#{1,6})(?:[ \t]+|$)")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")
_SETEXT = re.compile(r" {0,3}(=+|-+)[ \t]*$")
_LIST = re.compile(r" {0,3}(?:[-+*][ \t]+|\d+[.)][ \t]+)")
_HTML_CODE = re.compile(r" {0,3}<(pre|script|style|textarea)(?:[ \t>]|$)", re.I)
_HTML_COMMENT = re.compile(r" {0,3}<!--")


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _headings(markdown):
    """Locate structural headings; literal fenced/indented/quoted text stays."""
    headings, fence, html_code, paragraph_start = [], None, None, None
    html_comment = False
    for match in _LINES.finditer(markdown):
        if match.start() == match.end():
            continue
        line = match[0].rstrip("\r\n")
        if html_comment:
            if "-->" in line:
                html_comment = False
            paragraph_start = None
            continue
        if html_code:
            if re.search(r"</" + html_code + r"[ \t]*>", line, re.I):
                html_code = None
            paragraph_start = None
            continue
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + r"{" + str(fence[1]) + r",}[ \t]*", line):
                fence = None
            paragraph_start = None
            continue
        opening = _FENCE.fullmatch(line)
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            fence = (opening[1][0], len(opening[1]))
            paragraph_start = None
            continue
        comment = _HTML_COMMENT.match(line)
        if comment:
            html_comment = line.find("-->", comment.end()) < 0
            paragraph_start = None
            continue
        code = _HTML_CODE.match(line)
        if code:
            if not re.search(r"</" + code[1] + r"[ \t]*>", line, re.I):
                html_code = code[1]
            paragraph_start = None
            continue
        if not line.strip() or line.startswith(("    ", "\t")) or re.match(r" {0,3}>", line):
            paragraph_start = None
            continue
        atx = _ATX.match(line)
        if atx:
            headings.append((match.start(), len(atx[1]), line == RUNTIME_HEADING))
            paragraph_start = None
            continue
        underline = _SETEXT.fullmatch(line)
        if underline:
            if paragraph_start is not None:
                headings.append((paragraph_start, 1 if underline[1][0] == "=" else 2, False))
            paragraph_start = None
            continue
        if _LIST.match(line):
            paragraph_start = None
        elif paragraph_start is None:
            paragraph_start = match.start()
    # Setext starts belong to the current paragraph, which begins after the
    # last structural heading. Positions are already in document order.
    return headings


def extract_machine_sections(markdown: str) -> dict:
    """Remove only the exact reserved runtime heading's structural section.

    Offsets are zero-based, half-open Unicode codepoint positions in the
    original string. Same/higher ATX or Setext headings terminate a slot and
    remain in the body. Unknown/paired markers have no special meaning here.
    Empty output is returned with body_present=False and must fail the caller's
    ordinary nonempty-body/citation gate. No removed claim is deemed correct.
    """
    if not isinstance(markdown, str):
        raise TypeError("报告正文必须为字符串。")
    document_hash = _digest(markdown)
    headings = _headings(markdown)
    spans, next_boundary = [], len(markdown)
    # The only reserved marker is level two. One reverse pass finds its next
    # same/higher boundary without repeated slices or suffix scans.
    for start, level, reserved in reversed(headings):
        if reserved:
            spans.append((start, next_boundary))
        if level <= 2:
            next_boundary = start
    removed = []
    for start, end in reversed(spans):
        raw = markdown[start:end]
        removed.append({"marker": RUNTIME_HEADING, "start": start, "end": end,
            "raw": raw, "sha256": _digest(raw), "document_sha256": document_hash,
            "reason": "reserved_runtime_metadata_replaced_by_server_receipt",
            "offset_unit": "unicode_codepoints", "raw_truth_established": False})
    pieces, cursor = [], 0
    for entry in removed:
        pieces.append(markdown[cursor:entry["start"]])
        cursor = entry["end"]
    pieces.append(markdown[cursor:])
    clean = "".join(pieces)
    return {"version": 1, "clean_markdown": clean, "removed_sections": removed,
        "raw_sha256": document_hash, "clean_sha256": _digest(clean),
        "changed": bool(removed), "footer_render_required": bool(removed),
        "raw_truth_established": False, "body_present": bool(clean.strip())}
