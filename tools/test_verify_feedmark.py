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


if __name__ == "__main__":
    unittest.main()
