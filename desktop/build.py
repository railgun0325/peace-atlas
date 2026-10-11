"""Build self-contained desktop ZIPs without requiring Node or target-OS tools.

Downloads are pinned, verified against official release checksums, and cached.
macOS archives are assembled directly, preserving symlinks and Unix file modes
even when this builder runs on Windows. No repository secrets or data are read.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import time
import urllib.request
from urllib.parse import quote
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
CACHE = ROOT / ".build-cache"
DIST = ROOT / "dist"
VERSION = "2.0.0"
ELECTRON_VERSION = "44.5.1"
PYTHON_VERSION = "3.12.15"
PYTHON_RELEASE = "20261003"
PLAYWRIGHT_VERSION = "1.59.0"
CHROMIUM_REVISION = "1217"
CHROMIUM_VERSION = "147.0.7727.15"
ARTIFACT_SUFFIX = "-foundation-quality-20261011"
MAC_MAIN_EXECUTABLE = "PEACE ATLAS"
CHROMIUM_TARGETS = {
    "windows-x64": "win64", "macos-arm64": "mac-arm64", "macos-x64": "mac-x64",
}
CHROMIUM_DIGESTS = {
    # SHA-256 pins populated from HTTPS official CfT artifacts after checking
    # Google's separately supplied object MD5; no upstream SHA-256 is claimed.
    "win64": "61f2a4b00a264a2f4311a3d4c45c3d13505aa33f57ba458e2f20918d5fcd0f18",
    "mac-arm64": "324f68a2c3a03ea5c9de81297ede5f78b333659b2b6594a4cb8071cd171f1404",
    "mac-x64": "97c69099141b5031ef44ecf4869e08eddf915421191b7e3ffeeb11babdacdc1b",
}
TARGETS = {
    "windows-x64": {"electron": "win32-x64", "python": "x86_64-pc-windows-msvc", "platform": "win_amd64"},
    "macos-arm64": {"electron": "darwin-arm64", "python": "aarch64-apple-darwin", "platform": "macosx_13_0_arm64"},
    "macos-x64": {"electron": "darwin-x64", "python": "x86_64-apple-darwin", "platform": "macosx_13_0_x86_64"},
}
DEFAULT_TARGETS = ("windows-x64", "macos-arm64")
BACKEND_MODULES = ("__init__.py", "clean.py", "dedupe.py", "topics.py", "entities.py", "japan_geo.py", "gdelt_fetcher.py")
AGENT_ASSET_PATHS = (
    "manifest.json", "INSTALL.md", "AUTOMATIC_REVIEW.md", "peace-atlas/SKILL.md", "peace-atlas/agents/openai.yaml",
    "peace-atlas/references/api.md", "peace-atlas/scripts/peace_atlas.py",
)
CONFIG_FILES = ("japan_peace_topics.yml", "japan_peace_entities.yml")
# Verified from the official GitHub release asset metadata on 2026-10-06.
# These pinned digests also make rebuilding independent of GitHub API quotas.
PINNED_DIGESTS = {
    "electron-v44.5.1-darwin-arm64.zip": "1d75703019bb16461ae65f3081d7e6f5c0b11e901d0ccb5c343bcf7bcdd6435c",
    "electron-v44.5.1-darwin-x64.zip": "e567d13833d0e161d7749727355b98643461df3395b537cfa7bdddf8a8bfedff",
    "electron-v44.5.1-win32-x64.zip": "9b382492dcfee91f8f9e92c91f7972550a1b95d2299cac72279dab33a600d7db",
    "SHASUMS256.txt": "8cfa7460b2aaac1812bfeed3c6957d14ad384b9f31ae0407ba5e64bb6b43ec3e",
    "cpython-3.12.15+20261003-aarch64-apple-darwin-install_only_stripped.tar.gz": "ad8d0c637c0a36b967b310e2c07254f4d2ca8cabaa7699e55ed6290aceb481a2",
    "cpython-3.12.15+20261003-x86_64-apple-darwin-install_only_stripped.tar.gz": "562c30864ece2cb1d3e0ad66a1acd498611a47e5a10ce81b99158bef1ccbd355",
    "cpython-3.12.15+20261003-x86_64-pc-windows-msvc-install_only_stripped.tar.gz": "6fba7f2ae506facf41d457ea8293c7497910a675c69a4e954875169410a50402",
}
MAC_FINALIZE = r'''#!/bin/bash
# Native macOS finalization: integrity signing only, no Gatekeeper changes.
set -euo pipefail
on_error() {
  STATUS=$?
  echo "Finalization stopped (exit $STATUS). Your existing research data was not used." >&2
  echo "Audit directory: ${AUDIT:-not created yet}" >&2
  if [ -n "${LOG:-}" ] && [ -f "$LOG" ]; then tail -n 18 "$LOG" >&2; fi
  if [ -t 0 ]; then read -r -p 'Press Return to close this window. ' _; fi
  exit "$STATUS"
}
trap on_error ERR
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
APP="$SCRIPT_DIR/PEACE ATLAS.app"
SMOKE=1
for argument in "$@"; do
  if [ "$argument" = "--smoke" ]; then SMOKE=1; elif [ "$argument" = "--no-smoke" ]; then SMOKE=0; else APP="$argument"; fi
done
if [ "$(uname -s)" != Darwin ]; then echo 'Run this finalizer on macOS.' >&2; false; fi
if [ ! -d "$APP/Contents" ]; then echo "App bundle missing: $APP" >&2; false; fi
APP="$(cd "$APP" && pwd -P)"
BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$APP/Contents/Info.plist")
if [ "$BUNDLE_ID" != org.peaceatlas.workbench ]; then echo 'Unexpected app identity; stopped.' >&2; false; fi
MAIN_EXECUTABLE=$(/usr/libexec/PlistBuddy -c 'Print CFBundleExecutable' "$APP/Contents/Info.plist")
if [ "$MAIN_EXECUTABLE" != "PEACE ATLAS" ] || [ ! -x "$APP/Contents/MacOS/$MAIN_EXECUTABLE" ] || [ -e "$APP/Contents/MacOS/Electron" ]; then
  echo 'Unexpected or missing main executable; stopped.' >&2; false
fi
AUDIT=$(mktemp -d "${TMPDIR:-/tmp}/peace-atlas-sign.XXXXXX")
LOG="$AUDIT/codesign.log"
echo "Signing PEACE ATLAS locally. Audit: $AUDIT"
sign_code() {
  if /usr/bin/codesign --display "$1" >/dev/null 2>&1; then
    /usr/bin/codesign --force --sign - --preserve-metadata=identifier,entitlements,flags,runtime "$1" >>"$LOG" 2>&1
  else
    /usr/bin/codesign --force --sign - "$1" >>"$LOG" 2>&1
  fi
}
# Sign individual Mach-O files, including Python extensions in Resources.
# Libraries keep their own metadata; no blanket entitlements are added.
while IFS= read -r -d '' code; do
  if /usr/bin/file -b "$code" | /usr/bin/grep -q 'Mach-O'; then sign_code "$code"; fi
done < <(/usr/bin/find "$APP/Contents" -type f -print0)
# Seal nested bundles from the inside out, then seal the outer application.
while IFS= read -r -d '' bundle; do sign_code "$bundle"; done < <(
  /usr/bin/find "$APP/Contents" -depth -type d \( -name '*.framework' -o -name '*.app' -o -name '*.xpc' -o -name '*.appex' \) -print0
)
/usr/bin/codesign --force --sign - --identifier "$BUNDLE_ID" --preserve-metadata=entitlements,flags,runtime "$APP" >>"$LOG" 2>&1
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP" >>"$LOG" 2>&1
/usr/bin/codesign --display --verbose=4 "$APP" >>"$LOG" 2>&1
echo 'Ad-hoc integrity signature verified. This is not Developer ID notarization.'
if [ "$SMOKE" -eq 1 ]; then
  SMOKE_DIR="$AUDIT/smoke"
  mkdir -p "$SMOKE_DIR"
  WORKBENCH_SMOKE_EXIT=1 WORKBENCH_DATA_DIR="$SMOKE_DIR" "$APP/Contents/MacOS/$MAIN_EXECUTABLE"
  "$APP/Contents/Resources/python/bin/python3" -I -B -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["health"]["ok"] is True; print("Native backend and desktop smoke passed")' "$SMOKE_DIR/desktop-smoke.json"
  test -s "$SMOKE_DIR/desktop-smoke.png"
  "$APP/Contents/Resources/python/bin/python3" -I -B "$APP/Contents/Resources/backend/browser_smoke.py" --browsers-path "$APP/Contents/Resources/browsers" --output "$SMOKE_DIR/browser-smoke.json"
  /usr/bin/codesign --verify --deep --strict --verbose=2 "$APP" >>"$LOG" 2>&1
fi
OUTPUT="$SCRIPT_DIR/PEACE-ATLAS-2.0.0-@TARGET@@ARTIFACT_SUFFIX@-adhoc.zip"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUTPUT"
/usr/bin/shasum -a 256 "$OUTPUT" | /usr/bin/tee "$OUTPUT.sha256"
echo "Finalized archive: $OUTPUT"
echo "Signing/native-test audit: $AUDIT"
echo "Ready application: $APP"
echo 'No quarantine attributes or Gatekeeper settings were changed.'
/usr/bin/open "$APP"
if [ -t 0 ]; then read -r -p 'PEACE ATLAS is opening. Press Return to close this window. ' _; fi
'''
WSL_SIGNER = r'''"""Isolated Linux helper generated by desktop/build.py."""
from pathlib import Path, PurePosixPath
import hashlib, json, os, plistlib, shutil, stat, struct, subprocess, sys, tempfile, zipfile

source_zip, output_zip, signer_source, audit_file = map(Path, sys.argv[1:])
stage = Path(tempfile.mkdtemp(prefix="peace-atlas-sign-", dir="/var/tmp"))
unpacked = stage / "bundle"
unpacked.mkdir()
original_metadata, links = {}, []
with zipfile.ZipFile(source_zip) as source:
    for entry in source.infolist():
        name = PurePosixPath(entry.filename)
        if name.is_absolute() or ".." in name.parts:
            raise ValueError("Unsafe ZIP member")
        original_metadata[entry.filename] = entry
        destination = unpacked.joinpath(*name.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry.is_dir():
            destination.mkdir(exist_ok=True)
        elif stat.S_ISLNK(entry.external_attr >> 16):
            links.append((destination, source.read(entry).decode("utf-8")))
        else:
            with source.open(entry) as src, destination.open("wb") as out:
                shutil.copyfileobj(src, out)
            destination.chmod((entry.external_attr >> 16) & 0o777 or 0o644)
    for destination, link in links:
        destination.symlink_to(link)
app = unpacked / "PEACE ATLAS.app"
tool = stage / "rcodesign"
shutil.copyfile(signer_source, tool)
tool.chmod(0o755)
environment = {key: value for key, value in os.environ.items() if not key.startswith("RCODESIGN_")}

MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
                b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def thin_signature(data, required=True):
    if data[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("Unsupported thin Mach-O byte order/architecture; cannot claim verification")
    offset = 32
    for _ in range(struct.unpack_from("<I", data, 16)[0]):
        command, size = struct.unpack_from("<II", data, offset)
        if command == 0x1d:
            signature_offset, signature_size = struct.unpack_from("<II", data, offset + 8)
            break
        offset += size
    else:
        if not required:
            return {"entitlements": None}
        raise ValueError("Mach-O has no code signature")
    signature = data[signature_offset:signature_offset + signature_size]
    blobs = {}
    for index in range(struct.unpack_from(">I", signature, 8)[0]):
        kind, offset = struct.unpack_from(">II", signature, 12 + index * 8)
        length = struct.unpack_from(">I", signature, offset + 4)[0]
        blobs[kind] = signature[offset:offset + length]
    directory = blobs[0]
    flags, hash_offset, identifier_offset, special, slots, limit = struct.unpack_from(">IIIIII", directory, 12)
    hash_size, hash_type, _, page_exponent = struct.unpack_from(">BBBB", directory, 36)
    algorithm = {1: "sha1", 2: "sha256", 3: "sha256", 4: "sha384"}[hash_type]
    page_size = (1 << page_exponent) if page_exponent else limit
    for index in range(slots):
        digest = hashlib.new(algorithm, data[index * page_size:min((index + 1) * page_size, limit)]).digest()[:hash_size]
        if digest != directory[hash_offset + index * hash_size:hash_offset + (index + 1) * hash_size]:
            raise ValueError(f"Mach-O code page hash mismatch at page {index}")
    for slot in (2, 5, 7):
        if slot in blobs and slot <= special:
            actual = hashlib.new(algorithm, blobs[slot]).digest()[:hash_size]
            expected = directory[hash_offset - slot * hash_size:hash_offset - (slot - 1) * hash_size]
            if actual != expected:
                raise ValueError(f"Mach-O special slot hash mismatch at slot {slot}")
    return {"flags": flags, "identifier": directory[identifier_offset:directory.index(b"\0", identifier_offset)].decode(),
            "entitlements": plistlib.loads(blobs[5][8:]) if 5 in blobs else None, "code_pages": slots,
            "special_hashes": {str(slot): directory[hash_offset - slot * hash_size:hash_offset - (slot - 1) * hash_size].hex() for slot in (1, 3) if slot <= special},
            "algorithm": algorithm, "hash_size": hash_size}


def macho_signatures(data, required=True):
    magic = data[:4]
    if magic in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"):
        endian = ">" if magic[:1] == b"\xca" else "<"
        wide = magic in (b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
        count = struct.unpack_from(endian + "I", data, 4)[0]
        slices = []
        for index in range(count):
            start = 8 + index * (32 if wide else 20)
            cpu, subtype, offset, size = struct.unpack_from(endian + ("IIQQ" if wide else "IIII"), data, start)
            if offset + size > len(data):
                raise ValueError("Universal Mach-O slice exceeds file boundary")
            signature = thin_signature(data[offset:offset + size], required)
            slices.append({**signature, "cpu_type": cpu, "cpu_subtype": subtype})
        return slices
    cpu, subtype = struct.unpack_from("<II", data, 4)
    return [{**thin_signature(data, required), "cpu_type": cpu, "cpu_subtype": subtype}]

original = {}
for current, directories, files in os.walk(app, followlinks=False):
    for name in files:
        path = Path(current) / name
        if path.is_symlink():
            continue
        with path.open("rb") as source:
            magic = source.read(4)
        if magic in MACHO_MAGICS:
            original[path.relative_to(app).as_posix()] = macho_signatures(path.read_bytes(), required=False)
audit_file.parent.mkdir(parents=True, exist_ok=True)
with audit_file.with_suffix(".log").open("w") as log:
    completed = subprocess.run([str(tool), "-C", "/dev/null", "sign", "--timestamp-url", "none", str(app)],
                               stdout=log, stderr=subprocess.STDOUT, cwd=stage, env=environment)
if completed.returncode:
    raise RuntimeError(f"rcodesign failed; inspect {audit_file.with_suffix('.log')}")
verified = []
for relative, before in original.items():
    path = app / relative
    after = macho_signatures(path.read_bytes())
    if len(after) != len(before):
        raise ValueError(f"Signing changed the universal architecture count: {relative}")
    for old, new in zip(before, after):
        if (new["entitlements"] != old["entitlements"] or not new["flags"] & 2 or
            new["cpu_type"] != old["cpu_type"] or new["cpu_subtype"] != old["cpu_subtype"] or
            (new["flags"] & 0x10000) != (old.get("flags", 0) & 0x10000)):
            raise ValueError(f"Ad-hoc signature/entitlements/runtime flags changed unexpectedly: {relative}")
        # Verify every main executable's Info.plist and CodeResources seal.
        if "/MacOS/" in relative:
            contents = path.parent.parent
            for slot, resource in (("1", contents / "Info.plist"), ("3", contents / "_CodeSignature/CodeResources")):
                digest = new["special_hashes"].get(slot)
                if digest and int(digest, 16):
                    actual = hashlib.new(new["algorithm"], resource.read_bytes()).digest()[:new["hash_size"]].hex()
                    if digest != actual:
                        raise ValueError(f"Bundle resource signature mismatch: {relative} slot {slot}")
    verified.append({"path": relative, "slices": [{"cpu_type": item["cpu_type"], "code_pages": item["code_pages"], "identifier": item["identifier"]} for item in after]})
symlink_count = 0
with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as output:
    for current, directories, files in os.walk(unpacked, followlinks=False):
        for name in sorted(files + [name for name in directories if (Path(current) / name).is_symlink()]):
            path = Path(current) / name
            relative = path.relative_to(unpacked).as_posix()
            entry = zipfile.ZipInfo(relative)
            entry.create_system = 3
            entry.external_attr = path.lstat().st_mode << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            if relative in original_metadata:
                entry.date_time = original_metadata[relative].date_time
            if path.is_symlink():
                output.writestr(entry, os.readlink(path).encode("utf-8"))
                symlink_count += 1
            else:
                with path.open("rb") as source, output.open(entry, "w", force_zip64=True) as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
if symlink_count != len(links) or any(not path.is_symlink() or os.readlink(path) != target for path, target in links):
    raise ValueError("Signing changed the bundle symlink graph")
result = {"signer": "apple-codesign 0.29.0", "execution_host": "Linux via WSL", "signature": "ad-hoc",
          "mach_o_code_pages": "passed", "entitlements_preserved": "passed", "main_bundle_resource_hashes": "passed",
          "universal_architectures_and_runtime_flags": "passed",
          "signed_binary_count": len(verified), "symlink_count": symlink_count, "binaries": verified,
          "native_apple_codesign_verification": "pending", "native_macos_launch": "pending"}
audit_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps({key: value for key, value in result.items() if key != "binaries"}), flush=True)
'''


def log(message):
    print(message, flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "peace-atlas-desktop-builder/2.0"}), timeout=90)


def release_metadata(repository, tag):
    path = CACHE / "metadata" / f"{repository.replace('/', '-')}-{tag}.json"
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    if (repository, tag) in {("electron/electron", "v44.5.1"), ("astral-sh/python-build-standalone", "20261003")}:
        names = [name for name in PINNED_DIGESTS if (name.startswith("cpython-") == repository.startswith("astral-sh/"))]
        data = {"tag_name": tag, "checksum_source": "official GitHub release asset digests, verified 2026-10-06",
                "assets": [{"name": name, "digest": "sha256:" + PINNED_DIGESTS[name],
                            "browser_download_url": f"https://github.com/{repository}/releases/download/{tag}/{quote(name)}"} for name in names]}
    else:
        with request(f"https://api.github.com/repos/{repository}/releases/tags/{tag}") as response:
            data = json.load(response)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return data


def download(url, destination, expected):
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
        raise ValueError(f"Missing trustworthy SHA-256 for {destination.name}")
    expected = expected.lower()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256(destination) == expected:
        log(f"Verified cache: {destination.name}")
        return destination
    partial = destination.with_name(destination.name + ".part")
    for attempt in range(3):
        try:
            log(f"Downloading: {destination.name}")
            with request(url) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
            actual = sha256(partial)
            if actual != expected:
                raise ValueError(f"SHA-256 mismatch for {destination.name}: {actual}")
            os.replace(partial, destination)
            return destination
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def acquire_assets(targets):
    electron = release_metadata("electron/electron", "v" + ELECTRON_VERSION)
    python = release_metadata("astral-sh/python-build-standalone", PYTHON_RELEASE)
    electron_assets = {asset["name"]: asset for asset in electron["assets"]}
    python_assets = {asset["name"]: asset for asset in python["assets"]}
    sums_asset = electron_assets["SHASUMS256.txt"]
    sums_digest = (sums_asset.get("digest") or "").removeprefix("sha256:")
    sums_path = download(sums_asset["browser_download_url"], CACHE / "downloads" / f"electron-{ELECTRON_VERSION}-SHASUMS256.txt", sums_digest)
    sums = {}
    for line in sums_path.read_text("utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        sums[name.lstrip("*")] = digest
    specs, tasks = {}, []
    for target in targets:
        electron_name = f"electron-v{ELECTRON_VERSION}-{TARGETS[target]['electron']}.zip"
        python_name = f"cpython-{PYTHON_VERSION}+{PYTHON_RELEASE}-{TARGETS[target]['python']}-install_only_stripped.tar.gz"
        electron_asset, python_asset = electron_assets[electron_name], python_assets[python_name]
        python_digest = (python_asset.get("digest") or "").removeprefix("sha256:")
        if not python_digest:
            checksum_asset = python_assets.get(python_name + ".sha256")
            if checksum_asset is None:
                raise RuntimeError(f"Official checksum missing: {python_name}")
            with request(checksum_asset["browser_download_url"]) as response:
                python_digest = response.read().decode("utf-8").split()[0]
        specs[target] = {
            "electron": {"path": str(CACHE / "downloads" / electron_name), "sha256": sums[electron_name], "url": electron_asset["browser_download_url"]},
            "python": {"path": str(CACHE / "downloads" / python_name), "sha256": python_digest, "url": python_asset["browser_download_url"]},
        }
        chromium_platform = CHROMIUM_TARGETS[target]
        specs[target]["chromium"] = {
            "path": str(CACHE / "downloads" / f"chromium-{CHROMIUM_REVISION}-{chromium_platform}.zip"),
            "sha256": CHROMIUM_DIGESTS[chromium_platform],
            "url": f"https://cdn.playwright.dev/builds/cft/{CHROMIUM_VERSION}/{chromium_platform}/chrome-{chromium_platform}.zip",
        }
        tasks.extend(specs[target].values())
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(download, entry["url"], Path(entry["path"]), entry["sha256"]) for entry in tasks]
        for future in futures:
            future.result()
    return specs


def prepare_wheels(target):
    common = CACHE / "wheels" / "common"
    common.mkdir(parents=True, exist_ok=True)
    if not list(common.glob("sgmllib3k-1.0.0-*.whl")):
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(common),
                        "--index-url", "https://pypi.org/simple", "sgmllib3k==1.0.0"], check=True)
    wheel_dir = CACHE / "wheels" / target
    requirement_hash = sha256(DESKTOP / "requirements-runtime.txt")
    lock_path = wheel_dir / "resolved-wheels.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text("utf-8"))
        if lock["requirements_sha256"] == requirement_hash and lock.get("wheel_platform") == TARGETS[target]["platform"] and all(
                (wheel_dir / item["name"]).is_file() and sha256(wheel_dir / item["name"]) == item["sha256"] for item in lock["wheels"]):
            log(f"Verified wheel cache: {target} ({len(lock['wheels'])} wheels)")
            return [wheel_dir / item["name"] for item in lock["wheels"]]
    # A fresh resolution directory avoids accidental mixing of dependency versions.
    resolved = wheel_dir / ("resolve-" + uuid.uuid4().hex[:10])
    resolved.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--only-binary=:all:",
        "--platform", TARGETS[target]["platform"], "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
        "--dest", str(resolved), "--find-links", str(common), "--find-links", str(wheel_dir), "--index-url", "https://pypi.org/simple",
        "-r", str(DESKTOP / "requirements-runtime.txt")], check=True)
    wheels = sorted(resolved.glob("*.whl"))
    if not wheels:
        raise RuntimeError(f"No dependency wheels resolved for {target}")
    records = []
    for wheel in wheels:
        output = wheel_dir / wheel.name
        shutil.copyfile(wheel, output)
        records.append({"name": wheel.name, "sha256": sha256(output), "size": output.stat().st_size})
    lock_path.write_text(json.dumps({"target": target, "wheel_platform": TARGETS[target]["platform"], "requirements_sha256": requirement_hash, "wheels": records}, indent=2), "utf-8")
    return [wheel_dir / record["name"] for record in records]


def sign_macos_archive(source, target, distribution):
    """Use an existing, explicitly selected WSL distro; install no system tools."""
    signer_name = "apple-codesign-0.29.0-x86_64-unknown-linux-musl.tar.gz"
    signer_url = "https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign%2F0.29.0/" + signer_name
    signer_hash = "dbe85cedd8ee4217b64e9a0e4c2aef92ab8bcaaa41f20bde99781ff02e600002"
    directory = CACHE / "tools" / "apple-codesign-0.29.0"
    archive = download(signer_url, directory / signer_name, signer_hash)
    executable = directory / "rcodesign-linux"
    with tarfile.open(archive) as packed:
        member = next(entry for entry in packed.getmembers() if entry.name.endswith("/rcodesign") and entry.isfile())
        executable.write_bytes(packed.extractfile(member).read())
    script = CACHE / "sign-macos-wsl.py"
    script.write_text(WSL_SIGNER, encoding="utf-8", newline="\n")
    signed = source.with_name(source.name + ".signed")
    audit = CACHE / "signing-audits" / (target + ".json")

    def linux_path(path):
        result = subprocess.run(["wsl.exe", "-d", distribution, "--", "wslpath", "-a", "-u", str(path.resolve())],
                                check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return result.stdout.strip()

    command = ["wsl.exe", "-d", distribution, "--", "python3", *map(linux_path, (script, source, signed, executable, audit))]
    log(f"Ad-hoc signing {target} in isolated WSL workspace")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("macOS signing failed: " + (result.stdout + result.stderr)[-5000:])
    signature = json.loads(audit.read_text("utf-8"))
    signature["signer_sha256"] = signer_hash
    signature["signer_url"] = signer_url
    os.replace(signed, source)
    log(f"Signed {target}: {signature['signed_binary_count']} Mach-O binaries; code-page/resource hashes verified; native Mac verification pending")
    return {key: value for key, value in signature.items() if key != "binaries"}


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in path.parts[0]:
        raise ValueError(f"Unsafe archive path: {name}")
    return path.as_posix()


class Archive:
    def __init__(self, path):
        self.path = path
        self.archive = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)
        self.names = set()

    def add(self, name, data, mode=0o100644, source_info=None):
        name = safe_name(name)
        if name in self.names:
            raise ValueError(f"Duplicate archive entry: {name}")
        self.names.add(name)
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = mode << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        if source_info:
            info.date_time = source_info.date_time
            info.external_attr = source_info.external_attr
            info.create_system = source_info.create_system
        if isinstance(data, (bytes, bytearray)):
            self.archive.writestr(info, data)
        else:
            with self.archive.open(info, "w", force_zip64=True) as output:
                shutil.copyfileobj(data, output, 1024 * 1024)

    def file(self, source, name, mode=0o100644):
        with source.open("rb") as data:
            self.add(name, data, mode)

    def close(self):
        self.archive.close()


def append_electron(archive, runtime, target, prefix):
    mac = target.startswith("macos")
    with zipfile.ZipFile(runtime) as source:
        for entry in source.infolist():
            if entry.is_dir():
                continue
            name = safe_name(entry.filename)
            if mac:
                if not name.startswith("Electron.app/"):
                    # Runtime copyright/notices are kept beside the app.
                    if name in {"LICENSE", "LICENSES.chromium.html", "version"}:
                        archive.add("Runtime notices/" + name, source.read(entry), source_info=entry)
                    continue
                name = prefix + name.removeprefix("Electron.app/")
                if name.startswith(prefix + "Contents/_CodeSignature/"):
                    continue
                if name == prefix + "Contents/MacOS/Electron":
                    # Electron's macOS app.isPackaged checks the executable's
                    # basename. Rebrand the actual main binary with its plist,
                    # preserving its original Unix executable metadata.
                    name = prefix + "Contents/MacOS/" + MAC_MAIN_EXECUTABLE
                if name == prefix + "Contents/Info.plist":
                    plist = plistlib.loads(source.read(entry))
                    plist.update(CFBundleDisplayName="PEACE ATLAS · 反戦研究室", CFBundleName="PEACE ATLAS",
                                 CFBundleIdentifier="org.peaceatlas.workbench", CFBundleShortVersionString=VERSION,
                                 CFBundleVersion=VERSION, CFBundleExecutable=MAC_MAIN_EXECUTABLE)
                    if (DESKTOP / "assets/icon.icns").is_file():
                        plist["CFBundleIconFile"] = "peace-atlas.icns"
                    archive.add(name, plistlib.dumps(plist), source_info=entry)
                    continue
                if name.startswith(prefix + "Contents/Frameworks/Electron Helper") and name.endswith(".app/Contents/Info.plist"):
                    plist = plistlib.loads(source.read(entry))
                    # Electron ships helpers without this optional field. Make
                    # their real executable explicit for portable bundle signers.
                    helper_name = PurePosixPath(name).parents[1].name.removesuffix(".app")
                    plist.setdefault("CFBundleExecutable", helper_name)
                    archive.add(name, plistlib.dumps(plist), source_info=entry)
                    continue
            else:
                name = prefix + name
                if name.endswith("/electron.exe"):
                    name = name.removesuffix("electron.exe") + "PEACE ATLAS.exe"
                    if os.name == "nt" and (DESKTOP / "assets/icon.ico").is_file():
                        tool_archive = download("https://registry.npmjs.org/rcedit/-/rcedit-5.0.2.tgz",
                            CACHE / "tools/rcedit-5.0.2/rcedit-5.0.2.tgz",
                            "39e3949ea1f187005043fc13acc31eb23887119ed1e4ff8cdb2d202c4a31ed73")
                        tool = tool_archive.parent / "rcedit-x64.exe"
                        with tarfile.open(tool_archive) as package:
                            tool.write_bytes(package.extractfile("package/bin/rcedit-x64.exe").read())
                        with TemporaryDirectory(prefix="peace-atlas-branding-") as branding_temp:
                            branded = Path(branding_temp) / "PEACE ATLAS.exe"
                            with source.open(entry) as data, branded.open("wb") as output:
                                shutil.copyfileobj(data, output, 1024 * 1024)
                            subprocess.run([str(tool), str(branded), "--set-icon", str(DESKTOP / "assets/icon.ico"),
                                            "--set-version-string", "ProductName", "PEACE ATLAS",
                                            "--set-version-string", "FileDescription", "PEACE ATLAS / 反戦研究室",
                                            "--set-product-version", VERSION, "--set-file-version", VERSION], check=True)
                            # Archive.file consumes the bytes before this owned temp is removed.
                            archive.file(branded, name, 0o100755)
                        continue
            if name.endswith("/resources/default_app.asar") or name.endswith("/Resources/default_app.asar"):
                continue
            with source.open(entry) as data:
                archive.add(name, data, source_info=entry)


def append_python(archive, runtime, resource_prefix):
    with tarfile.open(runtime, "r:gz") as source:
        for entry in source:
            name = safe_name(entry.name)
            if not name.startswith("python/"):
                raise RuntimeError(f"Unexpected Python archive root: {name}")
            if entry.isdir():
                continue
            destination = resource_prefix + name
            if entry.issym():
                if PurePosixPath(entry.linkname).is_absolute():
                    raise RuntimeError(f"Absolute runtime symlink: {name}")
                archive.add(destination, entry.linkname.encode("utf-8"), stat.S_IFLNK | 0o777)
            elif entry.isfile() or entry.islnk():
                data = source.extractfile(entry)
                if data is None:
                    raise RuntimeError(f"Cannot read runtime file: {name}")
                with data:
                    archive.add(destination, data, stat.S_IFREG | (entry.mode & 0o777))
            else:
                raise RuntimeError(f"Unsupported Python archive entry: {name}")


def append_wheels(archive, wheels, resource_prefix, target):
    site = "python/Lib/site-packages/" if target == "windows-x64" else "python/lib/python3.12/site-packages/"
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as source:
            for entry in source.infolist():
                if entry.is_dir():
                    continue
                name = safe_name(entry.filename)
                if ".data/" in name:
                    _, group, tail = name.split("/", 2)
                    if group not in {"purelib", "platlib"}:
                        continue  # CLI wrappers are unnecessary: backend starts with python.
                    name = tail
                with source.open(entry) as data:
                    archive.add(resource_prefix + site + name, data, source_info=entry)


def append_chromium(archive, runtime, resource_prefix):
    """Keep the official full browser, frameworks, links, locales and notices."""
    browser_prefix = resource_prefix + f"browsers/chromium-{CHROMIUM_REVISION}/"
    with zipfile.ZipFile(runtime) as source:
        for entry in source.infolist():
            if entry.is_dir():
                continue
            with source.open(entry) as data:
                archive.add(browser_prefix + safe_name(entry.filename), data, source_info=entry)
    archive.add(browser_prefix + "INSTALLATION_COMPLETE", b"")


def harness_files(target=None):
    """Ship exactly the audited public SDK/runtime closure, never run profiles."""
    base = DESKTOP / "harness"
    manifest_path = base / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("protocol") != 1 or manifest.get("dsh_runtime_version") != "0.2.0-rc.2"
            or manifest.get("dsh_sdk_version") != "0.2.0-rc.2"):
        raise RuntimeError("Harness runtime versions or protocol differ from the audited pin")
    if target and target not in manifest.get("native_targets_packaged", []):
        raise RuntimeError(f"Harness native dependency closure is not packaged for {target}")
    records = manifest.get("files", [])
    names = [record["path"] for record in records]
    if not records or len(names) != len(set(names)):
        raise RuntimeError("Harness closure manifest has missing or duplicate records")
    digest = hashlib.sha256(json.dumps(records, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
    if digest != manifest.get("closure_sha256"):
        raise RuntimeError("Harness dependency closure digest differs from its records")
    actual = {path.relative_to(base).as_posix() for path in base.rglob("*")
              if path.is_file() and path != manifest_path}
    if set(names) != actual:
        raise RuntimeError("Harness files differ from the audited closure manifest")
    files = [(manifest_path, "harness/runtime-manifest.json")]
    for record in records:
        relative = PurePosixPath(record["path"])
        if relative.is_absolute() or ".." in relative.parts or {"owned-runs", ".bin", "sessions"}.intersection(relative.parts):
            raise RuntimeError("Harness closure contains an unsafe/runtime-generated path")
        source = base / relative
        if not source.is_file() or sha256(source) != record["sha256"] or source.stat().st_size != record["size"]:
            raise RuntimeError(f"Harness runtime changed after its audit: {relative}")
        if target and target not in record.get("targets", []):
            continue
        files.append((source, "harness/" + relative.as_posix()))
    return files


def application_files(target=None):
    files = []
    for name in ("main.cjs", "preload.cjs", "restore.cjs", "package.json"):
        files.append((DESKTOP / name, "app/" + name))
    asset_directory = DESKTOP / "assets"
    if asset_directory.is_dir():
        for path in sorted(asset_directory.iterdir()):
            if path.is_file() and path.suffix in {".svg", ".png", ".ico", ".icns"}:
                files.append((path, "app/assets/" + path.name))
        if target and target.startswith("macos"):
            files.append((asset_directory / "icon.icns", "peace-atlas.icns"))
    for name in BACKEND_MODULES:
        files.append((ROOT / "analysis-worker/app" / name, "backend/app/" + name))
    for subtree in ("workbench", "static/workbench"):
        base = ROOT / "analysis-worker/app" / subtree
        allowed_suffixes = {".py"} if subtree == "workbench" else {".js", ".css", ".html", ".svg", ".png", ".webp", ".woff", ".woff2", ".json"}
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(base)
            if subtree == "workbench" and relative.parts[0] == "agent_assets":
                continue  # Public installation assets have their own exact path allowlist.
            font_license = relative.parts[0] == "fonts" and path.name.endswith("-OFL.txt") if subtree == "static/workbench" else False
            if path.is_file() and not {"__pycache__", "tests", "test"}.intersection(relative.parts) and (path.suffix in allowed_suffixes or font_license):
                files.append((path, "backend/app/" + subtree + "/" + path.relative_to(base).as_posix()))
    agent_assets = ROOT / "analysis-worker/app/workbench/agent_assets"
    for relative in AGENT_ASSET_PATHS:
        files.append((agent_assets / relative, "backend/app/workbench/agent_assets/" + relative))
    for name in CONFIG_FILES:
        files.append((ROOT / "analysis-worker/config" / name, "backend/config/" + name))
    files.append((DESKTOP / "backend_entry.py", "backend/backend_entry.py"))
    files.append((DESKTOP / "browser_smoke.py", "backend/browser_smoke.py"))
    files.extend(harness_files(target))
    for path, _ in files:
        if not path.is_file():
            raise FileNotFoundError(f"Required application source not ready: {path}")
    return files


def validate_application_manifest(archive, target, resource_prefix):
    """Require the whole current allowlist, including newly added modules."""
    sources = application_files(target)
    expected = {name: source for source, name in sources}
    if len(expected) != len(sources):
        raise RuntimeError("Duplicate application source destinations")
    manifest = json.loads(archive.read(resource_prefix + "build-manifest.json"))
    records = manifest["application_files"]
    recorded = [record["path"] for record in records]
    if len(recorded) != len(set(recorded)):
        raise RuntimeError("Duplicate application manifest paths")
    if set(recorded) != set(expected):
        raise RuntimeError(f"Application manifest differs from current source allowlist: missing={sorted(set(expected) - set(recorded))}, unexpected={sorted(set(recorded) - set(expected))}")
    members = archive.namelist()
    for record in records:
        name = resource_prefix + record["path"]
        if members.count(name) != 1:
            raise RuntimeError(f"Application archive member missing or duplicated: {record['path']}")
        actual = hashlib.sha256(archive.read(name)).hexdigest()
        if actual != record["sha256"]:
            raise RuntimeError(f"Source changed during packaging: {record['path']}; rebuild after edits finish")
        if sha256(expected[record["path"]]) != record["sha256"]:
            raise RuntimeError(f"Application source changed after its snapshot: {record['path']}; rebuild after edits finish")
    return len(records)


def validate_archive(path, target, resource_prefix):
    minimum_macos = None
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP integrity failure: {bad}")
        members = archive.namelist()
        names = set(members)
        expected = [resource_prefix + "backend/backend_entry.py", resource_prefix + "app/main.cjs", resource_prefix + "app/preload.cjs",
                    resource_prefix + "harness/sidecar.mjs", resource_prefix + "harness/runtime-manifest.json",
                    resource_prefix + "backend/app/workbench/server.py", resource_prefix + "backend/app/static/workbench/index.html"]
        expected.append(resource_prefix + ("python/python.exe" if target == "windows-x64" else "python/bin/python3.12"))
        browser_platform = CHROMIUM_TARGETS[target]
        browser_executable = ("chrome-win64/chrome.exe" if target == "windows-x64" else
            f"chrome-{browser_platform}/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
        expected.append(resource_prefix + f"browsers/chromium-{CHROMIUM_REVISION}/" + browser_executable)
        site = "python/Lib/site-packages/" if target == "windows-x64" else "python/lib/python3.12/site-packages/"
        expected.append(resource_prefix + site + "playwright/driver/package/browsers.json")
        expected.extend(resource_prefix + site + name for name in ("greenlet/__init__.py", "pyee/__init__.py"))
        driver_manifest = json.loads(archive.read(resource_prefix + site + "playwright/driver/package/browsers.json"))
        chromium = next(item for item in driver_manifest["browsers"] if item["name"] == "chromium")
        if chromium["revision"] != CHROMIUM_REVISION or chromium["browserVersion"] != CHROMIUM_VERSION:
            raise RuntimeError("Packaged Playwright/browser versions disagree")
        if target.startswith("macos"):
            main_executable = "PEACE ATLAS.app/Contents/MacOS/" + MAC_MAIN_EXECUTABLE
            main_plist = "PEACE ATLAS.app/Contents/Info.plist"
            if members.count(main_plist) != 1 or members.count(main_executable) != 1:
                raise RuntimeError("macOS main executable or plist missing or duplicated")
            plist = plistlib.loads(archive.read(main_plist))
            if plist.get("CFBundleExecutable") != MAC_MAIN_EXECUTABLE or "PEACE ATLAS.app/Contents/MacOS/Electron" in names:
                raise RuntimeError("macOS main executable and bundle metadata disagree")
            if not stat.S_ISREG(archive.getinfo(main_executable).external_attr >> 16):
                raise RuntimeError("macOS main executable must be a regular file")
            expected.append(main_executable)
            expected.append(resource_prefix + "python/bin/python3")
            minimum_macos = plist.get("LSMinimumSystemVersion")
            chromium_plist = resource_prefix + f"browsers/chromium-{CHROMIUM_REVISION}/chrome-{browser_platform}/Google Chrome for Testing.app/Contents/Info.plist"
            browser_minimum = plistlib.loads(archive.read(chromium_plist)).get("LSMinimumSystemVersion", "0")
            version_tuple = lambda value: tuple(map(int, value.split(".")))
            if version_tuple(minimum_macos) < version_tuple(browser_minimum):
                raise RuntimeError("Browser minimum macOS is higher than the application's declared minimum")
            for executable_name in (main_executable, resource_prefix + "python/bin/python3.12",
                                    resource_prefix + site + "playwright/driver/node",
                                    resource_prefix + f"browsers/chromium-{CHROMIUM_REVISION}/" + browser_executable):
                if not ((archive.getinfo(executable_name).external_attr >> 16) & stat.S_IXUSR):
                    raise RuntimeError(f"macOS executable bit missing: {executable_name}")
            if not any(stat.S_ISLNK(info.external_attr >> 16) for info in archive.infolist()):
                raise RuntimeError("macOS runtime symlinks missing")
        missing = set(expected) - names
        if missing:
            raise RuntimeError(f"Missing archive files: {sorted(missing)}")
        if any(PurePosixPath(name).name in {".env", "legacy_main.py"} or "japan_social_accounts_seed" in name for name in names):
            raise RuntimeError("Excluded private/legacy file found in bundle")
        if any("/backend/app/static/workbench/tests/" in name or "/backend/app/workbench/tests/" in name for name in names):
            raise RuntimeError("Application test files found in distribution")
        application_count = validate_application_manifest(archive, target, resource_prefix)
    return {"zip_crc": "passed", "entry_count": len(names), "required_files": "passed",
            "application_manifest_hashes": "passed",
            "application_manifest_completeness": "passed", "application_file_count": application_count,
            "macos_symlinks_and_modes": "passed" if target.startswith("macos") else "not_applicable",
            "macos_main_executable": MAC_MAIN_EXECUTABLE if target.startswith("macos") else None,
            "minimum_macos": minimum_macos, "native_runtime_test": "not_run"}


def build_target(target, assets, wheels, build_stamp, mac_signing="native-finalizer", wsl_distribution=None):
    DIST.mkdir(parents=True, exist_ok=True)
    artifact_base = f"PEACE-ATLAS-{VERSION}-{target}{ARTIFACT_SUFFIX}"
    # Unique Windows roots prevent stale files from surviving an incremental rebuild.
    portable_name = artifact_base + "-" + build_stamp
    prefix = portable_name + "/" if target == "windows-x64" else "PEACE ATLAS.app/"
    resources = prefix + ("resources/" if target == "windows-x64" else "Contents/Resources/")
    output = DIST / (artifact_base + ".zip")
    partial = output.with_suffix(".zip.part")
    archive = Archive(partial)
    files = application_files(target)
    manifest = {"product": "PEACE ATLAS", "version": VERSION, "target": target,
                "created_at": datetime.now(timezone.utc).isoformat(), "electron_version": ELECTRON_VERSION,
                "python_version": PYTHON_VERSION, "python_release": PYTHON_RELEASE, "wheel_platform": TARGETS[target]["platform"],
                "release_scope": "owned DSH SDK/runtime with read-only evidence tools and host-controlled model relay; checkpointed full-source reading, bounded exact-quote correction and adjustable report continuation; saved account identity projection and retained profile-link labels; native platform and actual model acceptance are recorded separately",
                "playwright_version": PLAYWRIGHT_VERSION, "chromium_revision": CHROMIUM_REVISION,
                "chromium_version": CHROMIUM_VERSION, "browser_runtime": "full Chrome for Testing",
                "chromium_checksum_provenance": "local SHA-256 pinned after official Google object MD5 verification",
                "runtimes": {kind: {"name": Path(value["path"]).name, "sha256": value["sha256"], "url": value["url"]} for kind, value in assets.items()},
                "wheels": [{"name": wheel.name, "sha256": sha256(wheel)} for wheel in wheels],
                "application_files": [{"path": name, "sha256": sha256(path)} for path, name in files],
                "signing": ("ad-hoc signed with apple-codesign 0.29.0; native macOS verification pending" if mac_signing == "wsl" else
                            "native macOS finalization required; run the included finalizer before launch validation") if target.startswith("macos") else "unsigned portable"}
    harness_manifest = json.loads((DESKTOP / "harness/runtime-manifest.json").read_text(encoding="utf-8"))
    manifest["harness_runtime"] = {"runtime": harness_manifest["dsh_runtime_version"],
        "sdk": harness_manifest["dsh_sdk_version"], "protocol": harness_manifest["protocol"],
        "profile": harness_manifest["profile"], "tool_names": harness_manifest["tool_names"],
        "audited_closure_sha256": harness_manifest["closure_sha256"],
        "packaged_file_count": sum(name.startswith("harness/") for _, name in files),
        "native_target": target, "macos_native_test": harness_manifest["macos_native_test"]}
    log(f"Assembling {target} ({len(files)} application files)")
    try:
        append_electron(archive, assets["electron"]["path"], target, prefix)
        append_python(archive, assets["python"]["path"], resources)
        append_wheels(archive, wheels, resources, target)
        append_chromium(archive, assets["chromium"]["path"], resources)
        for path, name in files:
            archive.file(path, resources + name)
        archive.add(resources + "build-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        if target.startswith("macos"):
            archive.add("Finalize PEACE ATLAS.command", MAC_FINALIZE.replace("@TARGET@", target).replace("@ARTIFACT_SUFFIX@", ARTIFACT_SUFFIX).encode("utf-8"), stat.S_IFREG | 0o755)
            archive.add("READ BEFORE LAUNCH.txt", (
                "PEACE ATLAS - macOS application\n\n"
                + ("This bundle is ad-hoc signed; native macOS verification and launch still await your acceptance test.\n"
                   if mac_signing == "wsl" else "This bundle requires native macOS signature finalization before it can be considered launch-ready.\n") +
                "Double-click the included 'Finalize PEACE ATLAS.command' on macOS. No terminal typing is needed.\n"
                "For automated use, execute:\n"
                "  bash './Finalize PEACE ATLAS.command' --smoke\n\n"
                "The script ad-hoc signs nested code, verifies the app signature, tests the native\n"
                "desktop/backend with temporary data, writes a new -adhoc.zip, and opens the app. It does not modify\n"
                "Gatekeeper settings or quarantine attributes. No Developer ID signature or notarization\n"
                "has been applied. Native Mac verification is not claimed by the cross-platform build.\n"
                "This Apple Silicon build requires macOS 13 or later.\n").encode("utf-8"))
    finally:
        archive.close()
    signing = None
    if target.startswith("macos") and mac_signing == "wsl":
        signing = sign_macos_archive(partial, target, wsl_distribution)
    validation = validate_archive(partial, target, resources)
    if signing:
        validation["cross_platform_signing"] = signing
    os.replace(partial, output)
    portable_path = None
    if target == "windows-x64":
        with zipfile.ZipFile(output) as source:
            for entry in source.infolist():
                path = DIST.joinpath(*PurePosixPath(safe_name(entry.filename)).parts)
                if not path.resolve().is_relative_to(DIST.resolve()):
                    raise RuntimeError("Portable extraction escaped dist")
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as data, path.open("wb") as destination:
                    shutil.copyfileobj(data, destination, 1024 * 1024)
        portable_path = str(DIST / portable_name)
    result = {"target": target, "archive": str(output), "size_bytes": output.stat().st_size,
              "sha256": sha256(output), "portable_directory": portable_path, "validation": validation}
    (DIST / (artifact_base + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    log(json.dumps(result, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["all", *TARGETS], default="all")
    parser.add_argument("--prepare-only", action="store_true", help="Download verified runtimes/wheels without packaging application code")
    parser.add_argument("--mac-signing", choices=["native-finalizer", "wsl"], default="native-finalizer")
    parser.add_argument("--wsl-distribution", help="Existing WSL distro used for optional ad-hoc macOS signing; never installed or reconfigured")
    args = parser.parse_args()
    if args.mac_signing == "wsl" and not args.wsl_distribution:
        parser.error("--mac-signing wsl requires an explicitly selected --wsl-distribution")
    targets = list(DEFAULT_TARGETS) if args.target == "all" else [args.target]
    assets = acquire_assets(targets)
    wheels = {target: prepare_wheels(target) for target in targets}
    if args.prepare_only:
        log("Runtime assets and dependencies ready; application files were not packaged.")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results = [build_target(target, assets[target], wheels[target], stamp, args.mac_signing, args.wsl_distribution) for target in targets]
    source_versions = {}
    for result in results:
        with zipfile.ZipFile(result["archive"]) as archive:
            manifest_name = next(name for name in archive.namelist() if name.endswith("/resources/build-manifest.json") or name.endswith("/Resources/build-manifest.json"))
            manifest = json.loads(archive.read(manifest_name))
            for record in manifest["application_files"]:
                previous = source_versions.setdefault(record["path"], record["sha256"])
                if previous != record["sha256"]:
                    raise RuntimeError(f"Source changed between platform builds: {record['path']}; rebuild all targets after edits finish")
    (DIST / ("desktop-build-results" + ARTIFACT_SUFFIX + ".json")).write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    main()
