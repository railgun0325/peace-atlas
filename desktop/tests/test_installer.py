"""Installer input/path/retention contracts; no real application is installed."""
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("desktop_installer", Path(__file__).parents[1] / "installer.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pa-installer-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "PEACE-ATLAS-fixture"
        (self.package / "resources/app").mkdir(parents=True)
        (self.package / "PEACE ATLAS.exe").write_bytes(b"fixture application, not executable")
        (self.package / "resources/app/main.cjs").write_bytes(b"fixture launcher")
        self.appsha = INSTALLER.digest(self.package / "resources/app/main.cjs")
        self.manifest = {"product": "PEACE ATLAS", "version": "2.0.0", "target": "windows-x64",
                         "application_files": [{"path": "app/main.cjs", "sha256": self.appsha}]}
        (self.package / "resources/build-manifest.json").write_text(json.dumps(self.manifest))
        self.freeze = self.root / "freeze.json"
        self.freeze.write_text(json.dumps({"files": {"app/main.cjs": {"sha256": self.appsha}},
            "target_files": {"windows-x64": ["app/main.cjs"]}, "targets": {"windows-x64": 1}}))
        self.archive = self.root / "fixture.zip"
        self.zip()

    def zip(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for path in self.package.rglob("*"):
                if path.is_file():
                    archive.write(path, self.package.name + "/" + path.relative_to(self.package).as_posix())

    def payload(self):
        return INSTALLER.validate_payload(self.package, self.freeze, INSTALLER.digest(self.freeze), self.archive, INSTALLER.digest(self.archive))

    def prepare(self, **kw):
        return INSTALLER.prepare_windows(self.package, self.payload(), self.root / "compile", self.root / "setup.exe", **kw)

    def test_exact_archive_freeze_and_all_runtime_members_bound(self):
        payload = self.payload()
        self.assertEqual(len(payload["files"]), 3)
        self.assertEqual(payload["source_freeze_sha256"], INSTALLER.digest(self.freeze))
        self.assertTrue(payload["preserve_user_data"])

    def test_freeze_pin_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "SHA"):
            INSTALLER.validate_payload(self.package, self.freeze, "0" * 64, self.archive, INSTALLER.digest(self.archive))

    def test_archive_pin_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "archive changed"):
            INSTALLER.validate_payload(self.package, self.freeze, INSTALLER.digest(self.freeze), self.archive, "0" * 64)

    def test_modified_source_rejected(self):
        (self.package / "resources/app/main.cjs").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "payload changed"):
            self.payload()

    def test_added_runtime_file_rejected(self):
        (self.package / "unexpected.dll").write_bytes(b"unaccepted")
        with self.assertRaisesRegex(ValueError, "extra"):
            self.payload()

    def test_changed_runtime_rejected(self):
        (self.package / "PEACE ATLAS.exe").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "archive file differs"):
            self.payload()

    def test_missing_runtime_rejected(self):
        (self.package / "PEACE ATLAS.exe").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.payload()

    def test_source_omission_rejected(self):
        self.manifest["application_files"] = []
        (self.package / "resources/build-manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "source freeze"):
            self.payload()

    def test_duplicate_archive_rejected(self):
        import warnings
        with warnings.catch_warnings(), zipfile.ZipFile(self.archive, "a") as archive:
            warnings.simplefilter("ignore")
            archive.writestr(self.package.name + "/PEACE ATLAS.exe", b"duplicate")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.payload()

    def test_unsafe_member_rejected(self):
        for name in ("../data", "/root", "C:/data", "a\\b", "a/$tmp", "a/COM1.exe", "a/b.", 'a/"b', "a//b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                INSTALLER.relative_name(name)

    def test_case_collision_rejected(self):
        with patch.object(Path, "iterdir", return_value=iter([self.package / "PEACE ATLAS.exe", self.package / "PEACE ATLAS.exe"])):
            with self.assertRaisesRegex(ValueError, "Case-colliding"):
                INSTALLER.inventory(self.package)

    def test_production_is_user_and_no_automatic_launch(self):
        prepared = self.prepare()
        template = (Path(prepared["workspace"]) / "installer.nsi").read_text()
        self.assertIn("RequestExecutionLevel user", template)
        self.assertIn("SetShellVarContext current", template)
        self.assertNotIn("ExecShell", template)
        self.assertNotIn("RMDir /r", template)
        self.assertNotIn("HKLM", template)

    def test_test_namespace_is_explicit_and_owned(self):
        prepared = self.prepare(test_root=self.root / "owned-install", test_instance="installer-test-abcdef")
        settings = (Path(prepared["workspace"]) / "settings.nsh").read_text()
        self.assertIn("installer-test-abcdef", settings)
        self.assertIn("TEST_SHORTCUTS", settings)
        self.assertTrue(prepared["test_fixture"])

    def test_test_root_cannot_escape_staging_parent(self):
        with self.assertRaisesRegex(ValueError, "owned"):
            self.prepare(test_root=self.root.parent / "foreign-install", test_instance="installer-test-abcdef")

    def test_test_namespace_pair_required(self):
        with self.assertRaisesRegex(ValueError, "both"):
            self.prepare(test_instance="installer-test-abcdef")

    def test_uninstall_uses_fixed_member_delete_and_empty_directory_only(self):
        prepared = self.prepare()
        remove = (Path(prepared["workspace"]) / "remove.nsh").read_text()
        self.assertIn('Delete "$INSTDIR\\resources\\app\\main.cjs"', remove)
        self.assertNotIn("/r", remove)
        self.assertNotIn("APPDATA", remove)

    def test_guard_requires_marker_registry_root_and_manifest_and_process(self):
        guard = INSTALLER.WINDOWS_GUARD
        for text in ("$root -ine $allowed", "manifest_sha256 -ine $ExpectedManifest", "InstallLocation -ine $root", "Get-Process", "ReparsePoint"):
            self.assertIn(text, guard)
        self.assertNotIn("Remove-Item", guard)
        self.assertNotIn("workbench.sqlite3", guard)

    def test_compiler_pin_rejected_before_execution(self):
        compiler = self.root / "makensis.exe"
        compiler.write_bytes(b"not executable")
        with patch.object(INSTALLER.subprocess, "check_output") as call:
            with self.assertRaisesRegex(ValueError, "compiler differs"):
                INSTALLER.compile_windows({"workspace": str(self.root)}, compiler, "0" * 64)
            call.assert_not_called()

    def test_native_signing_allowance_rejected_on_windows(self):
        with self.assertRaisesRegex(ValueError, "signing allowance"):
            INSTALLER.validate_payload(self.package, self.freeze, INSTALLER.digest(self.freeze), self.archive,
                                       INSTALLER.digest(self.archive), native_signed_files=["app/main.cjs"])

    def test_acceptance_requires_both_pinned_passed_receipts(self):
        payload = self.payload()
        proof = self.root / "proof.json"
        proof.write_text(json.dumps({"ok": True}))
        body = {"installer_input_format": 1, "package_audit_passed": True, "native_passed": True,
                **{key: payload[key] for key in ("target", "source_freeze_sha256", "archive_sha256", "application_manifest_sha256")},
                "receipts": [{"kind": kind, "path": proof.name, "sha256": INSTALLER.digest(proof)} for kind in ("native", "package_audit")]}
        acceptance = self.root / "acceptance.json"
        acceptance.write_text(json.dumps(body))
        INSTALLER.validate_acceptance(acceptance, INSTALLER.digest(acceptance), payload)
        proof.write_text(json.dumps({"ok": False}))
        with self.assertRaisesRegex(ValueError, "SHA"):
            INSTALLER.validate_acceptance(acceptance, INSTALLER.digest(acceptance), payload)

    def test_payload_cannot_replace_installer_ownership(self):
        payload = self.payload()
        payload["files"].append({"path": "installation-state.json", "sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "ownership files"):
            INSTALLER.prepare_windows(self.package, payload, self.root / "compile", self.root / "setup.exe")

    def test_mac_wizard_stages_only_app_and_declares_unsigned(self):
        app = self.root / "PEACE ATLAS.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "org.peaceatlas.workbench", "CFBundleExecutable": "PEACE ATLAS"}))
        prepared = INSTALLER.prepare_macos(app, {"target": "macos-arm64", "version": "2.0.0"}, self.root / "mac-stage", self.root / "setup.pkg")
        stage = Path(prepared["workspace"])
        self.assertIn("enable_localSystem=\"true\"", (stage / "distribution.xml").read_text())
        self.assertIn("unsigned", (stage / "resources/welcome.html").read_text())
        self.assertEqual(plistlib.loads((stage / "components.plist").read_bytes())[0]["BundleIsRelocatable"], False)

    def test_no_mac_native_success_on_windows(self):
        with patch.object(INSTALLER.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(RuntimeError, "Native macOS"):
                INSTALLER.compile_macos({})

    def test_mac_preinstall_does_not_touch_user_data_security_or_launch(self):
        script = INSTALLER.MAC_PREINSTALL
        self.assertIn("pgrep", script)
        for word in ("xattr", "spctl", "rm ", "kill", "open ", "HOME", "defaults"):
            self.assertNotIn(word, script)


if __name__ == "__main__":
    unittest.main()
