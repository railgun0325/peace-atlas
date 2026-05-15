from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BASE_DIR / "config" / "entities.yml"


def load_entity_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG
    with config_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def match_entities(text: str, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = config or load_entity_config()
    text = text or ""
    lower_text = text.lower()
    results: dict[tuple[str, str], dict[str, Any]] = {}
    for entity_type, entries in (config.get("entities") or {}).items():
        for entry in entries or []:
            name = str(entry.get("name", "")).strip()
            aliases = [name, *entry.get("aliases", [])]
            for alias in aliases:
                alias = str(alias).strip()
                if not alias:
                    continue
                if alias.lower() in lower_text:
                    key = (name, entity_type)
                    confidence = 0.95 if alias == name else 0.85
                    existing = results.get(key)
                    if not existing or confidence > existing["confidence"]:
                        results[key] = {
                            "entity_text": alias,
                            "entity_type": entity_type,
                            "normalized_name": name,
                            "confidence": confidence,
                        }
    return sorted(results.values(), key=lambda item: (-item["confidence"], item["normalized_name"]))
