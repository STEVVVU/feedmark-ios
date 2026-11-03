"""Drive an unsigned iOS build on a dedicated GitHub-hosted simulator."""

import argparse
import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import secrets
import stat
import subprocess
import threading
import time
import urllib.request
import zipfile
from xml.sax.saxutils import escape as xml_escape


ROOT = Path(__file__).resolve().parents[1]
MAESTRO_VERSION = "cli-2.11.0"
MAESTRO_SHA256 = "5384593cb4e7a106489e75a821d157dd43f4e438df6bc308b72e82c685e1283a"
FIXTURE_CACHE_CONTROL = "max-age=1"
CLEANUP_FLAGS = ("originalOpmlRestored", "simulatorStopped", "fixtureStopped", "evidenceRetained")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
NATIVE_PHASES = ("baseline", "notebook", "reopen", "prepend", "duplicate", "deleted", "repair", "remove")


class PhaseController:
    def __init__(self, phases, timeout=300):
        self.phases = phases
        self.timeout = timeout
        self.index = 0
        self.error = None
        self.events = []
        self.lock = threading.Lock()
        self.busy = False
        self.closed = False
        self.callback_done = threading.Event()
        self.callback_done.set()
        self.ready = threading.Event()
        self.timer = None
        self.process = None

    def start(self, process):
        with self.lock:
            self.process = process
            self._arm()
            self.ready.set()

    def _arm(self):
        self.started = time.monotonic()
        self.timer = threading.Timer(self.timeout, self._expire, args=(self.index,))
        self.timer.daemon = True
        self.timer.start()

    def _expire(self, index):
        with self.lock:
            if self.closed or (index != self.index and not (self.index == len(self.phases) and index == self.index - 1)):
                return
            self.error = self.error or "Native phase timed out: " + self.phases[min(index, len(self.phases) - 1)]
            if self.process.poll() is None:
                self.process.kill()

    def advance(self, phase, action):
        if not self.ready.wait(self.timeout):
            raise TimeoutError("Native phase controller did not start.")
        with self.lock:
            if self.closed or self.error or self.busy or self.index >= len(self.phases) or self.phases[self.index] != phase:
                self.error = self.error or "Unexpected native phase: " + phase
                raise RuntimeError(self.error)
            self.busy = True
            self.callback_done.clear()
        try:
            value = action()
            with self.lock:
                if self.closed:
                    raise RuntimeError("Native campaign already closed.")
                if self.error or time.monotonic() - self.started >= self.timeout:
                    self.error = self.error or "Native phase timed out: " + phase
                    raise TimeoutError(self.error)
                self.events.append({"phase": phase, "elapsedSeconds": round(time.monotonic() - self.started, 3)})
                self.index += 1
                if self.index < len(self.phases):
                    self.timer.cancel()
                    self._arm()
            return value
        except BaseException as error:
            with self.lock:
                self.error = self.error or str(error) or type(error).__name__
            raise
        finally:
            with self.lock:
                self.busy = False
                self.callback_done.set()

    def ensure_active(self):
        with self.lock:
            if self.closed or self.error:
                raise RuntimeError(self.error or "Native campaign already closed.")

    def close(self):
        with self.lock:
            self.closed = True
            if self.timer:
                self.timer.cancel()
            if self.error:
                raise RuntimeError(self.error)
            if self.index != len(self.phases):
                raise RuntimeError("Native campaign ended before all phases completed.")


def required_evidence(native):
    files = ["build.log", "warnings.log", "doctor.log", "fixture-requests.json"]
    if not native:
        return files
    files += ["installed-app.json", "native-article.png", "maestro.log", "campaign-junit.xml"]
    files += ["notebook-" + name + ".json" for name in
              ("saved", "reopened", "prepend", "duplicate", "deleted", "repaired", "removed")]
    screenshots = {"baseline": ("sidebar", "timeline", "article"),
                   "notebook": ("notebook-before-open", "notebook-after-open", "notebook-before-save",
                                "notebook-after-save", "notebook-saved"),
                   "notebook-reopen": ("notebook-reopened",),
                   "notebook-repair": ("notebook-repaired",), "notebook-remove": ("notebook-removed",)}
    for version in ("prepend", "duplicate", "deleted"):
        screenshots["notebook-" + version] = ("notebook-before-dismiss-" + version, "notebook-after-dismiss-" + version,
                                              "refresh-before-" + version, "refresh-after-" + version,
                                              "notebook-" + version)
    files += ["native-artifacts/*/campaign/takeScreenshot/" + name + ".png"
              for names in screenshots.values() for name in names]
    return files


