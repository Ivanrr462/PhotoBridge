import http.client
import json
import os
import tempfile
import unittest
from pathlib import Path

from photobridge.server import PhotoBridgeServer
from photobridge.storage import PART_SUFFIX, Storage

TOKEN = "token-de-prueba"
ID = "0123456789abcdef0123456789abcdef"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "fotos"
        self.events = []
        storage = Storage(self.dest, index_path=Path(self.tmp.name) / "sent.json")
        self.server = PhotoBridgeServer(0, TOKEN, storage, lambda k, d: self.events.append((k, d)))
        self.server.start_in_background()
        self.conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)

    def tearDown(self):
        self.conn.close()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        try:
            self.conn.request(method, path, body=body, headers=headers or {})
            response = self.conn.getresponse()
        except (http.client.RemoteDisconnected, ConnectionError):
            self.conn.close()
            self.conn.request(method, path, body=body, headers=headers or {})
            response = self.conn.getresponse()
        data = response.read()
        if response.getheader("Connection", "").lower() == "close":
            self.conn.close()
        return response.status, data

    def start(self, size, name="IMG_0001.jpg", token=TOKEN, upload_id=ID):
        body = json.dumps({"id": upload_id, "name": name, "size": size, "lastModified": 1_600_000_000_000})
        status, data = self.request(
            "POST", "/api/uploads", body, {"X-Token": token, "Content-Type": "application/json"}
        )
        return status, (json.loads(data) if data else {})

    def put(self, offset, chunk, token=TOKEN, upload_id=ID):
        status, data = self.request("PUT", f"/api/uploads/{upload_id}?offset={offset}", chunk, {"X-Token": token})
        return status, (json.loads(data) if data else {})

    def visible_files(self):
        return sorted(p.name for p in self.dest.iterdir() if not p.name.endswith(PART_SUFFIX))

    # -- página y estáticos ---------------------------------------------------

    def test_index_requires_token(self):
        status, body = self.request("GET", f"/?t={TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn(b"Elegir fotos", body)

        status, body = self.request("GET", "/?t=malo")
        self.assertEqual(status, 403)
        self.assertIn("Enlace no válido".encode(), body)

    def test_static_files(self):
        self.assertEqual(self.request("GET", "/static/app.js")[0], 200)
        self.assertEqual(self.request("GET", "/static/../server.py")[0], 404)

    def test_info(self):
        status, body = self.request("GET", "/api/info", headers={"X-Token": TOKEN})
        self.assertEqual(status, 200)
        self.assertIn("computer", json.loads(body))
        self.assertEqual(self.request("GET", "/api/info")[0], 401)

    # -- subidas ------------------------------------------------------------------

    def test_chunked_upload_on_one_connection(self):
        data = os.urandom(2_500_000)
        status, state = self.start(len(data), name="Cumpleaños 🎂.jpg")
        self.assertEqual((status, state["offset"], state["done"]), (200, 0, False))
        offset = 0
        while not state["done"]:
            status, state = self.put(offset, data[offset : offset + 1_000_000])
            self.assertEqual(status, 200)
            offset = state["offset"]
        self.assertEqual(state["savedAs"], "Cumpleaños 🎂.jpg")
        self.assertEqual((self.dest / state["savedAs"]).read_bytes(), data)
        self.assertEqual([k for k, _ in self.events], ["received"])

    def test_resume_returns_current_offset(self):
        self.start(100)
        self.put(0, b"x" * 30)
        status, state = self.start(100)
        self.assertEqual((status, state["offset"]), (200, 30))

    def test_wrong_offset_gets_409_with_real_offset(self):
        self.start(100)
        self.put(0, b"x" * 30)
        status, state = self.put(0, b"x" * 30)
        self.assertEqual((status, state["offset"]), (409, 30))

    def test_unknown_upload_is_404(self):
        status, state = self.put(0, b"x", upload_id="f" * 32)
        self.assertEqual((status, state["error"]), (404, "unknown_upload"))

    def test_already_sent_file_is_skipped(self):
        self.start(3)
        self.put(0, b"abc")
        status, state = self.start(3)
        self.assertEqual(status, 200)
        self.assertTrue(state["done"] and state["skipped"])
        self.assertEqual(self.visible_files(), ["IMG_0001.jpg"])
        self.assertEqual([k for k, _ in self.events], ["received", "skipped"])

    def test_wrong_token_is_rejected(self):
        self.assertEqual(self.start(10, token="malo")[0], 401)
        self.assertEqual(self.put(0, b"x", token="malo")[0], 401)
        self.assertFalse(self.dest.exists() and any(self.dest.iterdir()))

    def test_bad_requests(self):
        self.assertEqual(self.request("POST", "/api/uploads", b"{no json", {"X-Token": TOKEN})[0], 400)
        self.assertEqual(self.start(10, upload_id="../../x")[0], 400)
        self.assertEqual(self.start(0)[0], 400)
        self.start(10)
        self.assertEqual(self.put(0, b"x" * 50)[0], 400)


if __name__ == "__main__":
    unittest.main()
