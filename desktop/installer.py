"""Construct installers from an explicitly accepted frozen desktop package.

No application/runtime download, user-data migration, signing or installation is
performed here. Native acceptance and publication remain separate operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

DESKTOP = Path(__file__).resolve().parent
PRODUCT = "PEACE ATLAS"
INSTALLER_FORMAT = 1
NSIS_VERSION = "21-Jun-2026.cvs"  # Actual maintained nsis@2.0.1 Windows compiler.
NSIS_BUNDLE_URL = "https://github.com/electron-userland/electron-builder-binaries/releases/download/nsis%402.0.1/nsis-bundle-3.12.tar.gz"
NSIS_BUNDLE_SHA256 = "fe36a357f3a220db893498e830fd80e0769768a18a99f4e4d2447b982538feed"
NSIS_COMPILER_SHA256 = "f641c43e16078b556b85b32b336096f464e26008a1a62670ae8cfbc6df9d5c08"
MACHO_MAGICS = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative_name(value):
    if not isinstance(value, str) or not value or any(c in value for c in '\\:$"\r\n\x00`'):
        raise ValueError("Unsafe installer member")
    name = PurePosixPath(value)
    if name.is_absolute() or any(part in {".", "..", ""} for part in value.split("/")):
        raise ValueError("Unsafe installer member")
    if any(part.endswith((".", " ")) or part.split(".")[0].upper() in
           {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
           for part in name.parts):
        raise ValueError("Unsafe Windows installer member")
    return name.as_posix()


def checked_json(path, expected):
    if not re.fullmatch(r"[0-9a-f]{64}", expected or "") or digest(path) != expected:
        raise ValueError("Pinned input SHA-256 differs")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def inventory(root, *, mac=False):
    root = Path(root).resolve(strict=True)
    records = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            info = path.lstat()
            name = relative_name(path.relative_to(root).as_posix())
            if stat.S_ISLNK(info.st_mode):
                if not mac or not path.resolve(strict=True).is_relative_to(root):
                    raise ValueError("Installer link escapes payload or is not allowed")
                records.append({"path": name, "link": os.readlink(path)})
            elif getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError("Installer reparse point is not allowed")
            elif stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                records.append({"path": name, "sha256": digest(path), "size": info.st_size})
            else:
                raise ValueError("Nonregular installer payload")
    if len({r["path"].casefold() for r in records}) != len(records):
        raise ValueError("Case-colliding installer payload")
    return sorted(records, key=lambda r: r["path"])


def validate_payload(package, freeze_path, freeze_sha, archive, archive_sha, *, target="windows-x64", native_signed_files=()):
    """Bind all application sources and every runtime member to accepted ZIP bytes."""
    freeze = checked_json(freeze_path, freeze_sha)
    if digest(archive) != archive_sha:
        raise ValueError("Accepted package archive changed")
    package = Path(package).resolve(strict=True)
    mac = target.startswith("macos-")
    signed = set(native_signed_files)
    if len(signed) != len(native_signed_files) or (signed and not mac):
        raise ValueError("Invalid native signing allowance")
    resources = package / ("Contents/Resources" if mac else "resources")
    manifest_path = resources / "build-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("product") != PRODUCT or manifest.get("target") != target:
        raise ValueError("Unexpected product or target")
    version = manifest.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Unexpected product version")
    expected = {name: freeze["files"][name]["sha256"] for name in freeze["target_files"][target]}
    rows = manifest.get("application_files", [])
    actual = {relative_name(row["path"]): row["sha256"] for row in rows}
    if len(rows) != len(actual) or actual != expected or len(actual) != freeze["targets"][target]:
        raise ValueError("Application manifest differs from source freeze")
    changed_signed = set()
    for name, sha in actual.items():
        path = resources.joinpath(*PurePosixPath(name).parts)
        if digest(path) != sha:
            with path.open("rb") as stream:
                magic = stream.read(4)
            if name not in signed or magic not in MACHO_MAGICS:
                raise ValueError("Frozen application payload changed")
            changed_signed.add(name)
    if changed_signed != signed:
        raise ValueError("Native signing allowance is not exact")
    records = inventory(package, mac=mac)
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        names = [entry.filename for entry in members]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate accepted archive member")
        prefix = "PEACE ATLAS.app/" if mac else package.name + "/"
        recorded = {r["path"]: r for r in records}
        archive_names = set()
        for entry in members:
            name = relative_name(entry.filename.rstrip("/"))
            if entry.is_dir() and name == prefix.rstrip("/"):
                continue
            if mac and name.startswith("__MACOSX/PEACE ATLAS.app/"):
                # ditto --sequesterRsrc AppleDouble metadata is tied to an actual
                # application member; it is never treated as executable payload.
                if entry.is_dir():
                    continue
                metadata = PurePosixPath(name).relative_to("__MACOSX/PEACE ATLAS.app")
                if not metadata.name.startswith("._"):
                    raise ValueError("Unexpected macOS archive metadata")
                original = metadata.with_name(metadata.name[2:])
                if not package.joinpath(*original.parts).exists() or source.read(entry)[:4] != b"\x00\x05\x16\x07":
                    raise ValueError("Unbound macOS AppleDouble metadata")
                continue
            if not name.startswith(prefix):
                if mac and name in {"Finalize PEACE ATLAS.command", "READ BEFORE LAUNCH.txt"}:
                    continue
                raise ValueError("Accepted archive member outside application")
            if entry.is_dir():
                continue
            relative = name[len(prefix):]
            archive_names.add(relative)
            row = recorded.get(relative)
            if not row:
                raise ValueError("Accepted archive payload missing")
            if "link" in row:
                if source.read(entry).decode("utf-8") != row["link"]:
                    raise ValueError("Accepted archive symlink differs")
            elif hashlib.sha256(source.read(entry)).hexdigest() != row["sha256"]:
                raise ValueError("Accepted archive file differs")
        if archive_names != set(recorded):
            raise ValueError("Portable has unaccepted extra files")
    return {"installer_format": INSTALLER_FORMAT, "product": PRODUCT, "version": version,
            "target": target, "source_freeze_sha256": freeze_sha, "archive_sha256": archive_sha,
            "application_manifest_sha256": digest(manifest_path), "files": records,
            "native_signed_application_files": sorted(signed),
            "preserve_user_data": True, "automatic_launch": False}


def validate_acceptance(path, sha, payload):
    """Explicit handoff, with pinned independent package/native evidence files."""
    acceptance = checked_json(path, sha)
    for key in ("source_freeze_sha256", "archive_sha256", "application_manifest_sha256", "target"):
        if acceptance.get(key) != payload[key]:
            raise ValueError("Installer acceptance does not bind this package")
    if acceptance.get("installer_input_format") != 1 or acceptance.get("package_audit_passed") is not True or acceptance.get("native_passed") is not True:
        raise ValueError("Independent package and native acceptance required")
    receipts = acceptance.get("receipts", [])
    if len(receipts) != 2 or {r.get("kind") for r in receipts} != {"package_audit", "native"}:
        raise ValueError("Pinned package and native evidence required")
    for row in receipts:
        receipt_path = Path(path).parent / row["path"]
        if not receipt_path.resolve(strict=True).is_relative_to(Path(path).parent.resolve(strict=True)):
            raise ValueError("Acceptance evidence escaped handoff directory")
        proof = checked_json(receipt_path, row["sha256"])
        if proof.get("ok") is not True:
            raise ValueError("Acceptance receipt is not passed")
        if row["kind"] == "native" and payload["native_signed_application_files"]:
            if proof.get("native_codesign") is not True or proof.get("frozen_non_macho_application_unchanged") is not True or sorted(proof.get("application_macho_hashes_changed_by_signing", [])) != payload["native_signed_application_files"]:
                raise ValueError("Native signing changes are not bound by native acceptance")
    return acceptance


# This guard is embedded in the installer AND uninstaller, never executed from
# a mutable application directory. It never reads application data/configuration.
WINDOWS_GUARD = r'''param([string]$Mode, [string]$InstallRoot, [string]$Manifest, [string]$ExpectedManifest,
 [string]$Instance, [string]$AllowedRoot, [string]$RegistryKey, [string]$ProcessName)
$ErrorActionPreference = 'Stop'
function FileSha([string]$Path) {
 $stream = [IO.File]::OpenRead($Path)
 $algorithm = [Security.Cryptography.SHA256]::Create()
 try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
 finally { $stream.Dispose(); $algorithm.Dispose() }
}
try {
 $root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
 $allowed = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\')
 if ($root -ine $allowed -or $root.Length -lt 12) { throw 'Install path is not the fixed application root.' }
 $cursor = $root
 while ($cursor) {
  if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -Force -LiteralPath $cursor).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Reparse path rejected.' }
  $parent = [IO.Directory]::GetParent($cursor); if (!$parent) { break }; $cursor = $parent.FullName
 }
 if ((FileSha $Manifest) -ine $ExpectedManifest) { throw 'Installer manifest changed.' }
 $payload = Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json
 if ($payload.installer_format -ne 1 -or $payload.product -ne 'PEACE ATLAS') { throw 'Invalid application manifest.' }
 if (Get-Process -Name $ProcessName -ErrorAction SilentlyContinue) { throw 'PEACE ATLAS is running. Close the application and wait for its background work to finish before continuing.' }
 $marker = Join-Path $root 'installation-state.json'
 $registry = 'HKCU:\' + $RegistryKey
 if ($Mode -eq 'install') {
  if ((Test-Path -LiteralPath $root) -and @(Get-ChildItem -Force -LiteralPath $root).Count -gt 0) {
   if (!(Test-Path -LiteralPath $marker)) { throw 'Nonempty unowned application directory.' }
   $state = Get-Content -Raw -LiteralPath $marker | ConvertFrom-Json
   if ($state.instance -ne $Instance -or $state.root -ine $root) { throw 'Installation ownership differs.' }
   if (!(Test-Path -LiteralPath $registry) -or (Get-ItemProperty -LiteralPath $registry).InstallLocation -ine $root) { throw 'Registered installation ownership differs.' }
  }
 } elseif ($Mode -eq 'record') {
  @{instance=$Instance; root=$root; manifest_sha256=$ExpectedManifest} | ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding UTF8
 } elseif ($Mode -eq 'verify') {
  foreach ($file in $payload.files) {
   $path = Join-Path $root $file.path
   if ((Get-Item -Force -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Payload reparse point rejected.' }
   if ((FileSha $path) -ine $file.sha256) { throw 'Installed application integrity failed.' }
  }
 } elseif ($Mode -eq 'uninstall') {
  $state = Get-Content -Raw -LiteralPath $marker | ConvertFrom-Json
  if ($state.instance -ne $Instance -or $state.root -ine $root -or $state.manifest_sha256 -ine $ExpectedManifest) { throw 'Uninstall ownership differs.' }
  if (!(Test-Path -LiteralPath $registry) -or (Get-ItemProperty -LiteralPath $registry).InstallLocation -ine $root) { throw 'Registered uninstall path differs.' }
  foreach ($file in $payload.files) {
   $path = Join-Path $root $file.path
   $cursor = $path
   while ($cursor -and $cursor.Length -ge $root.Length) {
    if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -Force -LiteralPath $cursor).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Uninstall reparse point rejected.' }
    $parent = [IO.Directory]::GetParent($cursor); if (!$parent) { break }; $cursor = $parent.FullName
   }
  }
 } else { throw 'Unknown installer operation.' }
 exit 0
} catch { Write-Output $_.Exception.Message; exit 10 }
'''


def nsis_string(value):
    return str(value).replace("$", "$$").replace('"', '$\\"').replace("\r", "").replace("\n", "")


def prepare_windows(package, payload, workspace, output, *, test_root=None, test_instance=None):
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    if (test_root is None) != (test_instance is None):
        raise ValueError("Test root and test namespace must both be explicit")
    if test_instance and not re.fullmatch(r"installer-test-[a-z0-9-]{6,48}", test_instance):
        raise ValueError("Unsafe test namespace")
    instance = test_instance or "PEACE ATLAS"
    if test_root:
        test_root = Path(test_root).resolve()
        if not test_root.is_relative_to(workspace.parent) or test_root == workspace.parent:
            raise ValueError("Test install root must be owned by staging parent")
        if test_root.exists():
            state_path = test_root / "installation-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8-sig")) if state_path.is_file() else {}
            if state.get("instance") != instance or Path(state.get("root", "")).resolve() != test_root:
                raise ValueError("Existing test root has no matching installer ownership")
    data = {**payload, "instance": instance, "test_fixture": bool(test_root)}
    manifest = workspace / "installer-manifest.json"
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (workspace / "installer-guard.ps1").write_text(WINDOWS_GUARD, encoding="utf-8-sig")
    registry = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + instance
    definitions = {"OUTPUT": Path(output).resolve(), "PRODUCT_VERSION": payload["version"], "INSTANCE": instance,
                   "REGISTRY_KEY": registry, "STAGING": workspace, "MANIFEST_SHA": digest(manifest),
                   "PROCESS_NAME": instance if test_root else PRODUCT}
    lines = [f'!define {key} "{nsis_string(value)}"' for key, value in definitions.items()]
    if test_root:
        lines += [f'!define TEST_ROOT "{nsis_string(test_root)}"', f'!define TEST_SHORTCUTS "{nsis_string(workspace.parent / (instance + "-shortcuts"))}"']
    (workspace / "settings.nsh").write_text("\n".join(lines) + "\n", encoding="utf-8")
    installs, removes, directories = [], [], set()
    for row in payload["files"]:
        name = relative_name(row["path"])
        if name.casefold() in {"uninstall.exe", "installation-state.json", "installer-manifest.json"}:
            raise ValueError("Payload collides with installer ownership files")
        path = Path(package).joinpath(*PurePosixPath(name).parts)
        parent = str(PurePosixPath(name).parent).replace("/", "\\")
        installs += [f'SetOutPath "$INSTDIR\\{nsis_string(parent)}"', f'File "{nsis_string(path)}"']
        removes.append(f'Delete "$INSTDIR\\{nsis_string(name.replace(chr(47), chr(92)))}"')
        directories.update(str(p).replace("/", "\\") for p in PurePosixPath(name).parents if str(p) != ".")
    removes += [f'RMDir "$INSTDIR\\{nsis_string(d)}"' for d in sorted(directories, key=lambda p: (-p.count("\\"), p))]
    (workspace / "payload.nsh").write_text("\n".join(installs) + "\n", encoding="utf-8")
    (workspace / "remove.nsh").write_text("\n".join(removes) + "\n", encoding="utf-8")
    shutil.copy2(DESKTOP / "installer.nsi", workspace / "installer.nsi")
    return {"workspace": str(workspace), "manifest_sha256": digest(manifest), "instance": instance,
            "registry_key": registry, "test_fixture": bool(test_root)}


def compile_windows(prepared, compiler, compiler_sha):
    compiler = Path(compiler).resolve(strict=True)
    if digest(compiler) != compiler_sha:
        raise ValueError("Pinned NSIS compiler differs")
    environment = dict(os.environ, NSISDIR=str(compiler.parent))
    version = subprocess.check_output([str(compiler), "/VERSION"], text=True, env=environment).strip()
    if version != "v" + NSIS_VERSION:
        raise ValueError("Unexpected NSIS compiler version")
    subprocess.run([str(compiler), "/NOCONFIG", "/V3", str(Path(prepared["workspace"]) / "installer.nsi")], check=True, timeout=1800, env=environment)


MAC_PREINSTALL = '''#!/bin/sh
set -eu
if /usr/bin/pgrep -x "PEACE ATLAS" >/dev/null 2>&1; then
 echo "PEACE ATLAS is running. Close it before installing this update." >&2
 exit 1
fi
# Only the application payload is installed; user Library data is untouched.
exit 0
'''


def prepare_macos(package, payload, workspace, output):
    if not payload["target"].startswith("macos-") or Path(package).name != "PEACE ATLAS.app":
        raise ValueError("Expected accepted macOS application")
    info = plistlib.loads((Path(package) / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "org.peaceatlas.workbench" or info.get("CFBundleExecutable") != "PEACE ATLAS":
        raise ValueError("Unexpected macOS application identity")
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    scripts = workspace / "scripts"
    scripts.mkdir()
    (scripts / "preinstall").write_text(MAC_PREINSTALL, encoding="utf-8")
    (scripts / "preinstall").chmod(0o755)
    components = [{"RootRelativeBundlePath": "PEACE ATLAS.app", "BundleIsRelocatable": False,
                   "BundleHasStrictIdentifier": True, "BundleOverwriteAction": "upgrade"}]
    (workspace / "components.plist").write_bytes(plistlib.dumps(components))
    resources = workspace / "resources"
    resources.mkdir()
    (resources / "welcome.html").write_text("<html><body><h1>PEACE ATLAS</h1><p>Installs the application into /Applications. Close PEACE ATLAS before updating. Research, profiles and configuration in your Library are preserved.</p><p>This installer is unsigned. The application has an ad-hoc integrity signature; Developer ID signing and Apple notarization have not been performed. macOS may require explicit approval to open it.</p></body></html>", encoding="utf-8")
    (workspace / "distribution.xml").write_text('''<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2"><title>PEACE ATLAS</title><welcome file="welcome.html"/>
<options customize="never" require-scripts="false"/><domains enable_anywhere="false" enable_currentUserHome="false" enable_localSystem="true"/>
<choices-outline><line choice="app"/></choices-outline><choice id="app" visible="false"><pkg-ref id="org.peaceatlas.workbench"/></choice>
<pkg-ref id="org.peaceatlas.workbench" version="''' + payload["version"] + '''" onConclusion="none">component.pkg</pkg-ref></installer-gui-script>
''', encoding="utf-8")
    return {"workspace": str(workspace), "package": str(Path(package).resolve()), "output": str(Path(output).resolve()), "version": payload["version"],
            "installer_signature": "unsigned", "application_signature": "native ad-hoc (must be verified)", "notarization": "not performed"}


def compile_macos(prepared):
    if platform.system() != "Darwin":
        raise RuntimeError("Native macOS pkgbuild/productbuild and signature verification required")
    workspace, app = Path(prepared["workspace"]), Path(prepared["package"])
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True, timeout=180)
    payload = workspace / "payload"
    payload.mkdir()
    subprocess.run(["/usr/bin/ditto", str(app), str(payload / app.name)], check=True, timeout=300)
    subprocess.run(["/usr/bin/pkgbuild", "--root", str(payload), "--install-location", "/Applications", "--component-plist", str(workspace / "components.plist"),
                    "--scripts", str(workspace / "scripts"), "--identifier", "org.peaceatlas.workbench", "--version", prepared["version"], str(workspace / "component.pkg")], check=True, timeout=1800)
    subprocess.run(["/usr/bin/productbuild", "--distribution", str(workspace / "distribution.xml"), "--resources", str(workspace / "resources"),
                    "--package-path", str(workspace), prepared["output"]], check=True, timeout=1800)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("package", "archive", "archive-sha", "freeze", "freeze-sha", "acceptance", "acceptance-sha", "workspace", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--target", choices=("windows-x64", "macos-arm64", "macos-x64"), required=True)
    parser.add_argument("--compiler")
    parser.add_argument("--compiler-sha")
    args = parser.parse_args()
    acceptance = checked_json(args.acceptance, args.acceptance_sha)
    payload = validate_payload(args.package, args.freeze, args.freeze_sha, args.archive, args.archive_sha,
                               target=args.target, native_signed_files=acceptance.get("native_signed_application_files", []))
    validate_acceptance(args.acceptance, args.acceptance_sha, payload)
    if Path(args.output).exists():
        raise ValueError("Installer output must be new")
    if args.target == "windows-x64":
        prepared = prepare_windows(args.package, payload, args.workspace, args.output)
        compile_windows(prepared, args.compiler, args.compiler_sha)
    else:
        prepared = prepare_macos(args.package, payload, args.workspace, args.output)
        compile_macos(prepared)
    result = {"ok": True, "target": args.target, "source_freeze_sha256": args.freeze_sha,
              "accepted_archive_sha256": args.archive_sha, "acceptance_sha256": args.acceptance_sha,
              "installer_sha256": digest(args.output), "installer_size": Path(args.output).stat().st_size,
              "installer_builder_sha256": digest(__file__), "nsis_template_sha256": digest(DESKTOP / "installer.nsi"),
              "installed_or_native_launch_tested": False, "automatic_launch": False, "user_data_modified": False,
              "signing": "unsigned installer", "notarization": "not performed"}
    Path(args.output + ".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
