"""Independently compare completed desktop packages with their source manifest."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import posixpath
import stat
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("release_builder", ROOT / "desktop/build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    records = json.loads(Path(args.results).read_text("utf-8"))
    audited = []
    for record in records:
        archive_path, target = Path(record["archive"]), record["target"]
        assert digest(archive_path) == record["sha256"], "archive hash differs from completed build"
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None, "ZIP CRC check failed"
            names = set(archive.namelist())
            manifest_path = next(name for name in names if name.endswith("/build-manifest.json"))
            prefix = manifest_path.removesuffix("build-manifest.json")
            manifest = json.loads(archive.read(manifest_path))
            sources = {name: path for path, name in builder.application_files(target)}
            packed = {item["path"]: item["sha256"] for item in manifest["application_files"]}
            assert sources.keys() == packed.keys(), "packaged file set differs from source allowlist"
            for name, expected in packed.items():
                assert expected == digest(sources[name]), f"source changed since build: {name}"
                assert expected == hashlib.sha256(archive.read(prefix + name)).hexdigest(), f"payload differs: {name}"
            required = {"backend/app/workbench/backup.py", "backend/app/workbench/research_repository.py",
                        "backend/app/static/workbench/api.js", "app/main.cjs",
                        "backend/app/workbench/restore.py", "app/restore.cjs",
                        "backend/app/workbench/job_retries.py"}
            assert required <= packed.keys(), "product hardening files missing"
            portable_checks = []
            if record.get('portable_directory'):
                portable = Path(record['portable_directory']).resolve()
                for name, expected in packed.items():
                    extracted = portable / 'resources' / name
                    assert extracted.is_file() and digest(extracted) == expected, f"portable payload differs: {name}"
                    portable_checks.append('resources/' + name)
                for name in ('PEACE ATLAS.exe', 'resources/python/python.exe', 'resources/build-manifest.json'):
                    extracted = portable / name
                    assert extracted.is_file(), f"portable runtime missing: {name}"
                    assert digest(extracted) == hashlib.sha256(archive.read(portable.name + '/' + name)).hexdigest(), f"portable runtime differs: {name}"
                    portable_checks.append(name)
            private = {".env", "connection.secure", "cookies.sqlite", "state.json", "storage_state.json", "legacy_main.py"}
            for name in names:
                parts = PurePosixPath(name.lower()).parts
                assert parts[-1] not in private and not parts[-1].endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")), f"private data in package: {name}"
                assert "/backend/app/static/workbench/tests/" not in name and "/app/tests/" not in name, f"application tests in package: {name}"
            links = {entry.filename: archive.read(entry).decode("utf-8") for entry in archive.infolist() if stat.S_ISLNK(entry.external_attr >> 16)}
            for name, destination in links.items():
                assert not PurePosixPath(destination).is_absolute(), f"absolute symlink: {name}"
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), destination))
                assert resolved.startswith("PEACE ATLAS.app/"), f"symlink escaped bundle: {name}"
            if target.startswith("macos"):
                expected = builder.MAC_FINALIZE.replace("@TARGET@", target).replace("@ARTIFACT_SUFFIX@", builder.ARTIFACT_SUFFIX)
                assert archive.read("Finalize PEACE ATLAS.command").decode("utf-8") == expected
                for name in ("Finalize PEACE ATLAS.command", "PEACE ATLAS.app/Contents/MacOS/Electron"):
                    assert archive.getinfo(name).external_attr >> 16 & 0o111, f"missing executable mode: {name}"
            audited.append({"target": target, "archive": str(archive_path), "sha256": record["sha256"],
                            "size_bytes": archive_path.stat().st_size, "application_files": len(packed),
                            "entries": len(names), "source_manifest_payload": "passed", "zip_crc": "passed",
                            "private_data_and_application_tests": "absent", "symlinks": len(links),
                            "native_runtime_test": "separate acceptance required",
                            "build_validation": record["validation"]})
            audited[-1]['portable_files_verified'] = portable_checks
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"audited_at": datetime.now(timezone.utc).isoformat(), "ok": True,
                                 "packages": audited}, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"ok": True, "packages": [{"target": item["target"], "application_files": item["application_files"]} for item in audited]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