def missing_evidence(run, native, native_only=False):
    def valid_png(path):
        with path.open("rb") as image:
            return image.read(len(PNG_SIGNATURE)) == PNG_SIGNATURE

    missing = []
    patterns = required_evidence(native)
    if native_only:
        patterns = [pattern for pattern in patterns if pattern not in ("build.log", "warnings.log")]
        patterns += ["build-manifest.json", "build-result.json"]
    for pattern in patterns:
        matches = [path for path in run.glob(pattern) if path.is_file() and path.stat().st_size > 0]
        if pattern.endswith(".png"):
            matches = [path for path in matches if valid_png(path)]
        if not matches:
            missing.append(pattern)
    if not native_only and not any(path.is_file() for path in (run / "baseline.xcresult").rglob("*")):
        missing.append("baseline.xcresult/**")
    return missing


def validate_boundary(manifest, build_result, run_id, producer_attempt, current_attempt, source, archive):
    if not producer_attempt.isdecimal() or not current_attempt.isdecimal() or int(producer_attempt) < 1 or int(producer_attempt) > int(current_attempt):
        raise ValueError("Invalid producing workflow attempt.")
    if manifest.get("schemaVersion") != 1 or not run_id or not source or manifest.get("runId") != run_id or manifest.get("runAttempt") != producer_attempt or manifest.get("source") != source:
        raise ValueError("Build artifact run or source does not match this workflow.")
    if build_result.get("source") != source or build_result.get("runId") != run_id or build_result.get("runAttempt") != producer_attempt:
        raise ValueError("Build receipt run or source does not match this workflow.")
    executable = manifest.get("executable")
    if not isinstance(executable, str) or not executable or executable in (".", "..") or "/" in executable or "\\" in executable:
        raise ValueError("Build artifact has an invalid executable name.")
    port = manifest.get("fixturePort")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Build artifact has an invalid fixture port.")
    if build_result.get("buildAccepted") is not True or build_result.get("missingEvidence") != [] or build_result.get("cleanupErrors") != []:
        raise ValueError("Build receipt does not prove a successful build and cleanup.")
    if any(build_result.get("cleanup", {}).get(flag) is not True for flag in CLEANUP_FLAGS):
        raise ValueError("Build receipt does not prove successful cleanup.")
    if archive.is_symlink() or not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != manifest.get("archiveSha256"):
        raise ValueError("Build archive hash does not match manifest.")
    with zipfile.ZipFile(archive) as bundle:
        names = set()
        for entry in bundle.infolist():
            name = entry.filename.rstrip("/")
            parts = name.split("/")
            if not name or parts[0] != "NetNewsWire.app" or any(part in ("", ".", "..") for part in parts) or "\\" in name:
                raise ValueError("Unsafe app archive path.")
            name_key = name.casefold()
            if name_key in names:
                raise ValueError("Duplicate app archive path.")
            names.add(name_key)
            kind = (entry.external_attr >> 16) & 0o170000
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("Unsafe app archive entry type.")
    return port


def finalize_result(result, cleanup, cleanup_errors, native_checks_passed):
    cleanup_passed = all(cleanup.get(flag) is True for flag in CLEANUP_FLAGS) and not cleanup_errors
    result["cleanup"] = cleanup
    result["cleanupErrors"] = cleanup_errors
    result["nativeAcceptance"] = native_checks_passed and cleanup_passed
    return cleanup_passed


def simulators_stopped(owned_simulators, states):
    return all(states.get(owned) == "Shutdown" for owned in owned_simulators)


def fixture_opml(port):
    return ("<?xml version='1.0'?><opml version='2.0'><head><title>Verification</title></head><body>"
            f"<outline text='Feedmark verification feed' title='Feedmark verification feed' type='rss' "
            f"xmlUrl='http://127.0.0.1:{port}/feed.xml'/></body></opml>").encode()


def validate_staged_app(app, manifest, port):
    with (app / "Info.plist").open("rb") as info_file:
        info = plistlib.load(info_file)
    if info["CFBundleIdentifier"] != manifest["bundleId"] or info["CFBundleExecutable"] != manifest["executable"] or hashlib.sha256((app / info["CFBundleExecutable"]).read_bytes()).hexdigest() != manifest["binarySha256"]:
        raise ValueError("Extracted app identity does not match manifest.")
    binary_mode = stat.S_IMODE((app / info["CFBundleExecutable"]).stat().st_mode)
    if binary_mode != manifest.get("binaryMode") or binary_mode & 0o111 == 0:
        raise ValueError("Extracted app executable mode does not match the tested build.")
    opml = (app / "DefaultFeeds.opml").read_bytes()
    if opml != fixture_opml(port) or hashlib.sha256(opml).hexdigest() != manifest.get("fixtureSha256"):
        raise ValueError("Extracted app fixture does not match the manifest port and hash.")
    return info["CFBundleIdentifier"]


