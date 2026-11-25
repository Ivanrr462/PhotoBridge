"""Servidor HTTP local que sirve la web del móvil y recibe los archivos."""

from __future__ import annotations

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .network import computer_name, is_allowed_client
from .storage import IncompleteUpload, NotEnoughSpace, Storage

WEB_DIR = Path(__file__).parent / "web"
STATIC_FILES = {
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "icon.svg": "image/svg+xml",
    "favicon.svg": "image/svg+xml",
    "apple-touch-icon.png": "image/png",
}

INVALID_LINK_PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PhotoBridge</title><link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/styles.css"></head>
<body><main class="card"><h1>Enlace no válido</h1>
<p>Vuelve a escanear el código QR que aparece en la ventana de PhotoBridge de tu ordenador.</p>
</main></body></html>"""

EventCallback = Callable[[str, dict], None]


class PhotoBridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, token: str, storage: Storage, on_event: EventCallback | None = None):
        self.token = token
        self.storage = storage
        self.on_event = on_event or (lambda kind, data: None)
        try:
            super().__init__(("0.0.0.0", port), RequestHandler)
        except OSError:
            # El puerto preferido está ocupado: usamos uno libre cualquiera.
            super().__init__(("0.0.0.0", 0), RequestHandler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def start_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="photobridge-http", daemon=True)
        thread.start()
        return thread


class RequestHandler(BaseHTTPRequestHandler):
    server: PhotoBridgeServer
    server_version = f"PhotoBridge/{__version__}"
    timeout = 60  # si el móvil se queda colgado (pantalla bloqueada) cortamos

    # -- utilidades -------------------------------------------------------

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # silencio: la ventana ya muestra lo importante

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _token_ok(self, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate.encode(), self.server.token.encode())

    def _client_allowed(self) -> bool:
        if is_allowed_client(self.client_address[0]):
            return True
        self._json(403, {"error": "forbidden"})
        return False

    # -- rutas ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._client_allowed():
            return
        url = urlsplit(self.path)
        if url.path == "/":
            token = parse_qs(url.query).get("t", [None])[0]
            if self._token_ok(token):
                self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(403, INVALID_LINK_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path.startswith("/static/") and url.path[8:] in STATIC_FILES:
            name = url.path[8:]
            self._send(200, (WEB_DIR / name).read_bytes(), STATIC_FILES[name])
        elif url.path == "/api/info":
            if not self._token_ok(self.headers.get("X-Token")):
                self._json(401, {"error": "token"})
                return
            self._json(200, {"computer": computer_name(), "version": __version__})
        else:
            self._json(404, {"error": "not_found"})

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802
        if not self._client_allowed():
            return
        if urlsplit(self.path).path != "/api/upload":
            self._json(404, {"error": "not_found"})
            return
        if not self._token_ok(self.headers.get("X-Token")):
            self._json(401, {"error": "token"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json(411, {"error": "length_required"})
            return
        if length <= 0:
            self._json(400, {"error": "empty"})
            return

        raw_name = unquote(self.headers.get("X-File-Name", ""))
        try:
            last_modified = int(self.headers.get("X-Last-Modified", "0"))
        except ValueError:
            last_modified = 0

        try:
            saved = self.server.storage.save_stream(self.rfile, length, raw_name, last_modified)
        except NotEnoughSpace:
            self.close_connection = True
            self._json(507, {"error": "no_space"})
            self.server.on_event("error", {"message": "No queda espacio en el disco"})
            return
        except (IncompleteUpload, ConnectionError, TimeoutError):
            self.close_connection = True
            return  # el móvil se desconectó; el archivo parcial ya se ha borrado
        except OSError as exc:
            self.close_connection = True
            self._json(500, {"error": "write_failed"})
            self.server.on_event("error", {"message": f"No se pudo guardar el archivo: {exc}"})
            return

        self._json(200, {"savedAs": saved.name})
        self.server.on_event("received", {"path": saved, "size": length})
