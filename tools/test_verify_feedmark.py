import importlib.util
from pathlib import Path
import plistlib
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile


spec = importlib.util.spec_from_file_location("verify_feedmark", Path(__file__).with_name("verify-feedmark.py"))
verify_feedmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_feedmark)


class SimulatorRuntimeTests(unittest.TestCase):
    def test_matches_sdk_minor_instead_of_highest_installed_runtime(self):
        runtimes = [{"name": "iOS " + version, "version": version, "isAvailable": True}
                    for version in ("26.2", "26.4.1", "26.5")]
        self.assertEqual(verify_feedmark.select_ios_runtime(runtimes, "26.2")["version"], "26.2")
        runtimes.append({"name": "iOS 26.2.1", "version": "26.2.1", "isAvailable": True})
        self.assertEqual(verify_feedmark.select_ios_runtime(runtimes, "26.2")["version"], "26.2.1")

    def test_missing_sdk_runtime_fails_without_fallback(self):
        runtimes = [{"name": "iOS 26.2", "version": "26.2", "isAvailable": False},
                    {"name": "tvOS 26.2", "version": "26.2", "isAvailable": True},
                    {"name": "iOS 26.5", "version": "26.5", "isAvailable": True}]
        with self.assertRaisesRegex(RuntimeError, "No installed iOS"):
            verify_feedmark.select_ios_runtime(runtimes, "26.2")

    def test_invalid_sdk_version_fails(self):
        for version in ("26", "not a version"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                verify_feedmark.select_ios_runtime([], version)


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

    def test_timeout_diagnostics_record_identity_without_accepting_install(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            built, installed = run / "built.app", run / "installed.app"
            for app in (built, installed):
                app.mkdir()
                (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "example.app", "CFBundleExecutable": "App"}))
                (app / "App").write_bytes(b"matching")
            command, output = Mock(), Mock(return_value=str(installed).encode())
            result = verify_feedmark.install_timeout_diagnostics(run, "owned-device", "example.app", built, command, output)
            self.assertTrue(result["diagnosticOnly"])
            self.assertEqual(result["appIdentity"]["built"]["sha256"], result["appIdentity"]["installed"]["sha256"])
            self.assertTrue((run / "install-timeout-app.json").is_file())
            self.assertNotIn("nativeAcceptance", result)
            output.assert_called_once_with(["xcrun", "simctl", "get_app_container", "owned-device", "example.app", "app"])
            self.assertEqual(command.call_count, 2)
            self.assertTrue(all(call.kwargs["timeout"] == 30 for call in command.call_args_list))
            self.assertEqual(command.call_args_list[1].args[0][:4], ["xcrun", "simctl", "spawn", "owned-device"])
            self.assertFalse(any("install" in call.args[0] for call in command.call_args_list))

    def test_timeout_diagnostic_failures_are_recorded_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            command = Mock(side_effect=subprocess.TimeoutExpired(["log"], 30))
            output = Mock(side_effect=subprocess.CalledProcessError(1, ["get_app_container"]))
            result = verify_feedmark.install_timeout_diagnostics(Path(directory), "owned", "example.app", Path(directory), command, output)
            self.assertIn("appLookupError", result)
            self.assertEqual(set(result["logErrors"]), {"host-install-at-failure.log", "simulator-install-at-failure.log"})
            self.assertEqual(command.call_count, 2)
            self.assertEqual(output.call_count, 1)
            self.assertTrue(result["diagnosticOnly"])


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


class NativePhaseTests(unittest.TestCase):
    class Process:
        def __init__(self):
            self.killed = threading.Event()

        def poll(self):
            return None if not self.killed.is_set() else -9

        def kill(self):
            self.killed.set()

    def test_ordered_callbacks_and_failure_are_enforced(self):
        controller = verify_feedmark.PhaseController(("one", "two"), timeout=10)
        controller.start(self.Process())
        self.assertEqual(controller.advance("one", lambda: {"value": 3}), {"value": 3})
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            controller.advance("one", lambda: None)
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            controller.close()

        controller = verify_feedmark.PhaseController(("one", "two"), timeout=10)
        controller.start(self.Process())
        controller.advance("one", lambda: None)
        controller.advance("two", lambda: None)
        controller.close()
        self.assertEqual([event["phase"] for event in controller.events], ["one", "two"])

    def test_callback_error_and_deadline_fail_closed(self):
        process = self.Process()
        controller = verify_feedmark.PhaseController(("one",), timeout=0.05)
        controller.start(process)
        def fail():
            raise ValueError("archive mismatch")
        with self.assertRaisesRegex(ValueError, "archive mismatch"):
            controller.advance("one", fail)
        self.assertTrue(process.killed.wait(2))
        with self.assertRaisesRegex(RuntimeError, "archive mismatch"):
            controller.close()

        process = self.Process()
        controller = verify_feedmark.PhaseController(("one",), timeout=0.05)
        controller.start(process)
        self.assertTrue(process.killed.wait(2))
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            controller.close()

    def test_close_does_not_allow_inflight_callback_to_advance(self):
        controller = verify_feedmark.PhaseController(("one", "two"), timeout=10)
        controller.start(self.Process())
        entered = threading.Event()
        release = threading.Event()
        failures = []
        def action():
            entered.set()
            release.wait(2)
        def invoke():
            try:
                controller.advance("one", action)
            except RuntimeError as error:
                failures.append(str(error))
        worker = threading.Thread(target=invoke)
        worker.start()
        self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(RuntimeError, "before all phases"):
            controller.close()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(controller.events, [])
        self.assertEqual(failures, ["Native campaign already closed."])

        process = self.Process()
        controller = verify_feedmark.PhaseController(("one",), timeout=0.05)
        controller.start(process)
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            controller.advance("one", lambda: process.killed.wait(2))
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            controller.close()

        process = self.Process()
        controller = verify_feedmark.PhaseController(("one",), timeout=0.05)
        controller.start(process)
        controller.advance("one", lambda: None)
        self.assertTrue(process.killed.wait(2))
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            controller.close()


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


