import http.client
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from photobridge.server import PhotoBridgeServer
from photobridge.storage import Storage

TOKEN = "token-de-prueba"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name)
        self.events = []
        self.server = PhotoBridgeServer(0, TOKEN, Storage(self.dest), lambda k, d: self.events.append((k, d)))
        self.server.start_in_background()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    def upload(self, name, data, token=TOKEN):
        return self.request(
            "POST",
            "/api/upload",
            body=data,
            headers={"X-Token": token, "X-File-Name": quote(name), "X-Last-Modified": "1600000000000"},
        )

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

    def test_upload_saves_file(self):
        data = bytes(range(256)) * 10_000
        status, body = self.upload("Cumpleaños 🎂.jpg", data)
        self.assertEqual(status, 200)
        saved = self.dest / json.loads(body)["savedAs"]
        self.assertEqual(saved.name, "Cumpleaños 🎂.jpg")
        self.assertEqual(saved.read_bytes(), data)
        self.assertEqual(self.events[0][0], "received")

    def test_upload_with_wrong_token_is_rejected(self):
        status, _ = self.upload("foto.jpg", b"datos", token="malo")
        self.assertEqual(status, 401)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_same_name_twice_keeps_both(self):
        self.upload("foto.jpg", b"1")
        self.upload("foto.jpg", b"2")
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["foto (1).jpg", "foto.jpg"])


if __name__ == "__main__":
    unittest.main()
