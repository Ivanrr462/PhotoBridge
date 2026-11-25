import http.client
import json
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path

from photobridge.config import Config
from photobridge.desktop_ui import STALLED_SECONDS, DesktopController, device_label, qr_svg
from photobridge.server import PhotoBridgeServer
from photobridge.storage import Storage
from photobridge.thumbs import encode_png

TOKEN = "tok"


class HelpersTest(unittest.TestCase):
    def test_device_label(self):
        self.assertEqual(device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"), "iPhone")
        self.assertEqual(device_label("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)"), "iPad")
        self.assertEqual(device_label("Mozilla/5.0 (Linux; Android 14; K) Chrome/120 Mobile"), "Android")
        self.assertEqual(device_label(""), "Móvil")

    def test_qr_svg(self):
        svg = qr_svg("http://192.168.1.2:8765/?t=abc")
        self.assertTrue(svg.startswith(b"<svg"))
        self.assertIn(b"<path", svg)

    def test_png_encoder(self):
        rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])  # 2x2
        png = encode_png(2, 2, rgb)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        idat_start = png.index(b"IDAT") + 4
        idat_len = int.from_bytes(png[idat_start - 8 : idat_start - 4], "big")
        raw = zlib.decompress(png[idat_start : idat_start + idat_len])
        self.assertEqual(raw, b"\x00" + rgb[:6] + b"\x00" + rgb[6:])


class DesktopRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name)
        storage = Storage(self.dest)
        config = Config(token=TOKEN, port=0, dest_dir=str(self.dest), check_updates=False)
        self.server = PhotoBridgeServer(0, TOKEN, storage)
        self.desktop = DesktopController(config, self.server, storage)
        self.server.start_in_background()
        self.port = self.server.port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, body

    def post(self, path, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, body

    def k(self, path):
        return f"{path}{'&' if '?' in path else '?'}k={self.desktop.key}"

    def upload(self, name, data, upload_id):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST",
            "/api/uploads",
            json.dumps({"id": upload_id, "name": name, "size": len(data)}),
            {"X-Token": TOKEN, "Content-Type": "application/json"},
        )
        conn.getresponse().read()
        conn.request("PUT", f"/api/uploads/{upload_id}?offset=0", data, {"X-Token": TOKEN})
        conn.getresponse().read()
        conn.close()

    def test_requires_the_desktop_key(self):
        self.assertEqual(self.get("/desktop")[0], 403)
        self.assertEqual(self.get("/desktop?k=mala")[0], 403)
        self.assertEqual(self.get(f"/desktop/events?k={TOKEN}")[0], 403)  # la clave del móvil no sirve
        status, body = self.get(self.k("/desktop"))
        self.assertEqual(status, 200)
        self.assertIn("Fotos recibidas".encode(), body)

    def test_live_events(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", self.k("/desktop/events"))
        stream = conn.getresponse()
        self.assertEqual(stream.getheader("Content-Type"), "text/event-stream; charset=utf-8")

        def next_event():
            kind, data = None, None
            while True:
                line = stream.fp.readline().decode().rstrip("\n")
                if line.startswith("event: "):
                    kind = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                elif line == "" and kind:
                    return kind, data

        kind, state = next_event()
        self.assertEqual(kind, "state")
        self.assertEqual(state["items"], [])
        self.assertEqual(state["devices"], [])

        # Un iPhone abre la página del QR
        self.get(f"/?t={TOKEN}", {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"})
        self.assertEqual(next_event(), ("devices", {"devices": ["iPhone"], "new": "iPhone"}))

        # ...y envía un vídeo
        self.upload("IMG_1.MOV", b"x" * 100, "a" * 32)
        kind, progress = next_event()
        self.assertEqual(kind, "progress")
        self.assertEqual(progress["active"][0]["name"], "IMG_1.MOV")
        kind, data = next_event()
        self.assertEqual(kind, "item")
        self.assertEqual(data["item"]["kind"], "video")
        self.assertEqual(data["item"]["name"], "IMG_1.MOV")
        self.assertEqual(data["active"], [])
        conn.close()
        self.assertTrue(self.desktop.ever_connected)

    def test_thumbnails_and_actions(self):
        self.assertEqual(self.get(self.k("/desktop/thumb/1"))[0], 404)
        self.assertEqual(self.post(self.k("/desktop/action"), {"action": "open", "id": 99})[0], 404)
        self.assertEqual(self.post(self.k("/desktop/action"), {"action": "desconocida"})[0], 400)
        self.assertEqual(self.post("/desktop/action", {"action": "open_folder"})[0], 403)

    @unittest.skipUnless(sys.platform == "win32", "las miniaturas las genera Windows")
    def test_photo_thumbnail_is_png(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow (requirements-dev) no está instalado")
        source = Path(self.tmp.name) / "origen.jpg"
        Image.new("RGB", (640, 480), (200, 40, 40)).save(source)
        self.upload("foto.jpg", source.read_bytes(), "b" * 32)
        status, body = self.get(self.k("/desktop/thumb/1"))
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_stalled_uploads_are_removed(self):
        self.desktop.on_server_event("progress", {"id": "x", "name": "a.mov", "offset": 1, "size": 10})
        self.assertEqual(len(self.desktop.snapshot()["active"]), 1)
        self.assertFalse(self.desktop.prune_stalled())
        self.assertTrue(self.desktop.prune_stalled(time.monotonic() + STALLED_SECONDS + 1))
        self.assertEqual(self.desktop.snapshot()["active"], [])


if __name__ == "__main__":
    unittest.main()
