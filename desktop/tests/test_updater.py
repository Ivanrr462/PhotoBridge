import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from photobridge import updater

NEW_EXE = b"MZ" + b"nuevo-photobridge" * 5000


class FakeGitHub(BaseHTTPRequestHandler):
    """Imita /releases/latest y la descarga del asset."""

    release: dict = {}
    status = 200

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/latest":
            body = json.dumps(self.release).encode()
            self.send_response(self.status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/PhotoBridge.exe":
            self.send_response(200)
            self.send_header("Content-Length", str(len(NEW_EXE)))
            self.end_headers()
            self.wfile.write(NEW_EXE)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


class VersionTest(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(updater.parse_version("v0.2.0"), (0, 2, 0))
        self.assertEqual(updater.parse_version("1.10"), (1, 10))
        self.assertEqual(updater.parse_version("v2.0.0-beta"), (2, 0, 0))
        self.assertIsNone(updater.parse_version("latest"))

    def test_is_newer(self):
        self.assertTrue(updater.is_newer("0.3.0", "0.2.0"))
        self.assertTrue(updater.is_newer("0.10.0", "0.9.9"))
        self.assertTrue(updater.is_newer("1.0", "0.9.9"))
        self.assertFalse(updater.is_newer("0.2.0", "0.2.0"))
        self.assertFalse(updater.is_newer("0.2", "0.2.0"))
        self.assertFalse(updater.is_newer("0.1.9", "0.2.0"))
        self.assertFalse(updater.is_newer("raro", "0.2.0"))


class ReleaseServerTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        FakeGitHub.status = 200
        FakeGitHub.release = {
            "tag_name": "v9.0.0",
            "html_url": "https://example.invalid/release",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": "otro.zip", "browser_download_url": f"{self.base}/otro.zip", "size": 1},
                {
                    "name": "PhotoBridge.exe",
                    "browser_download_url": f"{self.base}/PhotoBridge.exe",
                    "size": len(NEW_EXE),
                    "digest": "sha256:" + hashlib.sha256(NEW_EXE).hexdigest(),
                },
            ],
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.exe = Path(self.tmp.name) / "PhotoBridge.exe"
        self.exe.write_bytes(b"MZ-version-vieja")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_newer_release_is_found(self):
        release = updater.check_for_update("0.2.0", api_url=f"{self.base}/latest")
        self.assertEqual(release.version, "9.0.0")
        self.assertEqual(release.size, len(NEW_EXE))
        self.assertEqual(release.sha256, hashlib.sha256(NEW_EXE).hexdigest())

    def test_no_update_when_current_or_unavailable(self):
        self.assertIsNone(updater.check_for_update("9.0.0", api_url=f"{self.base}/latest"))
        FakeGitHub.release["prerelease"] = True
        self.assertIsNone(updater.check_for_update("0.1.0", api_url=f"{self.base}/latest"))
        FakeGitHub.status = 404  # p. ej. repositorio privado
        self.assertIsNone(updater.check_for_update("0.1.0", api_url=f"{self.base}/latest"))
        self.assertIsNone(updater.check_for_update("0.1.0", api_url="http://127.0.0.1:1/latest"))  # sin conexión

    def test_download_and_install(self):
        release = updater.check_for_update("0.2.0", api_url=f"{self.base}/latest")
        seen = []
        new_file = updater.download(release, self.exe, lambda done, total: seen.append((done, total)))
        self.assertEqual(new_file.read_bytes(), NEW_EXE)
        self.assertEqual(seen[-1], (len(NEW_EXE), len(NEW_EXE)))

        updater.install(new_file, self.exe)
        self.assertEqual(self.exe.read_bytes(), NEW_EXE)
        self.assertEqual(updater.old_copy(self.exe).read_bytes(), b"MZ-version-vieja")
        self.assertFalse(new_file.exists())

        updater.cleanup_previous(self.exe)
        self.assertFalse(updater.old_copy(self.exe).exists())

    def test_corrupted_download_is_rejected(self):
        release = updater.check_for_update("0.2.0", api_url=f"{self.base}/latest")
        release.sha256 = "0" * 64
        with self.assertRaises(updater.UpdateError):
            updater.download(release, self.exe)
        release.sha256 = None
        release.size += 10
        with self.assertRaises(updater.UpdateError):
            updater.download(release, self.exe)
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir()], ["PhotoBridge.exe"])

    def test_failed_install_keeps_the_current_exe(self):
        with self.assertRaises(OSError):
            updater.install(Path(self.tmp.name) / "no-existe.exe", self.exe)
        self.assertEqual(self.exe.read_bytes(), b"MZ-version-vieja")


if __name__ == "__main__":
    unittest.main()
