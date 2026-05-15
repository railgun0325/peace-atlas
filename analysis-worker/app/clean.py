from __future__ import annotations

import hashlib
import re
from html import unescape

from bs4 import BeautifulSoup


WHITESPACE_RE = re.compile(r"\s+")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return normalize_text(text)


def normalize_text(text: str) -> str:
    text = unescape(text or "")
    text = text.replace("\u3000", " ")
    return WHITESPACE_RE.sub(" ", text).strip()


def clean_article_text(title: str, raw_text: str) -> str:
    cleaned = strip_html(raw_text)
    if not cleaned:
        cleaned = normalize_text(title)
    return cleaned


def detect_language(text: str) -> str:
    if not text:
        return "unknown"
    chinese_chars = len(CHINESE_RE.findall(text))
    return "zh-Hant" if chinese_chars >= max(3, len(text) * 0.08) else "en"


def content_hash(title: str, clean_text: str) -> str:
    material = normalize_text(f"{title}\n{clean_text}").lower()
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
