"""Comprobación e instalación de versiones nuevas desde las Releases de GitHub.

Al arrancar se consulta la última Release publicada (solo se envía la versión del
programa en el User-Agent). Si es más nueva, el usuario puede actualizar con un clic:

1. Se descarga el nuevo PhotoBridge.exe junto al actual y se comprueba su tamaño y su
   SHA-256 (si GitHub lo publica).
2. Windows permite renombrar un .exe en ejecución: el actual pasa a llamarse
   PhotoBridge.old.exe y el nuevo ocupa su lugar.
3. Se abre el nuevo y se cierra el viejo. El .old.exe se borra en el siguiente arranque.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

from . import __version__

REPO = "Ivanrr462/PhotoBridge"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "PhotoBridge.exe"
CHUNK = 256 * 1024


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    url: str
    size: int
    sha256: str | None
    page: str


def parse_version(text: str) -> tuple[int, ...] | None:
    match = re.match(r"^v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def _request(url: str) -> Request:
    return Request(url, headers={"User-Agent": f"PhotoBridge/{__version__}", "Accept": "application/vnd.github+json"})


def fetch_latest(api_url: str = LATEST_RELEASE_API, timeout: float = 6) -> Release | None:
    with urlopen(_request(api_url), timeout=timeout) as response:
        data = json.load(response)
    if data.get("draft") or data.get("prerelease"):
        return None
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET_NAME), None)
    if not asset:
        return None
    digest = asset.get("digest") or ""
    return Release(
        version=str(data.get("tag_name", "")).lstrip("v"),
        url=asset["browser_download_url"],
        size=int(asset["size"]),
        sha256=digest[7:].lower() if digest.startswith("sha256:") else None,
        page=data.get("html_url") or RELEASES_PAGE,
    )


def check_for_update(current: str = __version__, api_url: str = LATEST_RELEASE_API) -> Release | None:
    """La Release más nueva que la versión actual, o None (también si no hay conexión)."""
    try:
        release = fetch_latest(api_url)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return release if release and is_newer(release.version, current) else None


# ---------------------------------------------------------------------------- instalación


def running_exe() -> Path | None:
    """Ruta del PhotoBridge.exe en ejecución (None si se ejecuta desde el código fuente)."""
    return Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None


def old_copy(exe: Path) -> Path:
    return exe.with_name(f"{exe.stem}.old{exe.suffix}")


def cleanup_previous(exe: Path | None = None) -> None:
    exe = exe or running_exe()
    if exe:
        try:
            old_copy(exe).unlink(missing_ok=True)
        except OSError:
            pass  # el proceso viejo aún no ha terminado: se borrará la próxima vez


def download(release: Release, exe: Path, progress: Callable[[int, int], None] | None = None) -> Path:
    target = exe.with_name(f"{exe.stem}.download{exe.suffix}")
    digest = hashlib.sha256()
    received = 0
    try:
        with urlopen(_request(release.url), timeout=30) as response, open(target, "wb") as f:
            while chunk := response.read(CHUNK):
                f.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress:
                    progress(received, release.size)
        if received != release.size:
            raise UpdateError(f"descarga incompleta ({received} de {release.size} bytes)")
        if release.sha256 and digest.hexdigest() != release.sha256:
            raise UpdateError("el archivo descargado no coincide con el publicado")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def install(new_file: Path, exe: Path) -> None:
    """Sustituye el .exe en ejecución por el nuevo (Windows permite renombrar el que corre)."""
    old = old_copy(exe)
    old.unlink(missing_ok=True)
    os.replace(exe, old)
    try:
        os.replace(new_file, exe)
    except OSError:
        os.replace(old, exe)  # deshacer: el programa sigue como estaba
        raise


def relaunch(exe: Path) -> None:
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # que el nuevo .exe no reutilice el entorno de este
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(exe)], env=env, creationflags=flags, close_fds=True, cwd=str(exe.parent))
