from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BASE_DIR / "config" / "topics.yml"


def load_topic_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG
    with config_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def classify_topics(title: str, text: str, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = config or load_topic_config()
    haystack = f"{title}\n{text}".lower()
    matches: list[dict[str, Any]] = []
    for topic in config.get("topics", []):
        keywords = topic.get("keywords", [])
        score = 0
        matched_keywords = []
        for keyword in keywords:
            keyword = str(keyword).strip()
            if keyword and keyword.lower() in haystack:
                score += 1
                matched_keywords.append(keyword)
        min_score = int(topic.get("min_score", 1))
        if score >= min_score:
            matches.append(
                {
                    "topic_name": topic["name"],
                    "confidence": min(0.35 + score * 0.15, 0.95),
                    "matched_keywords": matched_keywords[:8],
                }
            )
    if not matches:
        return [{"topic_name": "未分类/待研判", "confidence": 0.2, "matched_keywords": []}]
    return sorted(matches, key=lambda item: (-item["confidence"], item["topic_name"]))
