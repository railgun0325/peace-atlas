"""Explicit local legacy import; source is read-only and destination is backed up."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis-worker"))

from app.workbench.legacy_import import migrate_collectors
from app.workbench.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source, destination = args.source.resolve(strict=True), args.destination.resolve()
    if source == destination:
        parser.error("Source and destination must be different files")
    backup = None
    if destination.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = destination.with_name(f"{destination.stem}-before-social-{stamp}{destination.suffix}")
        with sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(backup) as snapshot:
            original.backup(snapshot)
    store = Store(destination)
    try:
        result = migrate_collectors(source, store)
    finally:
        store.close()
    result.update(destination=str(destination), backup=str(backup) if backup else None)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
