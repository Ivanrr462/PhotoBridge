"""Ventana de escritorio: QR para conectar el móvil, galería de lo recibido y actualizaciones."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

from . import __version__, updater
from .config import Config
from .network import get_lan_ip
from .qr import qr_matrix
from .server import PhotoBridgeServer
from .storage import Storage
from .thumbs import init_thread, windows_thumbnail

BG = "#f5f6fd"
CARD = "#ffffff"
SOFT = "#eef0fa"
BORDER = "#e4e6f2"
TEXT = "#0f1222"
MUTED = "#5b6178"
ACCENT = "#4353ff"
ACCENT_HOVER = "#3442f0"
ACCENT_SOFT = "#e8ebff"
OK = "#12b76a"
ERROR = "#f04438"
FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
QR_SIZE = 260
IP_CHECK_MS = 5000

TILE = 116
GALLERY_COLUMNS = 3
GALLERY_SIZE = 9
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".3gp", ".avi", ".mkv", ".webm"}


def _enable_hidpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _format_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return f"{size} B"


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def open_path(path: Path) -> None:
    if path.is_dir():
        path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App:
    def __init__(self, config: Config, server: PhotoBridgeServer, storage: Storage):
        self.config = config
        self.server = server
        self.storage = storage
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.thumb_requests: queue.Queue[Path] = queue.Queue()
        self.current_url: str | None = None
        self.received_count = 0
        self.received_bytes = 0
        self.skipped_count = 0
        self.gallery: list[dict] = []  # más recientes primero: {"path", "image"}
        self.release: updater.Release | None = None
        self.updating = False

        server.on_event = lambda kind, data: self.events.put((kind, data))

        _enable_hidpi()
        self.root = tk.Tk()
        self.root.title("PhotoBridge")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self._set_window_icon()
        self._build()

    def _set_window_icon(self) -> None:
        web = Path(__file__).parent / "web"
        try:
            self._icons = [tk.PhotoImage(file=str(web / name)) for name in ("app-icon-256.png", "app-icon-32.png")]
            self.root.iconphoto(True, *self._icons)
        except tk.TclError:
            pass  # sin icono no pasa nada

    # -- piezas de la interfaz ---------------------------------------------------

    def _label(self, parent, text="", size=10, color=TEXT, bold=False, **kwargs) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=parent["bg"],
            fg=color,
            font=(FONT, size, "bold" if bold else "normal"),
            **kwargs,
        )

    def _button(self, parent, text, command, primary=False) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=(FONT, 10, "bold" if primary else "normal"),
            bg=ACCENT if primary else CARD,
            fg="white" if primary else TEXT,
            activebackground=ACCENT_HOVER if primary else BG,
            activeforeground="white" if primary else TEXT,
            disabledforeground="#c9cdf9" if primary else MUTED,
            relief="flat",
            bd=0,
            padx=18,
            pady=9,
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=ACCENT if primary else BORDER,
        )

    def _card(self, parent, **kwargs) -> tk.Frame:
        return tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER, **kwargs)

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=28, pady=22)
        outer.pack()

        # Aviso de actualización (oculto hasta que haga falta)
        self.banner = tk.Frame(outer, bg=ACCENT_SOFT, padx=14, pady=10, highlightthickness=1, highlightbackground="#c9cffd")
        self.banner_label = self._label(self.banner, "", size=10, color=TEXT, bold=True)
        self.banner_label.pack(side="left")
        self.banner_button = self._button(self.banner, "Actualizar", self.start_update, primary=True)
        self.banner_button.config(pady=5, padx=14)
        self.banner_button.pack(side="right")

        # Cabecera
        header = tk.Frame(outer, bg=BG)
        header.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))
        try:
            self._logo = tk.PhotoImage(file=str(Path(__file__).parent / "web" / "app-icon-64.png"))
            tk.Label(header, image=self._logo, bg=BG).pack(side="left", padx=(0, 10))
        except tk.TclError:
            pass
        self._label(header, "Photo", size=22, bold=True, padx=0, bd=0).pack(side="left")
        self._label(header, "Bridge", size=22, bold=True, color=ACCENT, padx=0, bd=0).pack(side="left")
        self._label(header, f"  v{__version__}", size=9, color=MUTED).pack(side="left", anchor="s", pady=(0, 6))

        # Columna izquierda: QR
        left = tk.Frame(outer, bg=BG)
        left.grid(row=2, column=0, sticky="n", padx=(0, 22))
        self._label(left, "Escanea este código con la cámara de tu móvil", size=11, color=MUTED).pack(pady=(0, 12))
        qr_card = self._card(left, padx=18, pady=18)
        qr_card.pack()
        self.canvas = tk.Canvas(qr_card, width=QR_SIZE, height=QR_SIZE, bg=CARD, highlightthickness=0)
        self.canvas.pack()
        self.url_label = self._label(qr_card, size=9, color=MUTED, wraplength=QR_SIZE)
        self.url_label.pack(pady=(10, 0))
        self.retry_button = self._button(qr_card, "Reintentar", self.refresh_qr, primary=True)
        self._label(
            left,
            "El móvil tiene que estar conectado a la misma wifi que este ordenador.",
            size=9,
            color=MUTED,
            wraplength=300,
        ).pack(pady=(12, 0))

        # Columna derecha: galería de lo recibido
        right = self._card(outer, padx=18, pady=16)
        right.grid(row=2, column=1, sticky="nsew")
        self._label(right, "Fotos recibidas", size=13, bold=True).pack(anchor="w")
        self.status_label = self._label(right, "Esperando fotos…", size=10, color=MUTED)
        self.status_label.pack(anchor="w", pady=(2, 10))

        grid_size = GALLERY_COLUMNS * (TILE + 8)
        self.gallery_frame = tk.Frame(right, bg=CARD, width=grid_size, height=grid_size)
        self.gallery_frame.pack()
        self.gallery_frame.grid_propagate(False)
        self.gallery_hint = self._label(right, "", size=9, color=MUTED)
        self.gallery_hint.pack(anchor="w", pady=(6, 0))

        folder_box = tk.Frame(right, bg=CARD)
        folder_box.pack(fill="x", pady=(12, 0))
        self._label(folder_box, "Se guardan en:", size=9, color=MUTED).pack(anchor="w")
        self.folder_label = self._label(
            folder_box, str(self.storage.dest_dir), size=9, wraplength=grid_size, justify="left"
        )
        self.folder_label.pack(anchor="w")

        buttons = tk.Frame(right, bg=CARD)
        buttons.pack(anchor="w", pady=(12, 0))
        self._button(buttons, "Abrir carpeta", lambda: open_path(self.storage.dest_dir), primary=True).pack(
            side="left", padx=(0, 8)
        )
        self._button(buttons, "Cambiar carpeta", self.change_folder).pack(side="left")

        self._render_gallery()

    # -- QR --------------------------------------------------------------------

    def _draw_qr(self, data: str) -> None:
        self.canvas.delete("all")
        matrix = qr_matrix(data)
        count = len(matrix)
        cell = QR_SIZE // count
        offset = (QR_SIZE - cell * count) // 2
        for y, row in enumerate(matrix):
            for x, filled in enumerate(row):
                if filled:
                    x0 = offset + x * cell
                    y0 = offset + y * cell
                    self.canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell, fill=TEXT, width=0)

    def _draw_no_network(self) -> None:
        self.canvas.delete("all")
        self.canvas.create_text(
            QR_SIZE / 2,
            QR_SIZE / 2,
            text="No estás conectado a ninguna red.\n\nConecta este ordenador a la wifi\n(o por cable al router).",
            font=(FONT, 11),
            fill=MUTED,
            justify="center",
        )

    def refresh_qr(self) -> None:
        ip = get_lan_ip()
        url = f"http://{ip}:{self.server.port}/?t={self.config.token}" if ip else None
        if url == self.current_url and self.canvas.find_all():
            return
        self.current_url = url
        if url:
            self._draw_qr(url)
            self.url_label.config(text=f"http://{ip}:{self.server.port}")
            self.retry_button.pack_forget()
        else:
            self._draw_no_network()
            self.url_label.config(text="")
            self.retry_button.pack(pady=(10, 0))

    def _watch_network(self) -> None:
        self.refresh_qr()  # si cambia la wifi o la IP, el QR se actualiza solo
        self.root.after(IP_CHECK_MS, self._watch_network)

    # -- galería -----------------------------------------------------------------

    def _add_to_gallery(self, path: Path) -> None:
        self.gallery = [item for item in self.gallery if item["path"] != path]
        self.gallery.insert(0, {"path": path, "image": None})
        del self.gallery[GALLERY_SIZE:]
        self.thumb_requests.put(path)
        self._render_gallery()

    def _render_gallery(self) -> None:
        for child in self.gallery_frame.winfo_children():
            child.destroy()
        if not self.gallery:
            empty = tk.Frame(self.gallery_frame, bg=SOFT, width=GALLERY_COLUMNS * (TILE + 8) - 8, height=TILE * 2)
            empty.grid(row=0, column=0, columnspan=GALLERY_COLUMNS, padx=4, pady=4)
            empty.pack_propagate(False)
            self._label(
                empty,
                "Aquí verás las fotos y vídeos\nque envíes desde el móvil",
                size=10,
                color=MUTED,
                justify="center",
            ).pack(expand=True)
            self.gallery_hint.config(text="")
            return
        for index, item in enumerate(self.gallery):
            self._tile(item).grid(row=index // GALLERY_COLUMNS, column=index % GALLERY_COLUMNS, padx=4, pady=4)
        self.gallery_hint.config(text="Haz clic en una foto para abrirla.")

    def _tile(self, item: dict) -> tk.Frame:
        path: Path = item["path"]
        is_video = path.suffix.lower() in VIDEO_EXTENSIONS
        tile = tk.Frame(self.gallery_frame, bg=SOFT, width=TILE, height=TILE, cursor="hand2")
        tile.pack_propagate(False)
        if item["image"] is not None:
            content = tk.Label(tile, image=item["image"], bg=SOFT, bd=0)
        else:
            content = tk.Label(
                tile, text=path.suffix.upper().lstrip(".") or "?", bg=SOFT, fg=MUTED, font=(FONT, 11, "bold")
            )
        content.pack(expand=True)
        widgets = [tile, content]
        if is_video:
            badge = tk.Label(tile, text="▶ vídeo", bg=TEXT, fg="white", font=(FONT, 8, "bold"), padx=5, pady=1)
            badge.place(x=5, rely=1.0, y=-5, anchor="sw")
            widgets.append(badge)
        for widget in widgets:
            widget.bind("<Button-1>", lambda _e, p=path: self._open_received(p))
        return tile

    def _open_received(self, path: Path) -> None:
        if path.exists():
            open_path(path)
        else:
            messagebox.showinfo("PhotoBridge", f"{path.name} ya no está en la carpeta.", parent=self.root)

    def _thumbnail_worker(self) -> None:
        init_thread()
        background = _hex_to_rgb(SOFT)
        while True:
            path = self.thumb_requests.get()
            try:
                thumb = windows_thumbnail(path, TILE - 6, background)
            except Exception:
                thumb = None
            if thumb:
                self.events.put(("thumb", {"path": path, "ppm": thumb.ppm}))

    def _set_thumbnail(self, path: Path, ppm: bytes) -> None:
        for item in self.gallery:
            if item["path"] == path:
                try:
                    item["image"] = tk.PhotoImage(data=ppm, format="PPM")
                except tk.TclError:
                    return
                self._render_gallery()
                return

    # -- carpeta -------------------------------------------------------------------

    def change_folder(self) -> None:
        chosen = filedialog.askdirectory(
            title="¿Dónde quieres guardar las fotos?", initialdir=str(self.storage.dest_dir)
        )
        if not chosen:
            return
        self.storage.dest_dir = Path(chosen)
        self.config.dest_dir = str(Path(chosen))
        self.config.save()
        self.folder_label.config(text=self.config.dest_dir)

    # -- actualizaciones -------------------------------------------------------------

    def _check_updates(self) -> None:
        updater.cleanup_previous()
        release = updater.check_for_update()
        if release:
            self.events.put(("update", {"release": release}))

    def _offer_update(self, release: updater.Release) -> None:
        self.release = release
        self.banner_label.config(text=f"Hay una versión nueva de PhotoBridge: v{release.version}")
        self.banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        wants = messagebox.askyesno(
            "Actualización disponible",
            f"Hay una versión nueva de PhotoBridge (v{release.version}). Ahora tienes la v{__version__}.\n\n"
            "¿Quieres actualizar ahora? Tarda unos segundos y PhotoBridge se volverá a abrir solo.",
            parent=self.root,
        )
        if wants:
            self.start_update()

    def start_update(self) -> None:
        release = self.release
        if not release or self.updating:
            return
        exe = updater.running_exe()
        if exe is None:  # ejecutándose desde el código fuente
            webbrowser.open(release.page)
            return
        self.updating = True
        self.banner_button.config(state="disabled", text="Actualizando…")
        self.banner_label.config(text="Descargando la actualización… 0 %")

        def work() -> None:
            try:
                new_file = updater.download(
                    release, exe, lambda done, total: self.events.put(("update_progress", {"done": done, "total": total}))
                )
                self.events.put(("update_ready", {"file": new_file, "exe": exe}))
            except Exception as exc:  # noqa: BLE001 - se informa al usuario
                self.events.put(("update_failed", {"error": str(exc) or exc.__class__.__name__}))

        threading.Thread(target=work, name="photobridge-update", daemon=True).start()

    def _finish_update(self, new_file: Path, exe: Path) -> None:
        try:
            updater.install(new_file, exe)
        except OSError as exc:
            new_file.unlink(missing_ok=True)
            self._update_failed(f"no se pudo reemplazar el programa ({exc})")
            return
        self.banner_label.config(text="Reiniciando PhotoBridge…")
        self.root.update_idletasks()
        self.server.shutdown()
        self.server.server_close()
        updater.relaunch(exe)
        self.root.destroy()

    def _update_failed(self, error: str) -> None:
        self.updating = False
        self.banner_button.config(state="normal", text="Reintentar")
        self.banner_label.config(text="No se pudo actualizar")
        if messagebox.askyesno(
            "No se pudo actualizar",
            f"La actualización ha fallado: {error}.\n\n¿Quieres abrir la página de descarga para bajarla a mano?",
            parent=self.root,
        ):
            webbrowser.open(self.release.page if self.release else updater.RELEASES_PAGE)

    # -- eventos ----------------------------------------------------------------------

    def _poll_events(self) -> None:
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "received":
                self.received_count += 1
                self.received_bytes += data["size"]
                self._update_status()
                self._add_to_gallery(data["path"])
            elif kind == "skipped":
                self.skipped_count += 1
                self._update_status()
                self._add_to_gallery(data["path"])
            elif kind == "thumb":
                self._set_thumbnail(data["path"], data["ppm"])
            elif kind == "error":
                self.status_label.config(text=data["message"], fg=ERROR)
            elif kind == "update":
                self._offer_update(data["release"])
            elif kind == "update_progress":
                percent = int(data["done"] * 100 / max(1, data["total"]))
                self.banner_label.config(text=f"Descargando la actualización… {percent} %")
            elif kind == "update_ready":
                self._finish_update(data["file"], data["exe"])
                return  # la ventana se ha cerrado
            elif kind == "update_failed":
                self._update_failed(data["error"])
        self.root.after(150, self._poll_events)

    def _update_status(self) -> None:
        parts = []
        if self.received_count:
            noun = "archivo recibido" if self.received_count == 1 else "archivos recibidos"
            parts.append(f"✔ {self.received_count} {noun} ({_format_size(self.received_bytes)})")
        if self.skipped_count:
            parts.append(
                "1 ya estaba en el ordenador"
                if self.skipped_count == 1
                else f"{self.skipped_count} ya estaban en el ordenador"
            )
        self.status_label.config(text=" · ".join(parts), fg=OK if self.received_count else MUTED)

    # -- arranque -----------------------------------------------------------------------

    def run(self) -> None:
        self.server.start_in_background()
        threading.Thread(target=self._thumbnail_worker, name="photobridge-thumbs", daemon=True).start()
        if self.config.check_updates:
            threading.Thread(target=self._check_updates, name="photobridge-updates", daemon=True).start()
        self._watch_network()
        self._poll_events()
        try:
            self.root.mainloop()
        finally:
            try:
                self.server.shutdown()
                self.server.server_close()
            except OSError:
                pass


def run_gui(config: Config, server: PhotoBridgeServer, storage: Storage) -> None:
    App(config, server, storage).run()
