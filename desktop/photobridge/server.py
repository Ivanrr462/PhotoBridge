"""Servidor HTTP local que sirve la web del móvil y recibe los archivos.

Protocolo de subida (reanudable, por trozos):

1. ``POST /api/uploads`` con ``{"id", "name", "size", "lastModified"}`` abre o retoma el
   envío y responde con ``offset`` (bytes ya guardados). Si el archivo ya se había recibido
   antes responde ``done: true, skipped: true`` y no hace falta enviarlo.
2. ``PUT /api/uploads/<id>?offset=N`` con un trozo del archivo como cuerpo. Responde con el
   nuevo ``offset``; en el último trozo, además, ``done: true`` y ``savedAs``.
   Si ``N`` no coincide con lo que hay guardado responde ``409`` con el ``offset`` correcto.
"""

from __future__ import annotations

import hmac
import json
import re
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .network import computer_name, is_allowed_client
from .storage import (
    IncompleteUpload,
    InvalidUpload,
    NotEnoughSpace,
    OffsetMismatch,
    Storage,
    UnknownUpload,
    UploadState,
)

WEB_DIR = Path(__file__).parent / "web"
STATIC_FILES = {
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "icon.svg": "image/svg+xml",
    "favicon.svg": "image/svg+xml",
    "apple-touch-icon.png": "image/png",
    "desktop.css": "text/css; charset=utf-8",
    "desktop.js": "text/javascript; charset=utf-8",
}
UPLOAD_PATH = re.compile(r"^/api/uploads/([0-9a-f]{16,64})$")
MAX_JSON_BODY = 16 * 1024
SOCKET_BUFFER = 4 * 1024 * 1024

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
    request_queue_size = 32

    def __init__(self, port: int, token: str, storage: Storage, on_event: EventCallback | None = None):
        self.token = token
        self.storage = storage
        self.on_event = on_event or (lambda kind, data: None)
        self.desktop = None  # DesktopController cuando la ventana es la interfaz HTML
        try:
            super().__init__(("0.0.0.0", port), RequestHandler)
        except OSError:
            # El puerto preferido está ocupado: usamos uno libre cualquiera.
            super().__init__(("0.0.0.0", 0), RequestHandler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def handle_error(self, request, client_address) -> None:
        # Los navegadores cierran conexiones preparadas o a medias: no es un error de verdad
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def start_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="photobridge-http", daemon=True)
        thread.start()
        return thread


