"""Source/manifest safety checks; no runtimes are downloaded or repackaged."""
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings
import zipfile


SPEC = importlib.util.spec_from_file_location("desktop_build", Path(__file__).parents[1] / "build.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="peace-atlas-manifest-test-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.sources = []
        self.payloads = {"app/main.cjs": b"launcher", "backend/app/workbench/backup.py": b"backup-service"}
        for index, (name, data) in enumerate(self.payloads.items()):
            source = root / str(index)
            source.write_bytes(data)
            self.sources.append((source, name))
        self.records = [{"path": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in self.payloads.items()]

    @contextmanager
    def archive(self, *, records=None, payloads=None, duplicate=None):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("resources/build-manifest.json", json.dumps({"application_files": records if records is not None else self.records}))
            for name, value in (payloads if payloads is not None else self.payloads).items():
                archive.writestr("resources/" + name, value)
            if duplicate:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr("resources/" + duplicate, self.payloads[duplicate])
        with zipfile.ZipFile(data) as archive, patch.object(BUILD, "application_files", return_value=self.sources):
            yield archive

    def validate(self, archive):
        return BUILD.validate_application_manifest(archive, "windows-x64", "resources/")

    def test_complete_current_allowlist_passes(self):
        with self.archive() as archive:
            self.assertEqual(self.validate(archive), 2)

    def test_new_backup_module_omitted_from_manifest_fails(self):
        with self.archive(records=self.records[:1]) as archive:
            with self.assertRaisesRegex(RuntimeError, "missing=.*backup.py"):
                self.validate(archive)

    def test_manifested_source_missing_from_archive_fails(self):
        with self.archive(payloads={"app/main.cjs": b"launcher"}) as archive:
            with self.assertRaisesRegex(RuntimeError, "member missing or duplicated"):
                self.validate(archive)

    def test_duplicate_manifest_paths_fail(self):
        with self.archive(records=self.records + self.records[:1]) as archive:
            with self.assertRaisesRegex(RuntimeError, "Duplicate application manifest"):
                self.validate(archive)

    def test_duplicate_archive_members_fail(self):
        with self.archive(duplicate="app/main.cjs") as archive:
            with self.assertRaisesRegex(RuntimeError, "member missing or duplicated"):
                self.validate(archive)

    def test_manifest_cannot_admit_a_connection_file(self):
        extra = {"path": "app/connection.secure", "sha256": "0" * 64}
        with self.archive(records=self.records + [extra]) as archive:
            with self.assertRaisesRegex(RuntimeError, "unexpected=.*connection.secure"):
                self.validate(archive)

    def test_changed_packaged_bytes_fail(self):
        with self.archive(payloads={**self.payloads, "app/main.cjs": b"changed"}) as archive:
            with self.assertRaisesRegex(RuntimeError, "Source changed during packaging"):
                self.validate(archive)

    def test_changed_source_after_snapshot_fails(self):
        with self.archive() as archive:
            self.sources[0][0].write_bytes(b"changed after snapshot")
            with self.assertRaisesRegex(RuntimeError, "source changed after its snapshot"):
                self.validate(archive)

    def test_duplicate_source_destinations_fail(self):
        with self.archive() as archive:
            self.sources.append(self.sources[0])
            with self.assertRaisesRegex(RuntimeError, "Duplicate application source"):
                self.validate(archive)


class WindowsBrandingLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="peace-atlas-branding-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cache = self.root / "cache"
        self.desktop = self.root / "desktop"
        (self.desktop / "assets").mkdir(parents=True)
        (self.desktop / "assets/icon.ico").write_bytes(b"test-icon")
        self.legacy = self.cache / "windows-branding" / "legacy-owned-elsewhere" / "PEACE ATLAS.exe"
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_bytes(b"preserve-existing-cache")
        self.runtime = self.root / "electron.zip"
        with zipfile.ZipFile(self.runtime, "w") as runtime:
            runtime.writestr("electron.exe", b"unbranded-electron")
            runtime.writestr("runtime.dll", b"runtime-library")
            runtime.writestr("resources/default_app.asar", b"exclude-default-app")
        self.editor_archive = self.root / "rcedit.tgz"
        with tarfile.open(self.editor_archive, "w:gz") as tools:
            member = tarfile.TarInfo("package/bin/rcedit-x64.exe")
            member.size = len(b"editor-fixture")
            tools.addfile(member, io.BytesIO(b"editor-fixture"))
        self.edited_paths = []

    @contextmanager
    def build_environment(self, editor):
        with patch.object(BUILD, "CACHE", self.cache), \
             patch.object(BUILD, "DESKTOP", self.desktop), \
             patch.object(BUILD, "os", SimpleNamespace(name="nt")), \
             patch.object(BUILD, "download", return_value=self.editor_archive), \
             patch.object(BUILD.subprocess, "run", side_effect=editor):
            yield

    def editor_succeeds(self, command, *, check):
        self.assertTrue(check)
        executable = Path(command[1])
        self.assertEqual(executable.read_bytes(), b"unbranded-electron")
        self.edited_paths.append(executable)
        executable.write_bytes(b"branded-electron-fixture")

    def assert_owned_temp_cleaned(self):
        self.assertEqual(len(self.edited_paths), 1)
        self.assertFalse(self.edited_paths[0].parent.exists(), "owned branding temporary directory leaked")
        self.assertEqual(self.legacy.read_bytes(), b"preserve-existing-cache")

    def test_success_preserves_archive_bytes_and_cleans_owned_temp(self):
        output = self.root / "result.zip"
        archive = BUILD.Archive(output)
        try:
            with self.build_environment(self.editor_succeeds):
                BUILD.append_electron(archive, self.runtime, "windows-x64", "portable/")
        finally:
            archive.close()
        with zipfile.ZipFile(output) as packaged:
            self.assertEqual(packaged.read("portable/PEACE ATLAS.exe"), b"branded-electron-fixture")
            self.assertEqual(packaged.read("portable/runtime.dll"), b"runtime-library")
            self.assertNotIn("portable/resources/default_app.asar", packaged.namelist())
        self.assert_owned_temp_cleaned()

    def test_editor_failure_cleans_owned_temp_and_preserves_existing_cache(self):
        def editor_fails(command, *, check):
            self.assertTrue(check)
            executable = Path(command[1])
            self.assertEqual(executable.read_bytes(), b"unbranded-electron")
            self.edited_paths.append(executable)
            raise subprocess.CalledProcessError(7, command)

        archive = BUILD.Archive(self.root / "failed.zip")
        try:
            with self.build_environment(editor_fails):
                with self.assertRaises(subprocess.CalledProcessError):
                    BUILD.append_electron(archive, self.runtime, "windows-x64", "portable/")
        finally:
            archive.close()
        self.assert_owned_temp_cleaned()

    def test_archive_failure_also_cleans_owned_temp(self):
        archive = BUILD.Archive(self.root / "write-failed.zip")
        try:
            with self.build_environment(self.editor_succeeds), \
                 patch.object(archive, "file", side_effect=OSError("simulated archive write failure")):
                with self.assertRaisesRegex(OSError, "simulated archive write failure"):
                    BUILD.append_electron(archive, self.runtime, "windows-x64", "portable/")
        finally:
            archive.close()
        self.assert_owned_temp_cleaned()


class AgentAssetPackagingTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="peace-atlas-agent-packaging-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.desktop = self.root / "desktop"
        self.desktop.mkdir()
        for name in ("main.cjs", "preload.cjs", "restore.cjs", "package.json", "backend_entry.py", "browser_smoke.py"):
            (self.desktop / name).write_text("fixture " + name, encoding="utf-8")
        backend = self.root / "analysis-worker/app"
        backend.mkdir(parents=True)
        for name in BUILD.BACKEND_MODULES:
            (backend / name).write_text("fixture " + name, encoding="utf-8")
        config = self.root / "analysis-worker/config"
        config.mkdir()
        for name in BUILD.CONFIG_FILES:
            (config / name).write_text("fixture " + name, encoding="utf-8")
        self.assets = backend / "workbench/agent_assets"
        self.asset_payloads = {}
        for relative in BUILD.AGENT_ASSET_PATHS:
            path = self.assets / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = ("public installer fixture: " + relative).encode("utf-8")
            path.write_bytes(data)
            self.asset_payloads[relative] = data
        (backend / "workbench/agent_api.py").write_text("public API module", encoding="utf-8")

    @contextmanager
    def build_environment(self):
        with patch.object(BUILD, "ROOT", self.root), patch.object(BUILD, "DESKTOP", self.desktop), \
             patch.object(BUILD, "harness_files", return_value=[]):
            yield

    def test_public_skill_and_cli_bytes_are_in_the_application_manifest(self):
        for target in BUILD.DEFAULT_TARGETS:
            with self.subTest(target=target), self.build_environment():
                sources = BUILD.application_files(target)
                names = [name for _, name in sources]
                expected = {"backend/app/workbench/agent_assets/" + relative for relative in BUILD.AGENT_ASSET_PATHS}
                self.assertTrue(expected.issubset(names))
                self.assertEqual(len(names), len(set(names)))
                self.assertIn("backend/app/workbench/agent_api.py", names)
                output = self.root / (target + ".zip")
                archive = BUILD.Archive(output)
                records = [{"path": name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()} for source, name in sources]
                try:
                    for source, name in sources:
                        archive.file(source, "resources/" + name)
                    archive.add("resources/build-manifest.json", json.dumps({"application_files": records}).encode("utf-8"))
                finally:
                    archive.close()
                with zipfile.ZipFile(output) as packaged:
                    self.assertEqual(BUILD.validate_application_manifest(packaged, target, "resources/"), len(records))
                    for relative, data in self.asset_payloads.items():
                        self.assertEqual(packaged.read("resources/backend/app/workbench/agent_assets/" + relative), data)

    def test_connection_files_and_installation_artifacts_are_excluded(self):
        unwanted = (
            "credentials.json", "connection.json", "connection.secure", ".env", "install-result.json",
            "peace-atlas/.installed.json", "peace-atlas/agents/local-overrides.yaml",
            "peace-atlas/scripts/temporary_installer.py", "peace-atlas/scripts/__pycache__/peace_atlas.pyc",
        )
        for relative in unwanted:
            path = self.assets / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"local credential or generated installation fixture")
        with self.build_environment():
            names = [name for _, name in BUILD.application_files("windows-x64")]
        shipped_assets = {name.removeprefix("backend/app/workbench/agent_assets/")
                          for name in names if name.startswith("backend/app/workbench/agent_assets/")}
        self.assertEqual(shipped_assets, set(BUILD.AGENT_ASSET_PATHS))
        self.assertTrue(set(unwanted).isdisjoint(shipped_assets))

    def test_missing_required_skill_asset_stops_packaging(self):
        (self.assets / "peace-atlas/agents/openai.yaml").unlink()
        with self.build_environment():
            with self.assertRaisesRegex(FileNotFoundError, "openai.yaml"):
                BUILD.application_files("windows-x64")


class ApplicationFilesTests(unittest.TestCase):
    def test_current_targets_include_backup_and_every_workbench_module(self):
        base = BUILD.ROOT / "analysis-worker/app/workbench"
        modules = {"backend/app/workbench/" + source.relative_to(base).as_posix()
                   for source in base.rglob("*.py")
                   if not {"tests", "test", "__pycache__"}.intersection(source.relative_to(base).parts)}
        for target in BUILD.DEFAULT_TARGETS:
            with self.subTest(target=target):
                names = [name for _, name in BUILD.application_files(target)]
                self.assertEqual(len(names), len(set(names)))
                self.assertIn("backend/app/workbench/backup.py", names)
                self.assertIn("backend/app/workbench/restore.py", names)
                self.assertIn("app/restore.cjs", names)
                self.assertTrue(modules.issubset(names))
                self.assertFalse(any("tests" in Path(name).parts for name in names))
                self.assertFalse(any(Path(name).name in {".env", "connection.secure", "workbench.sqlite3", "legacy_main.py"} for name in names))

    def test_candidate_finalizer_uses_the_current_suffix(self):
        self.assertEqual(BUILD.ARTIFACT_SUFFIX, "-harness-candidate-20261010")
        finalizer = BUILD.MAC_FINALIZE.replace("@TARGET@", "macos-arm64").replace("@ARTIFACT_SUFFIX@", BUILD.ARTIFACT_SUFFIX)
        self.assertIn("PEACE-ATLAS-2.0.0-macos-arm64-harness-candidate-20261010-adhoc.zip", finalizer)
        self.assertNotIn("@ARTIFACT_SUFFIX@", finalizer)


if __name__ == "__main__":
    unittest.main()
