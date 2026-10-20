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
import subprocess
import threading
import time
import urllib.request
import zipfile
from xml.sax.saxutils import escape as xml_escape


ROOT = Path(__file__).resolve().parents[1]
MAESTRO_VERSION = "cli-2.11.0"
MAESTRO_SHA256 = "5384593cb4e7a106489e75a821d157dd43f4e438df6bc308b72e82c685e1283a"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_name")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or platform.system() != "Darwin":
        parser.error("Use the dedicated macOS hosted workflow. No local native UI automation is configured.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", args.run_name):
        parser.error("Use a short alphanumeric run name.")
    run = ROOT / ".audit" / "verification" / args.run_name
    run.mkdir(parents=True, exist_ok=False)
    result = {"run": args.run_name, "actions": [], "nativeAcceptance": False}
    env = dict(os.environ, PROJECT_DIR=str(ROOT), MAESTRO_CLI_NO_ANALYTICS="true",
               MAESTRO_DISABLE_UPDATE_CHECK="true", MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED="true")
    for key in ("MERCURY_CLIENT_ID", "MERCURY_CLIENT_SECRET", "FEEDLY_CLIENT_ID", "FEEDLY_CLIENT_SECRET",
                "INOREADER_APP_ID", "INOREADER_APP_KEY"):
        env.pop(key, None)
    simulator = None
    server = None
    thread = None
    fixture_path = ROOT / "Shared/Importers/DefaultFeeds.opml"
    original_fixture = fixture_path.read_bytes()
    requests = []
    original_article = "Local synthetic article for native reader verification."
    fixture = {"version": "original", "paragraphs": [original_article]}
    fixture_lock = threading.Lock()

    def command(argv, log_name, timeout=1800):
        print("RUN " + " ".join(str(part) for part in argv), flush=True)
        with (run / log_name).open("ab") as log:
            subprocess.run([str(part) for part in argv], cwd=ROOT, env=env, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def output(argv, timeout=30):
        return subprocess.check_output([str(part) for part in argv], cwd=ROOT, env=env,
                                       timeout=timeout, stdin=subprocess.DEVNULL)

    try:
        command(["xcodebuild", "-version"], "doctor.log", timeout=30)
        command(["java", "-version"], "doctor.log", timeout=15)
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
        result["actions"].append({"action": "doctor", "simulator": simulator, "runtime": runtime["version"]})
        command(["xcrun", "simctl", "boot", simulator], "simulator.log", timeout=60)
        command(["xcrun", "simctl", "bootstatus", simulator, "-b"], "simulator.log", timeout=300)

        class FixtureHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                with fixture_lock:
                    version = fixture["version"]
                    body = "".join("<p>" + escape(paragraph) + "</p>" for paragraph in fixture["paragraphs"])
                    requests.append({"path": self.path, "method": "GET", "version": version})
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
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        fixture_path.write_text("<?xml version='1.0'?><opml version='2.0'><head><title>Verification</title></head><body>"
                                f"<outline text='Feedmark verification feed' title='Feedmark verification feed' type='rss' "
                                f"xmlUrl='http://127.0.0.1:{port}/feed.xml'/></body></opml>", encoding="utf-8")
        result["actions"].append({"action": "fixture", "loopbackPort": port, "originalOpmlSha256": hashlib.sha256(original_fixture).hexdigest()})
        command(["bash", "buildscripts/updateSecrets.sh"], "build.log", timeout=60)
        command(["xcodebuild", "-project", "NetNewsWire.xcodeproj", "-scheme", "NetNewsWire-iOS",
                 "-testPlan", "NetNewsWire-iOS", "-xcconfig", ".github/ios-ci-no-signing.xcconfig",
                 "-destination", "platform=iOS Simulator,id=" + simulator,
                 "-parallel-testing-enabled", "NO",
                 "-derivedDataPath", run / "DerivedData", "-resultBundlePath", run / "baseline.xcresult",
                 "OTHER_SWIFT_FLAGS=$(inherited) -DDEBUG -DSKIP_APP_GROUP_ACCESS", "test"], "build.log")
        command(["bash", "buildscripts/fail_on_warnings.sh", run / "build.log"], "warnings.log", timeout=30)
        app = run / "DerivedData/Build/Products/Debug-iphonesimulator/NetNewsWire.app"
        with (app / "Info.plist").open("rb") as info_file:
            bundle_id = plistlib.load(info_file)["CFBundleIdentifier"]
        command(["xcrun", "simctl", "install", simulator, app], "simulator.log", timeout=60)
        result["actions"].append({"action": "build-and-test", "bundleId": bundle_id, "signing": "disabled"})

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
        command([maestro, "--device", simulator, "test", "--format", "JUNIT", "--output", run / "native-junit.xml",
                 "--debug-output", run / "maestro-evidence", "--test-output-dir", run / "native-artifacts", flow], "maestro.log", timeout=300)
        assert any(item["path"] == "/feed.xml" for item in requests), "The actual app did not fetch the controlled RSS source."
        command(["xcrun", "simctl", "io", simulator, "screenshot", run / "native-article.png"], "simulator.log", timeout=30)
        assert (run / "native-article.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        command([maestro, "--device", simulator, "hierarchy", "--no-reinstall-driver"], "article-hierarchy.log", timeout=60)
        result["actions"].append({"action": "native-read", "feedRequests": len(requests), "screenshot": "native-article.png"})
        notebook_flow = run / "notebook.yaml"
        notebook_flow.write_text("appId: " + bundle_id + "\n---\n"
                                 "- tapOn: 'Reading notebook'\n"
                                 "- assertVisible: 'Capture selected passage'\n"
                                 "- tapOn:\n    id: notebook.captureWhole\n"
                                 "- scrollUntilVisible:\n    element:\n      id: notebook.note\n    direction: DOWN\n"
                                 "- tapOn:\n    id: notebook.note\n"
                                 "- inputText: 'Source context matters'\n"
                                 "- hideKeyboard\n"
                                 "- scrollUntilVisible:\n    element:\n      id: notebook.save\n    direction: DOWN\n"
                                 "- tapOn:\n    id: notebook.save\n"
                                 "- assertVisible: 'Note saved on this device.'\n"
                                 "- scrollUntilVisible:\n    element:\n      text: 'Source context matters'\n    direction: DOWN\n"
                                 "- assertVisible: 'Attached to this snapshot'\n"
                                 "- takeScreenshot: notebook-saved\n", encoding="utf-8")
        command([maestro, "--device", simulator, "test", "--format", "JUNIT", "--output", run / "notebook-junit.xml",
                 "--debug-output", run / "notebook-evidence", "--test-output-dir", run / "native-artifacts", notebook_flow], "maestro.log", timeout=300)
        container = Path(output(["xcrun", "simctl", "get_app_container", simulator, bundle_id, "data"]).decode().strip())
        notebook_file = container / "Library/Application Support/FeedmarkNotebook/notes.json"
        saved_bytes = notebook_file.read_bytes()
        (run / "notebook-saved.json").write_bytes(saved_bytes)
        saved = json.loads(saved_bytes)
        assert saved["version"] == 1 and len(saved["notes"]) == 1
        assert saved["notes"][0]["note"] == "Source context matters"
        assert saved["notes"][0]["anchors"][0]["quote"] == "Local synthetic article for native reader verification."
        result["actions"].append({"action": "native-save-note", "notes": 1, "originalQuoteRetained": True})
        command(["xcrun", "simctl", "terminate", simulator, bundle_id], "simulator.log", timeout=30)
        restore_flow = run / "notebook-reopen.yaml"
        restore_flow.write_text("appId: " + bundle_id + "\n---\n"
                                "- launchApp:\n    permissions:\n      all: deny\n"
                                "- tapOn: 'Reading notebook'\n"
                                "- scrollUntilVisible:\n    element:\n      text: 'Source context matters'\n    direction: DOWN\n"
                                "- assertVisible: 'Attached to this snapshot'\n"
                                "- takeScreenshot: notebook-reopened\n", encoding="utf-8")
        command([maestro, "--device", simulator, "test", "--format", "JUNIT", "--output", run / "reopen-junit.xml",
                 "--debug-output", run / "reopen-evidence", "--test-output-dir", run / "native-artifacts", restore_flow], "maestro.log", timeout=300)
        assert notebook_file.read_bytes() == saved_bytes, "Restart changed the saved note."
        (run / "notebook-reopened.json").write_bytes(notebook_file.read_bytes())
        result["actions"].append({"action": "native-note-restart", "exactFileRetained": True})
        command([maestro, "--device", simulator, "hierarchy", "--no-reinstall-driver"], "notebook-hierarchy.log", timeout=60)
        result["nativeAcceptance"] = True
    except BaseException as error:
        result["error"] = str(error)
        raise
    finally:
        cleanup = {"originalOpmlRestored": False, "simulatorStopped": simulator is None,
                   "fixtureStopped": server is None, "evidenceRetained": run.exists()}
        cleanup_errors = []
        try:
            fixture_path.write_bytes(original_fixture)
            cleanup["originalOpmlRestored"] = fixture_path.read_bytes() == original_fixture
        except OSError as error:
            cleanup_errors.append("OPML restoration: " + str(error))
        try:
            if simulator:
                command(["xcrun", "simctl", "shutdown", simulator], "cleanup.log", timeout=60)
                devices = json.loads(output(["xcrun", "simctl", "list", "devices", "--json"]))
                owned = next(device for group in devices["devices"].values() for device in group if device["udid"] == simulator)
                cleanup["simulatorStopped"] = owned["state"] == "Shutdown"
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
            result["cleanup"] = cleanup
            result["cleanupErrors"] = cleanup_errors
            (run / "fixture-requests.json").write_text(json.dumps(requests, indent=2), encoding="utf-8")
            (run / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print("EVIDENCE " + str(run / "result.json"), flush=True)
        if cleanup_errors or not all(cleanup.values()):
            raise RuntimeError("Cleanup did not complete. Inspect result.json and cleanup.log.")


if __name__ == "__main__":
    main()
