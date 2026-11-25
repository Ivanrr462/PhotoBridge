"""Punto de entrada de PhotoBridge."""

from __future__ import annotations

import argparse
import sys

from .config import Config, config_dir
from .network import get_lan_ip
from .server import PhotoBridgeServer
from .storage import Storage


def run_console(config: Config, server: PhotoBridgeServer, storage: Storage) -> None:
    from .qr import print_qr

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def on_event(kind: str, data: dict) -> None:
        if kind == "received":
            print(f"  ✔ {data['path'].name} ({data['size']} bytes)", flush=True)
        elif kind == "skipped":
            print(f"  = {data['path'].name} (ya estaba, no se duplica)", flush=True)
        elif kind == "error":
            print(f"  ✖ {data['message']}", flush=True)

    server.on_event = on_event
    ip = get_lan_ip() or "127.0.0.1"
    url = f"http://{ip}:{server.port}/?t={config.token}"
    print("PhotoBridge escuchando en la red local")
    print(f"  Abre en el móvil: {url}")
    print(f"  Carpeta destino:  {storage.dest_dir}\n")
    print_qr(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="photobridge", description="Recibe fotos del móvil por la wifi de casa.")
    parser.add_argument("--no-gui", action="store_true", help="modo consola, sin ventana")
    parser.add_argument("--port", type=int, help="puerto a usar (por defecto 8765)")
    parser.add_argument("--dest", help="carpeta donde guardar las fotos")
    args = parser.parse_args(argv)

    config = Config.load()
    if args.port:
        config.port = args.port
    if args.dest:
        config.dest_dir = args.dest

    storage = Storage(config.dest_dir, index_path=config_dir() / "sent.json")
    server = PhotoBridgeServer(config.port, config.token, storage)

    if args.no_gui:
        run_console(config, server, storage)
    else:
        from .gui import run_gui

        run_gui(config, server, storage)