class RequestHandler(BaseHTTPRequestHandler):
    server: PhotoBridgeServer
    server_version = f"PhotoBridge/{__version__}"
    protocol_version = "HTTP/1.1"  # conexiones persistentes: menos latencia entre trozos
    timeout = 60  # si el móvil se queda colgado (pantalla bloqueada) cortamos
    rbufsize = 1024 * 1024

    def setup(self) -> None:
        try:
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER)
        except OSError:
            pass
        super().setup()

    # -- utilidades -------------------------------------------------------

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # silencio: la ventana ya muestra lo importante

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.close_connection:
            self.send_header("Connection", "close")
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
        self.close_connection = True
        self._json(403, {"error": "forbidden"})
        return False

    def _authorized(self) -> bool:
        if self._token_ok(self.headers.get("X-Token")):
            return True
        self._discard_body()
        self._json(401, {"error": "token"})
        return False

    def _content_length(self) -> int | None:
        try:
            return int(self.headers.get("Content-Length", ""))
        except ValueError:
            return None

    def _discard_body(self) -> None:
        """No leemos cuerpos que rechazamos: cerramos la conexión para no desincronizarla."""
        if self._content_length():
            self.close_connection = True

    def _state_payload(self, state: UploadState) -> dict:
        payload: dict = {"offset": state.offset, "done": state.done}
        if state.saved is not None:
            payload["savedAs"] = state.saved.name
        if state.skipped:
            payload["skipped"] = True
        return payload

    # -- rutas ------------------------------------------------------------

    def _desktop_route(self, url) -> bool:
        """Rutas de la ventana del ordenador: solo desde este equipo y con su propia clave."""
        if not (url.path == "/desktop" or url.path.startswith("/desktop/")):
            return False
        desktop = self.server.desktop
        key = parse_qs(url.query).get("k", [""])[0]
        loopback = self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
        if desktop is None or not loopback or not hmac.compare_digest(key.encode(), desktop.key.encode()):
            self._discard_body()
            self._json(403, {"error": "forbidden"})
            return True
        desktop.handle(self, url)
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._client_allowed():
            return
        url = urlsplit(self.path)
        if self._desktop_route(url):
            return
        if url.path == "/":
            token = parse_qs(url.query).get("t", [None])[0]
            if self._token_ok(token):
                self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
                self.server.on_event(
                    "connected", {"ip": self.client_address[0], "ua": self.headers.get("User-Agent", "")}
                )
            else:
                self._send(403, INVALID_LINK_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path.startswith("/static/") and url.path[8:] in STATIC_FILES:
            name = url.path[8:]
            self._send(200, (WEB_DIR / name).read_bytes(), STATIC_FILES[name])
        elif url.path == "/api/info":
            if self._authorized():
                self._json(200, {"computer": computer_name(), "version": __version__})
        else:
            self._json(404, {"error": "not_found"})

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802
        if not self._client_allowed():
            return
        if self._desktop_route(urlsplit(self.path)):
            return
        if urlsplit(self.path).path != "/api/uploads":
            self._discard_body()
            self._json(404, {"error": "not_found"})
            return
        if not self._authorized():
            return

        length = self._content_length()
        if length is None or not 0 < length <= MAX_JSON_BODY:
            self.close_connection = True
            self._json(400, {"error": "bad_request"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            state = self.server.storage.start(
                str(body["id"]), str(body.get("name", "")), int(body["size"]), int(body.get("lastModified") or 0)
            )
        except (ValueError, KeyError, TypeError, InvalidUpload):
            self._json(400, {"error": "bad_request"})
            return
        except NotEnoughSpace:
            self._json(507, {"error": "no_space"})
            self.server.on_event("error", {"message": "No queda espacio en el disco"})
            return
        except OSError as exc:
            self._json(500, {"error": "write_failed"})
            self.server.on_event("error", {"message": f"No se pudo preparar el archivo: {exc}"})
            return

        upload_id = str(body["id"])
        if state.skipped:
            self.server.on_event("skipped", {"path": state.saved, "id": upload_id})
        else:
            self.server.on_event(
                "progress", {"id": upload_id, "name": state.name, "offset": state.offset, "size": state.size}
            )
        self._json(200, self._state_payload(state))

    def do_PUT(self) -> None:  # noqa: N802
        if not self._client_allowed():
            return
        url = urlsplit(self.path)
        match = UPLOAD_PATH.match(url.path)
        if not match:
            self._discard_body()
            self._json(404, {"error": "not_found"})
            return
        if not self._authorized():
            return

        length = self._content_length()
        try:
            offset = int(parse_qs(url.query)["offset"][0])
        except (KeyError, ValueError, IndexError):
            offset = -1
        if length is None or length <= 0 or offset < 0:
            self.close_connection = True
            self._json(400, {"error": "bad_request"})
            return

        try:
            state = self.server.storage.append(match.group(1), offset, self.rfile, length)
        except OffsetMismatch as exc:
            self.close_connection = True
            self._json(409, {"error": "offset", "offset": exc.offset})
            return
        except UnknownUpload:
            self.close_connection = True
            self._json(404, {"error": "unknown_upload"})
            return
        except InvalidUpload:
            self.close_connection = True
            self._json(400, {"error": "bad_request"})
            return
        except (IncompleteUpload, ConnectionError, TimeoutError):
            self.close_connection = True
            return  # el móvil se desconectó; lo recibido se conserva y se retomará
        except OSError as exc:
            self.close_connection = True
            self._json(500, {"error": "write_failed"})
            self.server.on_event("error", {"message": f"No se pudo guardar el archivo: {exc}"})
            return

        if state.saved is not None:
            self.server.on_event("received", {"path": state.saved, "size": state.offset, "id": match.group(1)})
        else:
            self.server.on_event(
                "progress", {"id": match.group(1), "name": state.name, "offset": state.offset, "size": state.size}
            )
        self._json(200, self._state_payload(state))
