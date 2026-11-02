import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("verify_feedmark", Path(__file__).with_name("verify-feedmark.py"))
verify_feedmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_feedmark)


class InstalledAppIdentityTests(unittest.TestCase):
    def test_matching_and_mismatched_binaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            built, installed = root / "built.app", root / "installed.app"
            for app in (built, installed):
                app.mkdir()
                with (app / "Info.plist").open("wb") as info_file:
                    plistlib.dump({"CFBundleIdentifier": "example.app", "CFBundleExecutable": "App"}, info_file)
                (app / "App").write_bytes(b"matching")
            receipt = root / "receipt.json"
            verify_feedmark.verify_installed_app(built, installed, receipt)
            (installed / "App").write_bytes(b"different")
            with self.assertRaisesRegex(ValueError, "does not match"):
                verify_feedmark.verify_installed_app(built, installed, receipt)
            self.assertNotEqual(verify_feedmark.json.loads(receipt.read_text())["built"]["sha256"],
                                verify_feedmark.json.loads(receipt.read_text())["installed"]["sha256"])


class ResultFinalizationTests(unittest.TestCase):
    def test_native_acceptance_requires_all_checks_and_cleanup(self):
        cleanup = {flag: True for flag in verify_feedmark.CLEANUP_FLAGS}
        cases = [
            (True, cleanup, [], True),
            (False, cleanup, [], False),
            (True, cleanup, ["shutdown failed"], False),
        ]
        for flag in cleanup:
            cases.append((True, {**cleanup, flag: False}, [], False))
            cases.append((True, {key: value for key, value in cleanup.items() if key != flag}, [], False))
        for checks_passed, flags, errors, accepted in cases:
            with self.subTest(flags=flags, errors=errors, checks_passed=checks_passed):
                result = {}
                verify_feedmark.finalize_result(result, flags, errors, checks_passed)
                self.assertIs(result["nativeAcceptance"], accepted)

    def test_all_owned_simulators_must_be_shutdown(self):
        owned = ["test", "native"]
        self.assertTrue(verify_feedmark.simulators_stopped(owned, {"test": "Shutdown", "native": "Shutdown"}))
        self.assertFalse(verify_feedmark.simulators_stopped(owned, {"test": "Shutdown", "native": "Booted"}))
        self.assertFalse(verify_feedmark.simulators_stopped(owned, {"test": "Shutdown"}))


class EvidenceRetentionTests(unittest.TestCase):
    def test_required_artifacts_survive_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            for pattern in verify_feedmark.required_evidence(True):
                path = run / pattern.replace("*", "run-time")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(verify_feedmark.PNG_SIGNATURE if path.suffix == ".png" else b"evidence")
            result_file = run / "baseline.xcresult" / "result"
            result_file.parent.mkdir()
            result_file.write_bytes(b"evidence")
            self.assertEqual(verify_feedmark.missing_evidence(run, True), [])
            (run / "notebook-removed.json").unlink()
            self.assertIn("notebook-removed.json", verify_feedmark.missing_evidence(run, True))
            self.assertEqual(verify_feedmark.missing_evidence(run, False), [])
            (run / "native-article.png").write_bytes(b"not a PNG")
            self.assertIn("native-article.png", verify_feedmark.missing_evidence(run, True))
            (run / "build.log").unlink()
            self.assertIn("build.log", verify_feedmark.missing_evidence(run, False))


if __name__ == "__main__":
    unittest.main()
