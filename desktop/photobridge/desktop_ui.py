"""Ventana del ordenador hecha con HTML, mostrada en Edge en modo aplicación.

El propio servidor de PhotoBridge sirve la página en ``/desktop`` (solo para este equipo y
con una clave aleatoria por sesión) y le envía los cambios en directo con Server-Sent
Events: móviles conectados, archivos que llegan, progreso, actualizaciones...

Edge (o Chrome) se abre con ``--app`` y un perfil propio, así que se ve como una ventana
normal sin pestañas ni barra de direcciones. Si no hay navegador compatible, o la ventana
no llega a conectarse, se usa la ventana clásica de Tkinter (gui.py).
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import SplitResult

from . import __version__, updater
from .config import Config
from .network import get_lan_ip
from .qr import qr_matrix
from .server import WEB_DIR, PhotoBridgeServer
from .storage import Storage
from .thumbs import init_thread, windows_thumbnail
from .trash import TrashError, send_to_trash

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".3gp", ".avi", ".mkv", ".webm"}
BROWSER_IMAGES = {".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".bmp", ".avif"}  # Edge las muestra tal cual
THUMB_SIZE = 360
PREVIEW_SIZE = 1800  # vista previa para formatos que Edge no abre (HEIC, RAW...): la genera Windows
FILE_CHUNK = 256 * 1024
VIDEO_TYPES = {".mov": "video/mp4", ".m4v": "video/mp4"}  # Edge reproduce el MOV de H.264 si se anuncia como MP4
KEEPALIVE_SECONDS = 2.0
CLOSE_GRACE_SECONDS = 3.5  # sin ventana conectada durante este tiempo -> cerrar el programa
CONNECT_TIMEOUT_SECONDS = 25  # si la ventana no aparece, se usa la de Tkinter
STALLED_SECONDS = 45  # un envío sin avances en este tiempo deja de mostrarse como "recibiendo"
WINDOW_SIZE = (1180, 820)


def device_label(user_agent: str) -> str:
    ua = user_agent or ""
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua or ("Macintosh" in ua and "Mobile" in ua):
        return "iPad"
    if "Android" in ua:
        return "Android"
    return "Móvil"


def qr_svg(data: str) -> bytes:
    matrix = qr_matrix(data, border=2)
    size = len(matrix)
    path = "".join(f"M{x} {y}h1v1h-1z" for y, row in enumerate(matrix) for x, on in enumerate(row) if on)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/><path d="{path}" fill="#0f1222"/></svg>'
    )
    return svg.encode()


class DesktopController:
    def __init__(self, config: Config, server: PhotoBridgeServer, storage: Storage):
        self.config = config
        self.server = server
        self.storage = storage
        self.key = secrets.token_urlsafe(18)
        self.requests: queue.Queue[tuple[str, dict]] = queue.Queue()  # tareas para el hilo principal

        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._items: list[dict] = []
        self._paths: dict[int, Path] = {}
        self._next_id = 1
        self.trash = send_to_trash  # se puede sustituir en los tests
        self._thumbs: dict[int, bytes | None] = {}
        self._devices: dict[str, str] = {}
        self._active: dict[str, dict] = {}
        self._phone_url: str | None = None
        self._release: updater.Release | None = None
        self._update_busy = False

        self.ever_connected = False
        self.last_seen = time.monotonic()

        server.desktop = self
        server.on_event = self.on_server_event

    # -- estado y eventos ------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": __version__,
                "items": list(self._items),
                "devices": list(self._devices.values()),
                "active": [{k: v for k, v in a.items() if k != "seen"} for a in self._active.values()],
                "folder": str(self.storage.dest_dir),
                "phoneUrl": self._phone_url,
                "update": self._update_payload(),
            }

    def _update_payload(self) -> dict | None:
        if not self._release:
            return None
        return {"version": self._release.version, "current": __version__, "busy": self._update_busy}

    def publish(self, kind: str, data: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.put((kind, data))

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def on_server_event(self, kind: str, data: dict) -> None:
        if kind == "connected":
            label = device_label(data.get("ua", ""))
            with self._lock:
                is_new = data.get("ip") not in self._devices
                self._devices[data.get("ip", "?")] = label
                devices = list(self._devices.values())
            self.publish("devices", {"devices": devices, "new": label if is_new else None})
        elif kind == "progress":
            with self._lock:
                self._active[data["id"]] = {
                    "id": data["id"],
                    "name": data["name"],
                    "offset": data["offset"],
                    "size": data["size"],
                    "seen": time.monotonic(),
                }
            self.publish("progress", {"active": self._active_list()})
        elif kind in ("received", "skipped"):
            item = self._add_item(data["path"], skipped=kind == "skipped", size=data.get("size"))
            with self._lock:
                self._active.pop(data.get("id"), None)
            self.publish("item", {"item": item, "active": self._active_list()})
        elif kind == "error":
            self.publish("app_error", {"message": data.get("message", "")})

    def _active_list(self) -> list[dict]:
        with self._lock:
            return [{k: v for k, v in a.items() if k != "seen"} for a in self._active.values()]

    def prune_stalled(self, now: float | None = None) -> bool:
        """Quita del panel los envíos sin actividad (el móvil se fue a mitad)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            stalled = [k for k, a in self._active.items() if now - a["seen"] > STALLED_SECONDS]
            for k in stalled:
                del self._active[k]
        if stalled:
            self.publish("progress", {"active": self._active_list()})
        return bool(stalled)

    def _add_item(self, path: Path, skipped: bool, size: int | None) -> dict | None:
        with self._lock:
            if path in self._paths.values():
                return None
            item_id = self._next_id
            self._next_id += 1
            try:
                stat = path.stat()
                size = size if size is not None else stat.st_size
                taken = int(stat.st_mtime * 1000)  # PhotoBridge pone aquí la fecha de captura
            except OSError:
                size, taken = size or 0, 0
            suffix = path.suffix.lower()
            if suffix in VIDEO_EXTENSIONS:
                kind, view = "video", "video"
            else:
                kind, view = "photo", "image" if suffix in BROWSER_IMAGES else "preview"
            item = {
                "id": item_id,
                "name": path.name,
                "ext": suffix.lstrip(".").upper(),
                "size": size,
                "kind": kind,
                "view": view,
                "taken": taken,
                "skipped": skipped,
            }
            self._items.append(item)
            self._paths[item_id] = path
            return item

    # -- trabajo en segundo plano ------------------------------------------------------

    def watch_network(self) -> None:
        while True:
            ip = get_lan_ip()
            url = f"http://{ip}:{self.server.port}/?t={self.config.token}" if ip else None
            with self._lock:
                changed = url != self._phone_url
                self._phone_url = url
            if changed:
                self.publish("qr", {"phoneUrl": url})
            self.prune_stalled()
            time.sleep(5)

    def check_updates(self) -> None:
        updater.cleanup_previous()
        release = updater.check_for_update()
        if release:
            with self._lock:
                self._release = release
                payload = self._update_payload()
            self.publish("update", payload)

    def start_update(self) -> None:
        with self._lock:
            release = self._release
            if not release or self._update_busy:
                return
            self._update_busy = True
        exe = updater.running_exe()
        if exe is None:  # ejecutándose desde el código fuente
            webbrowser.open(release.page)
            with self._lock:
                self._update_busy = False
            return

        def work() -> None:
            last = -1
            try:
                def progress(done: int, total: int) -> None:
                    nonlocal last
                    percent = int(done * 100 / max(1, total))
                    if percent != last:
                        last = percent
                        self.publish("update_progress", {"percent": percent})

                new_file = updater.download(release, exe, progress)
                self.requests.put(("install", {"file": new_file, "exe": exe}))
            except Exception as exc:  # noqa: BLE001 - se informa en la ventana
                with self._lock:
                    self._update_busy = False
                self.publish("update_error", {"message": str(exc) or exc.__class__.__name__, "page": release.page})

        threading.Thread(target=work, name="photobridge-update", daemon=True).start()

    def path_of(self, item_id: int) -> Path | None:
        with self._lock:
            return self._paths.get(item_id)

    def delete(self, ids: list[int]) -> tuple[list[int], str | None]:
        """Manda a la Papelera los archivos indicados y los quita de la galería."""
        with self._lock:
            targets = {i: self._paths[i] for i in ids if i in self._paths}
        if not targets:
            return [], None
        error = None
        try:
            self.trash(list(targets.values()))
        except (TrashError, OSError) as exc:
            error = str(exc)
        removed = [i for i, path in targets.items() if not path.exists()]
        with self._lock:
            for i in removed:
                self._paths.pop(i, None)
                self._thumbs.pop(i, None)
            self._items = [item for item in self._items if item["id"] not in removed]
        if removed:
            self.publish("removed", {"ids": removed})
        return removed, error

    def preview(self, item_id: int) -> bytes | None:
        path = self.path_of(item_id)
        if path is None:
            return None
        init_thread()
        try:
            thumb = windows_thumbnail(path, PREVIEW_SIZE)
        except Exception:  # noqa: BLE001
            return None
        return thumb.png if thumb else None

    def thumbnail(self, item_id: int) -> bytes | None:
        with self._lock:
            if item_id in self._thumbs:
                return self._thumbs[item_id]
            path = self._paths.get(item_id)
        if path is None:
            return None
        init_thread()
        try:
            thumb = windows_thumbnail(path, THUMB_SIZE)
            png = thumb.png if thumb else None
        except Exception:  # noqa: BLE001 - sin miniatura se muestra el tipo de archivo
            png = None
        with self._lock:
            self._thumbs[item_id] = png
        return png

    # -- HTTP: rutas /desktop ------------------------------------------------------------

    def handle(self, handler, url: SplitResult) -> None:
        method = handler.command
        path = url.path
        if method == "GET" and path == "/desktop":
            handler._send(200, (WEB_DIR / "desktop.html").read_bytes(), "text/html; charset=utf-8")
        elif method == "GET" and path == "/desktop/events":
            self._serve_events(handler)
        elif method == "GET" and path == "/desktop/qr.svg":
            with self._lock:
                url_text = self._phone_url
            if url_text:
                handler._send(200, qr_svg(url_text), "image/svg+xml")
            else:
                handler._json(404, {"error": "no_network"})
        elif method == "GET" and path.startswith("/desktop/thumb/"):
            try:
                png = self.thumbnail(int(path.rsplit("/", 1)[1]))
            except ValueError:
                png = None
            if png:
                handler._send(200, png, "image/png")
            else:
                handler._json(404, {"error": "no_thumbnail"})
        elif method in ("GET", "HEAD") and path.startswith("/desktop/file/"):
            target = self.path_of(_int(path.rsplit("/", 1)[1]))
            if target and target.is_file():
                _serve_file(handler, target)
            else:
                handler._json(404, {"error": "missing"})
        elif method == "GET" and path.startswith("/desktop/preview/"):
            png = self.preview(_int(path.rsplit("/", 1)[1]))
            if png:
                handler._send(200, png, "image/png")
            else:
                handler._json(404, {"error": "no_preview"})
        elif method == "POST" and path == "/desktop/action":
            self._action(handler)
        else:
            handler._json(404, {"error": "not_found"})

    def _action(self, handler) -> None:
        length = handler._content_length() or 0
        try:
            body = json.loads(handler.rfile.read(length)) if 0 < length <= 4096 else {}
        except ValueError:
            body = {}
        action = body.get("action")
        if action == "open":
            with self._lock:
                target = self._paths.get(_int(body.get("id")))
            if not target or not target.exists():
                handler._json(404, {"error": "missing"})
                return
            _open_path(target)
        elif action == "delete":
            ids = [_int(i) for i in body.get("ids", []) if _int(i) > 0][:5000]
            removed, error = self.delete(ids)
            handler._json(200 if not error else 500, {"removed": removed, "error": error})
            return
        elif action == "reveal":
            target = self.path_of(_int(body.get("id")))
            if not target or not target.exists():
                handler._json(404, {"error": "missing"})
                return
            _reveal(target)
        elif action == "open_folder":
            _open_path(self.storage.dest_dir)
        elif action == "choose_folder":
            self.requests.put(("choose_folder", {}))
        elif action == "update":
            self.start_update()
        elif action == "release_page":
            with self._lock:
                page = self._release.page if self._release else updater.RELEASES_PAGE
            webbrowser.open(page)
        else:
            handler._json(400, {"error": "bad_action"})
            return
        handler._json(200, {"ok": True})

    def _serve_events(self, handler) -> None:
        subscriber: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(subscriber)
        self.ever_connected = True
        self.last_seen = time.monotonic()

        handler.close_connection = True
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()

        def send(kind: str, data) -> None:
            payload = json.dumps(data, ensure_ascii=False)
            handler.wfile.write(f"event: {kind}\ndata: {payload}\n\n".encode("utf-8"))
            handler.wfile.flush()

        try:
            send("state", self.snapshot())
            while True:
                try:
                    kind, data = subscriber.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    handler.wfile.write(b": ping\n\n")
                    handler.wfile.flush()
                    continue
                send(kind, data)
                if kind == "closing":
                    break
        except OSError:
            pass  # la ventana se cerró
        finally:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)
            self.last_seen = time.monotonic()


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _serve_file(handler, path: Path) -> None:
    """Envía un archivo con soporte de rangos (Range), necesario para avanzar en los vídeos."""
    size = path.stat().st_size
    suffix = path.suffix.lower()
    content_type = VIDEO_TYPES.get(suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    start, end, status = 0, size - 1, 200
    match = _RANGE.match(handler.headers.get("Range", "").strip())
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
        else:
            start = max(0, size - int(match.group(2)))
        if start > end or start >= size:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        status = 206
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(end - start + 1))
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Cache-Control", "no-store")
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    if handler.command == "HEAD":
        return
    try:
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(FILE_CHUNK, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
    except (ConnectionError, TimeoutError):
        handler.close_connection = True  # el visor pidió otro trozo y cerró este


def _reveal(path: Path) -> None:
    """Abre el Explorador con el archivo seleccionado."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        _open_path(path.parent)


def _open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


# ------------------------------------------------------------------------- ventana de Edge


def find_browser() -> Path | None:
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")]
    candidates = [Path(r) / "Microsoft/Edge/Application/msedge.exe" for r in roots if r]
    candidates += [Path(r) / "Google/Chrome/Application/chrome.exe" for r in roots if r]
    return next((c for c in candidates if c.is_file()), None)


def _window_position(width: int, height: int) -> tuple[int, int] | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        return max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 2 - 20)
    except Exception:  # noqa: BLE001
        return None


def launch_window(browser: Path, url: str) -> subprocess.Popen:
    profile = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PhotoBridge" / "ventana"
    profile.mkdir(parents=True, exist_ok=True)
    width, height = WINDOW_SIZE
    args = [
        str(browser),
        f"--app={url}",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-extensions",
        "--hide-crash-restore-bubble",
        "--disable-features=Translate,msEdgeSidebarV2,msImplicitSignin,msEdgeShoppingAssistant",
    ]
    position = _window_position(width, height)
    if position:
        args.append(f"--window-position={position[0]},{position[1]}")
    return subprocess.Popen(args, close_fds=True)


def run_desktop(config: Config, server: PhotoBridgeServer, storage: Storage) -> bool:
    """Abre la ventana HTML. Devuelve False si no se pudo (y hay que usar la de Tkinter)."""
    browser = find_browser()
    if browser is None:
        return False

    import tkinter as tk
    from tkinter import filedialog

    controller = DesktopController(config, server, storage)
    threading.Thread(target=controller.watch_network, name="photobridge-network", daemon=True).start()
    if config.check_updates:
        threading.Thread(target=controller.check_updates, name="photobridge-updates", daemon=True).start()

    try:
        window = launch_window(browser, f"http://127.0.0.1:{server.port}/desktop?k={controller.key}")
    except OSError:
        server.desktop = None
        return False

    # Tkinter oculto: solo para los diálogos nativos (elegir carpeta)
    root = tk.Tk()
    root.withdraw()
    started = time.monotonic()
    outcome = {"fallback": False, "relaunch": None}

    def choose_folder() -> None:
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(
            parent=root, title="¿Dónde quieres guardar las fotos?", initialdir=str(storage.dest_dir)
        )
        root.attributes("-topmost", False)
        if chosen:
            storage.dest_dir = Path(chosen)
            config.dest_dir = str(Path(chosen))
            config.save()
            controller.publish("folder", {"folder": config.dest_dir})

    def tick() -> None:
        while True:
            try:
                kind, data = controller.requests.get_nowait()
            except queue.Empty:
                break
            if kind == "choose_folder":
                choose_folder()
            elif kind == "install":
                try:
                    updater.install(data["file"], data["exe"])
                except OSError as exc:
                    data["file"].unlink(missing_ok=True)
                    controller._update_busy = False
                    controller.publish("update_error", {"message": f"no se pudo reemplazar el programa ({exc})"})
                    continue
                controller.publish("closing", {})
                outcome["relaunch"] = data["exe"]
                root.after(700, root.quit)
                return

        now = time.monotonic()
        if not controller.ever_connected and now - started > CONNECT_TIMEOUT_SECONDS:
            outcome["fallback"] = True
            root.quit()
            return
        if controller.ever_connected and controller.subscriber_count() == 0 and now - controller.last_seen > CLOSE_GRACE_SECONDS:
            root.quit()  # el usuario cerró la ventana
            return
        root.after(200, tick)

    root.after(200, tick)
    root.mainloop()
    root.destroy()

    if outcome["fallback"]:
        _terminate(window)
        server.desktop = None
        return False

    server.shutdown()
    server.server_close()
    if outcome["relaunch"]:
        updater.relaunch(outcome["relaunch"])
    else:
        _terminate(window)
    return True


def _terminate(process: subprocess.Popen) -> None:
    try:
        if process.poll() is None:
            process.terminate()
    except OSError:
        pass