class ArtifactBoundaryTests(unittest.TestCase):
    def test_same_run_source_hash_port_and_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "app.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("NetNewsWire.app/NetNewsWire", b"binary")
            manifest = {"schemaVersion": 1, "runId": "123", "runAttempt": "1", "source": "abc",
                        "fixturePort": 12345, "executable": "NetNewsWire",
                        "archiveSha256": verify_feedmark.hashlib.sha256(archive.read_bytes()).hexdigest()}
            receipt = {"source": "abc", "runId": "123", "runAttempt": "1", "buildAccepted": True,
                       "cleanup": {flag: True for flag in verify_feedmark.CLEANUP_FLAGS},
                       "cleanupErrors": [], "missingEvidence": []}
            validate = lambda m, r: verify_feedmark.validate_boundary(m, r, "123", "1", "1", "abc", archive)
            self.assertEqual(validate(manifest, receipt), 12345)
            self.assertEqual(verify_feedmark.validate_boundary(manifest, receipt, "123", "1", "2", "abc", archive), 12345)
            with self.assertRaises(ValueError):
                verify_feedmark.validate_boundary(manifest, receipt, "123", "2", "2", "abc", archive)
            with self.assertRaises(ValueError):
                verify_feedmark.validate_boundary(manifest, receipt, "123", "2", "1", "abc", archive)
            for update in ({"source": "wrong"}, {"runId": "wrong"}, {"runAttempt": "2"},
                           {"fixturePort": 0}, {"fixturePort": 1023}, {"fixturePort": "12345"}, {"archiveSha256": "wrong"},
                           {"executable": "../escape"}):
                with self.subTest(update=update), self.assertRaises(ValueError):
                    validate({**manifest, **update}, receipt)
            with self.assertRaises(ValueError):
                validate(manifest, {**receipt, "buildAccepted": False})
            with self.assertRaises(ValueError):
                validate(manifest, {**receipt, "source": "wrong"})
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("NetNewsWire.app/../../escape", b"bad")
            manifest["archiveSha256"] = verify_feedmark.hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                validate(manifest, receipt)
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, target in (("A", "../x"), ("B", "A/../../../outside")):
                    link = zipfile.ZipInfo("NetNewsWire.app/long/deep/" + name)
                    link.create_system = 3
                    link.external_attr = (verify_feedmark.stat.S_IFLNK | 0o777) << 16
                    bundle.writestr(link, target)
            manifest["archiveSha256"] = verify_feedmark.hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "entry type"):
                validate(manifest, receipt)
            archive.unlink()
            with self.assertRaises(ValueError):
                validate(manifest, receipt)

    def test_fixture_port_cannot_be_replaced_if_occupied(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaises(OSError):
                verify_feedmark.ThreadingHTTPServer(("127.0.0.1", listener.getsockname()[1]),
                                                     verify_feedmark.BaseHTTPRequestHandler)

    def test_extracted_identity_mode_and_fixture_port(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            with (app / "Info.plist").open("wb") as info_file:
                plistlib.dump({"CFBundleIdentifier": "example.app", "CFBundleExecutable": "App"}, info_file)
            binary = app / "App"
            binary.write_bytes(b"binary")
            opml = verify_feedmark.fixture_opml(12345)
            (app / "DefaultFeeds.opml").write_bytes(opml)
            manifest = {"bundleId": "example.app", "executable": "App",
                        "binarySha256": verify_feedmark.hashlib.sha256(binary.read_bytes()).hexdigest(),
                        "binaryMode": 0o755,
                        "fixtureSha256": verify_feedmark.hashlib.sha256(opml).hexdigest()}
            with patch.object(verify_feedmark.stat, "S_IMODE", return_value=0o755):
                self.assertEqual(verify_feedmark.validate_staged_app(app, manifest, 12345), "example.app")
                with self.assertRaisesRegex(ValueError, "fixture"):
                    verify_feedmark.validate_staged_app(app, manifest, 12346)
                with self.assertRaisesRegex(ValueError, "mode"):
                    verify_feedmark.validate_staged_app(app, {**manifest, "binaryMode": 0}, 12345)
                binary.write_bytes(b"different")
                with self.assertRaisesRegex(ValueError, "identity"):
                    verify_feedmark.validate_staged_app(app, manifest, 12345)


if __name__ == "__main__":
    unittest.main()