def verify_installed_app(built_app, installed_app, receipt_path):
    def identity(app):
        with (app / "Info.plist").open("rb") as info_file:
            info = plistlib.load(info_file)
        executable = info["CFBundleExecutable"]
        binary = app / executable
        return {"appPath": str(app), "bundleId": info["CFBundleIdentifier"], "executable": executable,
                "binaryPath": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}

    receipt = {"built": identity(built_app), "installed": identity(installed_app)}
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    if any(receipt["built"][key] != receipt["installed"][key] for key in ("bundleId", "executable", "sha256")):
        raise ValueError("Installed app identity does not match the built app; see installed-app.json.")
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_name")
    parser.add_argument("--build-only", action="store_true", help="Build and run the iOS test plan without driving native UI.")
    parser.add_argument("--stage-build", action="store_true", help="Archive the tested app for the native job.")
    parser.add_argument("--native-from", type=Path, help="Drive an app staged by the build job of this workflow run.")
    parser.add_argument("--producer-attempt", help="Workflow attempt that produced the staged app.")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or platform.system() != "Darwin":
        parser.error("Use the dedicated macOS hosted workflow. No local native UI automation is configured.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", args.run_name):
        parser.error("Use a short alphanumeric run name.")
    if args.native_from and (args.build_only or args.stage_build) or args.stage_build and not args.build_only:
        parser.error("Use --stage-build with --build-only, or --native-from alone.")
    if bool(args.native_from) != bool(args.producer_attempt):
        parser.error("Use --native-from with --producer-attempt.")
    run = ROOT / ".audit" / "verification" / args.run_name
    run.mkdir(parents=True, exist_ok=False)
    result = {"run": args.run_name, "actions": [], "nativeAcceptance": False, "requestedNative": not args.build_only}
    native_checks_passed = False
    stage_dir = None
    env = dict(os.environ, PROJECT_DIR=str(ROOT), MAESTRO_CLI_NO_ANALYTICS="true",
               MAESTRO_DISABLE_UPDATE_CHECK="true", MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED="true")
    for key in ("MERCURY_CLIENT_ID", "MERCURY_CLIENT_SECRET", "FEEDLY_CLIENT_ID", "FEEDLY_CLIENT_SECRET",
                "INOREADER_APP_ID", "INOREADER_APP_KEY"):
        env.pop(key, None)
    simulator = None
    owned_simulators = []
    active_simulators = []
    bundle_id = None
    server = None
    thread = None
    fixture_path = ROOT / "Shared/Importers/DefaultFeeds.opml"
    original_fixture = fixture_path.read_bytes()
    requests = []
    original_article = "Local synthetic article for native reader verification."
    fixture = {"version": "original", "paragraphs": [original_article]}
    fixture_lock = threading.Lock()
    callback_token = secrets.token_urlsafe(24)
    phase_callback = None
    phase_controller = None

    def command(argv, log_name, timeout=1800):
        print("RUN " + " ".join(str(part) for part in argv), flush=True)
        with (run / log_name).open("ab") as log:
            subprocess.run([str(part) for part in argv], cwd=ROOT, env=env, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def output(argv, timeout=30):
        return subprocess.check_output([str(part) for part in argv], cwd=ROOT, env=env,
                                       timeout=timeout, stdin=subprocess.DEVNULL)

    def timed(action, operation):
        started = time.monotonic()
        succeeded = False
        try:
            value = operation()
            succeeded = True
            return value
        finally:
            result["actions"].append({"action": action, "simulator": simulator,
                                      "elapsedSeconds": round(time.monotonic() - started, 3), "succeeded": succeeded})

    try:
        command(["uname", "-a"], "doctor.log", timeout=15)
        command(["sysctl", "hw.logicalcpu", "hw.memsize"], "doctor.log", timeout=15)
        command(["xcodebuild", "-version"], "doctor.log", timeout=30)
        command(["java", "-version"], "doctor.log", timeout=15)
        source = output(["git", "rev-parse", "HEAD"]).decode().strip()
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if (args.stage_build or args.native_from) and (not run_id or not run_attempt or source != os.environ.get("GITHUB_SHA")):
            raise ValueError("Staged runs require matching GitHub run and checkout identity.")
        result.update({"source": source, "runId": run_id, "runAttempt": run_attempt})
        manifest = None
        fixture_port = 0
        if args.native_from:
            if any((args.native_from / name).is_symlink() or not (args.native_from / name).is_file()
                   for name in ("manifest.json", "build-result.json", "app.zip")):
                raise ValueError("Build artifact files must be regular files.")
            manifest = json.loads((args.native_from / "manifest.json").read_text(encoding="utf-8"))
            build_receipt = json.loads((args.native_from / "build-result.json").read_text(encoding="utf-8"))
            archive = args.native_from / "app.zip"
            fixture_port = validate_boundary(manifest, build_receipt, run_id, args.producer_attempt, run_attempt, source, archive)
            (run / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            (run / "build-result.json").write_text(json.dumps(build_receipt, indent=2), encoding="utf-8")
            command(["ditto", "-x", "-k", archive, run / "staged-app"], "simulator.log", timeout=300)
            app = run / "staged-app/NetNewsWire.app"
            bundle_id = validate_staged_app(app, manifest, fixture_port)
            result["buildAccepted"] = True
            result["actions"].append({"action": "verify-staged-build", "source": source, "runId": run_id,
                                      "binarySha256": manifest["binarySha256"], "fixturePort": fixture_port})
        inventory_started = time.monotonic()
        try:
            command(["xcrun", "simctl", "list", "--json"], "simulator-inventory.log", timeout=120)
        finally:
            result["actions"].append({"action": "simulator-inventory-probe",
                                      "elapsedSeconds": round(time.monotonic() - inventory_started, 3),
                                      "deadlineSeconds": 120})
        inventory = json.loads((run / "simulator-inventory.log").read_text(encoding="utf-8"))
        (run / "simulator-inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
        runtimes = [item for item in inventory["runtimes"] if item.get("isAvailable") and item["name"].startswith("iOS")]
        runtime = max(runtimes, key=lambda item: tuple(int(part) for part in item["version"].split(".")))
        device_type = next(item for item in inventory["devicetypes"] if item["name"] == "iPhone 17")
        simulator = output(["xcrun", "simctl", "create", "Feedmark-" + args.run_name,
                            device_type["identifier"], runtime["identifier"]]).decode().strip()
        owned_simulators.append(simulator)
        active_simulators.append(simulator)
        result["actions"].append({"action": "doctor", "simulator": simulator, "runtime": runtime["version"]})
        if args.native_from:
            timed("boot-native-simulator", lambda: command(["xcrun", "simctl", "boot", simulator], "simulator.log", timeout=60))
            timed("native-bootstatus", lambda: command(["xcrun", "simctl", "bootstatus", simulator, "-b"], "simulator.log", timeout=300))
        else:
            command(["xcrun", "simctl", "boot", simulator], "simulator.log", timeout=60)
            command(["xcrun", "simctl", "bootstatus", simulator, "-b"], "simulator.log", timeout=300)

        class FixtureHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                prefix = "/__verify/" + callback_token + "/"
                if not self.path.startswith(prefix) or phase_callback is None:
                    self.send_error(404)
                    return
                try:
                    payload = json.dumps(phase_callback(self.path[len(prefix):])).encode()
                    self.send_response(200)
                except Exception as error:
                    payload = json.dumps({"error": str(error)}).encode()
                    self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                with fixture_lock:
                    version = fixture["version"]
                    body = "".join("<p>" + escape(paragraph) + "</p>" for paragraph in fixture["paragraphs"])
                    requests.append({"path": self.path, "method": "GET", "version": version,
                                     "responseCacheControl": FIXTURE_CACHE_CONTROL})
                if self.path == "/feed.xml":
                    payload = ("<?xml version='1.0'?><rss version='2.0'><channel><title>Feedmark verification feed</title>"
                               "<link>http://127.0.0.1:{port}/</link><description>Synthetic local verification</description>"
                               "<item><title>Evidence survives the network</title><guid isPermaLink='false'>feedmark-fixture-001</guid>"
                               "<link>http://127.0.0.1:{port}/article</link>"
                               "<description>{description}</description>"
                               "</item></channel></rss>").format(port=self.server.server_port, description=xml_escape(body)).encode()
                    content_type = "application/rss+xml"
                elif self.path in ("/", "/article"):
                    payload = ("<html><body>" + body + "</body></html>").encode()
                    content_type = "text/html"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", FIXTURE_CACHE_CONTROL)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        try:
            server = ThreadingHTTPServer(("127.0.0.1", fixture_port), FixtureHandler)
        except OSError as error:
            raise OSError(f"Cannot bind the required fixture port {fixture_port}: {error}") from error
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        if not args.native_from:
            fixture_path.write_bytes(fixture_opml(port))
        result["actions"].append({"action": "fixture", "loopbackPort": port, "originalOpmlSha256": hashlib.sha256(original_fixture).hexdigest()})
        if not args.native_from:
            command(["bash", "buildscripts/updateSecrets.sh"], "build.log", timeout=60)
            command(["xcodebuild", "-project", "NetNewsWire.xcodeproj", "-scheme", "NetNewsWire-iOS",
                 "-testPlan", "NetNewsWire-iOS", "-xcconfig", ".github/ios-ci-no-signing.xcconfig",
                 "-jobs", "2", "-showBuildTimingSummary",
                 "-destination", "platform=iOS Simulator,id=" + simulator,
                 "-parallel-testing-enabled", "NO",
                 "-derivedDataPath", run / "DerivedData", "-resultBundlePath", run / "baseline.xcresult",
                 "OTHER_SWIFT_FLAGS=$(inherited) -DDEBUG -DSKIP_APP_GROUP_ACCESS", "test"], "build.log")
            command(["bash", "buildscripts/fail_on_warnings.sh", run / "build.log"], "warnings.log", timeout=30)
            app = run / "DerivedData/Build/Products/Debug-iphonesimulator/NetNewsWire.app"
            with (app / "Info.plist").open("rb") as info_file:
                info = plistlib.load(info_file)
            bundle_id = info["CFBundleIdentifier"]
            result["actions"].append({"action": "build-and-test", "bundleId": bundle_id, "signing": "disabled"})
            result["buildAccepted"] = True
            if args.stage_build:
                if (app / "DefaultFeeds.opml").read_bytes() != fixture_opml(port):
                    raise ValueError("Built app fixture does not match its server port.")
                stage_dir = run / "stage"
                stage_dir.mkdir()
                archive = stage_dir / "app.zip"
                command(["ditto", "-c", "-k", "--norsrc", "--keepParent", app, archive], "build.log", timeout=300)
                manifest = {"schemaVersion": 1, "runId": run_id, "runAttempt": run_attempt, "source": source, "fixturePort": port,
                            "bundleId": bundle_id, "executable": info["CFBundleExecutable"],
                            "fixtureSha256": hashlib.sha256(fixture_opml(port)).hexdigest(),
                            "binarySha256": hashlib.sha256((app / info["CFBundleExecutable"]).read_bytes()).hexdigest(),
                            "binaryMode": stat.S_IMODE((app / info["CFBundleExecutable"]).stat().st_mode),
                            "archiveSha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
                (stage_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if args.build_only:
            return
        if not args.native_from:
            timed("shutdown-test-simulator", lambda: command(["xcrun", "simctl", "shutdown", simulator], "simulator.log", timeout=60))
            active_simulators.remove(simulator)
            simulator = output(["xcrun", "simctl", "create", "Feedmark-native-" + args.run_name,
                            device_type["identifier"], runtime["identifier"]]).decode().strip()
            owned_simulators.append(simulator)
            active_simulators.append(simulator)
            result["actions"].append({"action": "create-native-simulator", "simulator": simulator, "runtime": runtime["version"]})
            timed("boot-native-simulator", lambda: command(["xcrun", "simctl", "boot", simulator], "simulator.log", timeout=60))
            timed("native-bootstatus", lambda: command(["xcrun", "simctl", "bootstatus", simulator, "-b"], "simulator.log", timeout=300))
        timed("install-native-app", lambda: command(["xcrun", "simctl", "install", simulator, app], "simulator.log", timeout=60))
        installed_app = Path(timed("locate-native-app", lambda: output(
            ["xcrun", "simctl", "get_app_container", simulator, bundle_id, "app"])).decode().strip())
        receipt = verify_installed_app(app, installed_app, run / "installed-app.json")
        result["actions"].append({"action": "verify-installed-app", "sha256": receipt["installed"]["sha256"],
                                  "receipt": "installed-app.json"})

        archive_path = run / "maestro.zip"
        urllib.request.urlretrieve("https://github.com/mobile-dev-inc/Maestro/releases/download/" + MAESTRO_VERSION + "/maestro.zip", archive_path)
        assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == MAESTRO_SHA256, "Maestro release checksum mismatch."
        tool_dir = run / "maestro-tool"
        tool_dir.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                target = (tool_dir / entry.filename).resolve()
                assert target.is_relative_to(tool_dir.resolve()), "Archive path escaped the isolated tool directory."
                assert (entry.external_attr >> 16) & 0o170000 != 0o120000, "Unexpected symlink in tool archive."
            archive.extractall(tool_dir)
            for entry in archive.infolist():
                target = tool_dir / entry.filename
                mode = (entry.external_attr >> 16) & 0o777
                if target.is_file() and mode:
                    target.chmod(mode)
        maestro = tool_dir / "maestro/bin/maestro"
        command([maestro, "--version"], "maestro.log", timeout=60)
        command([maestro, "test", "--help"], "maestro-help.txt", timeout=60)
        flow = run / "baseline.yaml"
        flow.write_text("appId: " + bundle_id + "\n---\n"
                        "- launchApp:\n    permissions:\n      all: deny\n"
                        "- extendedWaitUntil:\n    visible: 'Feedmark verification feed.*'\n    timeout: 60000\n"
                        "- takeScreenshot: sidebar\n"
                        "- tapOn: 'Feedmark verification feed.*'\n"
                        "- extendedWaitUntil:\n    visible: 'Evidence survives the network.*'\n    timeout: 60000\n"
                        "- takeScreenshot: timeline\n"
                        "- tapOn: 'Evidence survives the network.*'\n"
                        "- assertVisible: 'Local synthetic article for native reader verification.*'\n"
                        "- takeScreenshot: article\n", encoding="utf-8")
        notebook_flow = run / "notebook.yaml"
        notebook_flow.write_text("appId: " + bundle_id + "\n---\n"
                                 "- takeScreenshot: notebook-before-open\n"
                                 "- tapOn: 'Reading notebook'\n"
                                 "- takeScreenshot: notebook-after-open\n"
                                 "- assertVisible: 'Capture selected passage'\n"
                                 "- tapOn:\n    id: notebook.captureWhole\n"
                                 "- scrollUntilVisible:\n    element:\n      id: notebook.note\n    direction: DOWN\n"
                                 "- tapOn:\n    id: notebook.note\n"
                                 "- inputText: 'Source context matters'\n"
                                 "- tapOn:\n    id: '^Done$'\n"
                                 "- waitForAnimationToEnd\n"
                                 "- scrollUntilVisible:\n    element:\n      id: notebook.save\n    direction: DOWN\n"
                                 "- takeScreenshot: notebook-before-save\n"
                                 "- tapOn:\n    id: notebook.save\n"
                                 "- takeScreenshot: notebook-after-save\n"
                                 "- assertVisible: 'Note saved on this device.'\n"
                                 "- scrollUntilVisible:\n    element:\n      text: 'Source context matters'\n    direction: DOWN\n"
                                 "- assertVisible: 'Attached to this snapshot'\n"
                                 "- takeScreenshot: notebook-saved\n", encoding="utf-8")
        restore_flow = run / "notebook-reopen.yaml"
        restore_flow.write_text("appId: " + bundle_id + "\n---\n"
                                "- launchApp:\n    permissions:\n      all: deny\n"
                                "- tapOn: 'Reading notebook'\n"
                                "- scrollUntilVisible:\n    element:\n      text: 'Source context matters'\n    direction: DOWN\n"
                                "- assertVisible: 'Attached to this snapshot'\n"
                                "- takeScreenshot: notebook-reopened\n", encoding="utf-8")
        def drive(name, steps):
            path = run / (name + ".yaml")
            path.write_text("appId: " + bundle_id + "\n---\n" + steps, encoding="utf-8")

        def visible(text, timeout=30000):
            return "- extendedWaitUntil:\n    visible: " + json.dumps("(?s).*" + re.escape(text) + ".*") + f"\n    timeout: {timeout}\n"

        def scroll_to(text, direction="DOWN"):
            return "- scrollUntilVisible:\n    element:\n      text: " + json.dumps(re.escape(text)) + "\n    direction: " + direction + "\n"

        changes = [
            ("prepend", ["New introduction.", original_article], "New introduction.", "Reattached to matching text"),
            ("duplicate", ["Duplicate check.", original_article, original_article], "Duplicate check.",
             "Ambiguous attachment. Original quote retained."),
            ("deleted", ["Replacement passage selected explicitly."], "Replacement passage",
             "Passage missing or context changed. Original quote retained."),
        ]
        for version, paragraphs, marker, status in changes:
            drive("notebook-" + version,
                  "- takeScreenshot: notebook-before-dismiss-" + version + "\n"
                  "- tapOn: '^Done$'\n"
                  "- takeScreenshot: notebook-after-dismiss-" + version + "\n"
                  "- tapOn:\n    id: '^BackButton$'\n"
                  "- takeScreenshot: refresh-before-" + version + "\n"
                  "- swipe:\n    from:\n      text: 'Evidence survives the network.*'\n    direction: DOWN\n    duration: 700\n"
                  "- takeScreenshot: refresh-after-" + version + "\n"
                  + visible(marker, 60000)
                  + "- tapOn: 'Evidence survives the network.*'\n"
                  + visible(marker)
                  + "- tapOn: 'Reading notebook'\n"
                  + scroll_to(status)
                  + "- assertVisible: " + json.dumps(re.escape("Original quote: " + original_article)) + "\n"
                  + "- takeScreenshot: notebook-" + version + "\n")
        repair_id = "notebook.repair.${output.noteId}"
        drive("notebook-repair",
              "- scrollUntilVisible:\n    element:\n      id: notebook.captureWhole\n    direction: UP\n"
              "- tapOn:\n    id: notebook.captureWhole\n"
              "- scrollUntilVisible:\n    element:\n      id: " + repair_id + "\n    direction: DOWN\n"
              "- tapOn:\n    id: " + repair_id + "\n"
              + scroll_to("Repairs: 1")
              + "- assertVisible: 'Attached to this snapshot'\n"
              "- takeScreenshot: notebook-repaired\n")
        remove_id = "notebook.remove.${output.noteId}"
        drive("notebook-remove",
              "- scrollUntilVisible:\n    element:\n      id: " + remove_id + "\n    direction: DOWN\n"
              "- tapOn:\n    id: " + remove_id + "\n"
              "- assertVisible: " + json.dumps(re.escape("Remove this note?")) + "\n"
              "- tapOn: '^Remove$'\n"
              + scroll_to("No notes for this article yet.")
              + "- takeScreenshot: notebook-removed\n")
        saved_bytes = None
        saved = None
        notebook_file = None
        phase_controller = PhaseController(NATIVE_PHASES)

        def check_phase(phase):
            nonlocal saved_bytes, saved, notebook_file
            def check():
                nonlocal saved_bytes, saved, notebook_file
                if phase == "baseline":
                    assert any(item["path"] == "/feed.xml" for item in requests), "The actual app did not fetch the controlled RSS source."
                    command(["xcrun", "simctl", "io", simulator, "screenshot", run / "native-article.png"], "simulator.log", timeout=30)
                    phase_controller.ensure_active()
                    assert (run / "native-article.png").read_bytes().startswith(PNG_SIGNATURE)
                    result["actions"].append({"action": "native-read", "feedRequests": len(requests), "screenshot": "native-article.png"})
                elif phase == "notebook":
                    container = Path(output(["xcrun", "simctl", "get_app_container", simulator, bundle_id, "data"]).decode().strip())
                    phase_controller.ensure_active()
                    notebook_file = container / "Library/Application Support/FeedmarkNotebook/notes.json"
                    saved_bytes = notebook_file.read_bytes()
                    (run / "notebook-saved.json").write_bytes(saved_bytes)
                    saved = json.loads(saved_bytes)
                    assert saved["version"] == 1 and len(saved["notes"]) == 1
                    assert saved["notes"][0]["note"] == "Source context matters"
                    assert saved["notes"][0]["anchors"][0]["quote"] == original_article
                    result["actions"].append({"action": "native-save-note", "notes": 1, "originalQuoteRetained": True})
                    command(["xcrun", "simctl", "terminate", simulator, bundle_id], "simulator.log", timeout=30)
                    return {"noteId": saved["notes"][0]["id"]}
                elif phase == "reopen":
                    assert notebook_file.read_bytes() == saved_bytes, "Restart changed the saved note."
                    (run / "notebook-reopened.json").write_bytes(notebook_file.read_bytes())
                    result["actions"].append({"action": "native-note-restart", "exactFileRetained": True})
                    with fixture_lock:
                        fixture.update(version=changes[0][0], paragraphs=changes[0][1])
                elif phase in ("prepend", "duplicate", "deleted"):
                    assert any(item["path"] == "/feed.xml" and item["version"] == phase for item in requests), "No actual RSS fetch for " + phase
                    assert notebook_file.read_bytes() == saved_bytes, "Attachment inspection changed the original note."
                    (run / ("notebook-" + phase + ".json")).write_bytes(notebook_file.read_bytes())
                    change_index = next(index for index, change in enumerate(changes) if change[0] == phase)
                    result["actions"].append({"action": "native-refresh-attachment", "version": phase,
                                              "visibleStatus": changes[change_index][3], "originalArchiveUnchanged": True})
                    if change_index + 1 < len(changes):
                        with fixture_lock:
                            fixture.update(version=changes[change_index + 1][0], paragraphs=changes[change_index + 1][1])
                elif phase == "repair":
                    repaired_bytes = notebook_file.read_bytes()
                    repaired = json.loads(repaired_bytes)["notes"][0]
                    original_note = saved["notes"][0]
                    assert repaired["id"] == original_note["id"] and repaired["key"] == original_note["key"]
                    assert repaired["originalText"] == original_note["originalText"] and repaired["note"] == original_note["note"]
                    assert len(repaired["anchors"]) == 2 and repaired["anchors"][0] == original_note["anchors"][0]
                    assert repaired["anchors"][-1]["quote"] == "Replacement passage selected explicitly."
                    (run / "notebook-repaired.json").write_bytes(repaired_bytes)
                    result["actions"].append({"action": "native-repair-note", "priorAnchorRetained": True, "repairs": 1})
                elif phase == "remove":
                    removed_bytes = notebook_file.read_bytes()
                    removed = json.loads(removed_bytes)
                    assert removed["version"] == 1 and removed["notes"] == []
                    (run / "notebook-removed.json").write_bytes(removed_bytes)
                    result["actions"].append({"action": "native-remove-note", "persistedNotes": 0})
                return {}
            return phase_controller.advance(phase, check)

        phase_callback = check_phase
        (run / "campaign-evidence").mkdir()
        (run / "campaign-evidence/phase.js").write_text(
            "var r = http.post(" + json.dumps("http://127.0.0.1:" + str(port) + "/__verify/" + callback_token + "/") +
            " + PHASE, {body: ''});\nif (!r.ok) throw new Error('Phase failed: ' + PHASE + ' ' + r.body);\n"
            "var data = json(r.body);\nif (data.noteId) output.noteId = data.noteId;\n", encoding="utf-8")
        phase_files = ("baseline", "notebook", "notebook-reopen", "notebook-prepend", "notebook-duplicate",
                       "notebook-deleted", "notebook-repair", "notebook-remove")
        campaign = "appId: " + bundle_id + "\n---\n"
        for phase, filename in zip(NATIVE_PHASES, phase_files):
            campaign += "- runFlow: " + filename + ".yaml\n- runScript:\n    file: campaign-evidence/phase.js\n    env:\n      PHASE: " + phase + "\n"
        (run / "campaign.yaml").write_text(campaign, encoding="utf-8")
        argv = [maestro, "--device", simulator, "test", "--format", "JUNIT", "--output", run / "campaign-junit.xml",
                "--debug-output", run / "maestro-evidence", "--test-output-dir", run / "native-artifacts", run / "campaign.yaml"]
        print("RUN " + " ".join(str(part) for part in argv), flush=True)
        with (run / "maestro.log").open("ab") as log:
            process = subprocess.Popen([str(part) for part in argv], cwd=ROOT, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            try:
                phase_controller.start(process)
                returncode = process.wait()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                result["nativePhases"] = phase_controller.events
                phase_controller.close()
        if returncode:
            raise subprocess.CalledProcessError(returncode, argv)
        native_checks_passed = True
    except BaseException as error:
        result["error"] = str(error)
        if phase_controller:
            result["failurePhaseCallbackStopped"] = phase_controller.callback_done.wait(timeout=30)
        if simulator and bundle_id and result.get("failurePhaseCallbackStopped", True):
            try:
                container = Path(output(["xcrun", "simctl", "get_app_container", simulator, bundle_id, "data"]).decode().strip())
                notebook_file = container / "Library/Application Support/FeedmarkNotebook/notes.json"
                result["failureNotebookPresent"] = notebook_file.is_file()
                if notebook_file.is_file():
                    (run / "notebook-at-failure.json").write_bytes(notebook_file.read_bytes())
            except (OSError, subprocess.SubprocessError) as diagnostic_error:
                result["failureNotebookDiagnosticError"] = str(diagnostic_error)
            try:
                command(["xcrun", "simctl", "spawn", simulator, "log", "show", "--style", "compact", "--last", "15m",
                         "--predicate", 'process == "NetNewsWire"', "--info", "--debug"], "app-at-failure.log", timeout=30)
            except (OSError, subprocess.SubprocessError) as diagnostic_error:
                result["failureAppLogDiagnosticError"] = str(diagnostic_error)
        raise
    finally:
        cleanup = {"originalOpmlRestored": False, "simulatorStopped": not owned_simulators,
                   "fixtureStopped": server is None, "evidenceRetained": False}
        cleanup_errors = []
        if phase_controller and not phase_controller.callback_done.is_set():
            cleanup_errors.append("Native phase callback did not stop after the deadline")
        try:
            fixture_path.write_bytes(original_fixture)
            cleanup["originalOpmlRestored"] = fixture_path.read_bytes() == original_fixture
        except OSError as error:
            cleanup_errors.append("OPML restoration: " + str(error))
        try:
            if owned_simulators:
                for owned_simulator in active_simulators:
                    command(["xcrun", "simctl", "shutdown", owned_simulator], "cleanup.log", timeout=60)
                devices = json.loads(output(["xcrun", "simctl", "list", "devices", "--json"]))
                states = {device["udid"]: device["state"] for group in devices["devices"].values() for device in group}
                cleanup["simulatorStopped"] = simulators_stopped(owned_simulators, states)
        except Exception as error:
            cleanup_errors.append("Simulator shutdown: " + str(error))
        finally:
            try:
                if server:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
                    cleanup["fixtureStopped"] = not thread.is_alive()
            except Exception as error:
                cleanup_errors.append("Fixture shutdown: " + str(error))
            (run / "fixture-requests.json").write_text(json.dumps(requests, indent=2), encoding="utf-8")
            result["missingEvidence"] = missing_evidence(run, not args.build_only, bool(args.native_from))
            cleanup["evidenceRetained"] = not result["missingEvidence"]
            cleanup_passed = finalize_result(result, cleanup, cleanup_errors, native_checks_passed)
            (run / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            if stage_dir and cleanup_passed and result.get("buildAccepted") is True:
                (stage_dir / "build-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print("EVIDENCE " + str(run / "result.json"), flush=True)
        if not cleanup_passed:
            raise RuntimeError("Cleanup did not complete. Inspect result.json and cleanup.log.")


if __name__ == "__main__":
    main()
