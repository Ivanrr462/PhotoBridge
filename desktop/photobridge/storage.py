"""Guardado seguro de los archivos recibidos."""

from __future__ import annotations

import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024
SPACE_MARGIN = 200 * 1024 * 1024  # dejamos siempre 200 MB libres
MAX_NAME_LENGTH = 150

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class NotEnoughSpace(Exception):
    pass


class IncompleteUpload(Exception):
    pass


def sanitize_filename(raw: str) -> str:
    """Convierte el nombre que envía el móvil en un nombre válido y seguro en Windows."""
    name = re.split(r"[\\/]", raw or "")[-1]
    name = _INVALID_CHARS.sub("_", name).strip().rstrip(". ")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    if not stem:
        stem = "foto"
    ext = ext[:16]
    stem = stem[: MAX_NAME_LENGTH - len(ext) - 1]
    return f"{stem}.{ext}" if ext else stem


def unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, ext = candidate.stem, candidate.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem} ({counter}){ext}"
        if not candidate.exists():
            return candidate
        counter += 1


class Storage:
    def __init__(self, dest_dir: Path | str):
        self._lock = threading.Lock()
        self._dest_dir = Path(dest_dir)

    @property
    def dest_dir(self) -> Path:
        with self._lock:
            return self._dest_dir

    @dest_dir.setter
    def dest_dir(self, value: Path | str) -> None:
        with self._lock:
            self._dest_dir = Path(value)

    def free_space(self) -> int:
        dest = self.dest_dir
        dest.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(dest).free

    def save_stream(
        self,
        stream: BinaryIO,
        length: int,
        raw_name: str,
        last_modified_ms: int | None = None,
    ) -> Path:
        dest = self.dest_dir
        dest.mkdir(parents=True, exist_ok=True)
        if length > shutil.disk_usage(dest).free - SPACE_MARGIN:
            raise NotEnoughSpace()

        name = sanitize_filename(raw_name)
        tmp = dest / f".{uuid.uuid4().hex}.photobridge-part"
        try:
            with open(tmp, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = stream.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise IncompleteUpload()
                    f.write(chunk)
                    remaining -= len(chunk)
            if last_modified_ms and last_modified_ms > 0:
                timestamp = last_modified_ms / 1000
                os.utime(tmp, (timestamp, timestamp))
            with self._lock:
                final = unique_path(dest, name)
                os.rename(tmp, final)
            return final
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
