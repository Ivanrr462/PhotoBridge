"""Ventana de escritorio: muestra el QR y lo que se va recibiendo."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from .config import Config
from .network import get_lan_ip
from .qr import qr_matrix
from .server import PhotoBridgeServer
from .storage import Storage

BG = "#f5f6fd"
CARD = "#ffffff"
BORDER = "#e4e6f2"
TEXT = "#0f1222"
MUTED = "#5b6178"
ACCENT = "#4353ff"
ACCENT_HOVER = "#3442f0"
ACCENT_2 = "#19a8fb"
OK = "#12b76a"
ERROR = "#f04438"
FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
QR_SIZE = 280
IP_CHECK_MS = 5000


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


def open_folder(path: Path) -> None:
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
        self.current_url: str | None = None
        self.received_count = 0
        self.received_bytes = 0

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

    # -- interfaz -----------------------------------------------------------

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
            relief="flat",
            bd=0,
            padx=18,
            pady=9,
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=ACCENT if primary else BORDER,
        )

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=32, pady=26)
        outer.pack()

        header = tk.Frame(outer, bg=BG)
        header.pack()
        try:
            self._logo = tk.PhotoImage(file=str(Path(__file__).parent / "web" / "app-icon-64.png"))
            tk.Label(header, image=self._logo, bg=BG).pack(side="left", padx=(0, 10))
        except tk.TclError:
            pass
        self._label(header, "Photo", size=22, bold=True, padx=0, bd=0).pack(side="left")
        self._label(header, "Bridge", size=22, bold=True, color=ACCENT, padx=0, bd=0).pack(side="left")

        self._label(
            outer, "Escanea este código con la cámara de tu móvil", size=11, color=MUTED
        ).pack(pady=(10, 18))

        card = tk.Frame(outer, bg=CARD, padx=20, pady=20, highlightthickness=1, highlightbackground=BORDER)
        card.pack()
        self.canvas = tk.Canvas(card, width=QR_SIZE, height=QR_SIZE, bg=CARD, highlightthickness=0)
        self.canvas.pack()
        self.url_label = self._label(card, size=9, color=MUTED, wraplength=QR_SIZE)
        self.url_label.pack(pady=(10, 0))
        self.retry_button = self._button(card, "Reintentar", self.refresh_qr, primary=True)

        self._label(
            outer,
            "El móvil tiene que estar conectado a la misma wifi que este ordenador.",
            size=9,
            color=MUTED,
            wraplength=340,
        ).pack(pady=(12, 0))

        self.status_label = self._label(outer, "Esperando fotos…", size=11, bold=True, wraplength=340)
        self.status_label.pack(pady=(18, 2))
        self.last_label = self._label(outer, "", size=9, color=MUTED, wraplength=340)
        self.last_label.pack()

        folder_box = tk.Frame(outer, bg=BG)
        folder_box.pack(pady=(16, 0), fill="x")
        self._label(folder_box, "Las fotos se guardan en:", size=9, color=MUTED).pack(anchor="w")
        self.folder_label = self._label(folder_box, str(self.storage.dest_dir), size=9, wraplength=340, justify="left")
        self.folder_label.pack(anchor="w")

        buttons = tk.Frame(outer, bg=BG)
        buttons.pack(pady=(14, 0))
        self._button(buttons, "Abrir carpeta", lambda: open_folder(self.storage.dest_dir), primary=True).pack(
            side="left", padx=4
        )
        self._button(buttons, "Cambiar carpeta", self.change_folder).pack(side="left", padx=4)

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

    # -- acciones -----------------------------------------------------------

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

    def _poll_events(self) -> None:
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "received":
                self.received_count += 1
                self.received_bytes += data["size"]
                noun = "archivo recibido" if self.received_count == 1 else "archivos recibidos"
                self.status_label.config(
                    text=f"✔ {self.received_count} {noun} ({_format_size(self.received_bytes)})", fg=OK
                )
                self.last_label.config(text=f"Último: {data['path'].name}")
            elif kind == "error":
                self.status_label.config(text=data["message"], fg=ERROR)
        self.root.after(200, self._poll_events)

    def _watch_network(self) -> None:
        self.refresh_qr()  # si cambia la wifi o la IP, el QR se actualiza solo
        self.root.after(IP_CHECK_MS, self._watch_network)

    def run(self) -> None:
        self.server.start_in_background()
        self._watch_network()
        self._poll_events()
        try:
            self.root.mainloop()
        finally:
            self.server.shutdown()
            self.server.server_close()


def run_gui(config: Config, server: PhotoBridgeServer, storage: Storage) -> None:
    App(config, server, storage).run()
