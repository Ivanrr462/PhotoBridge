"""Guardado de los archivos recibidos: subidas por trozos, reanudables y sin duplicados.

Cada archivo se identifica con un id que calcula el móvil a partir de su contenido.
Mientras llega se escribe en un temporal oculto ``.<id>.photobridge-part``. Si la conexión
se corta (pantalla bloqueada, wifi...), el móvil pregunta cuántos bytes hay y sigue desde
ahí. Al completarse se le pone la fecha real de captura y se renombra a su nombre final.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from .metadata import capture_time

CHUNK_SIZE = 1024 * 1024
SPACE_MARGIN = 200 * 1024 * 1024  # dejamos siempre 200 MB libres
MAX_NAME_LENGTH = 150
PART_SUFFIX = ".photobridge-part"
STALE_PART_SECONDS = 3 * 24 * 3600
MAX_INDEX_ENTRIES = 100_000

UPLOAD_ID = re.compile(r"^[0-9a-f]{16,64}$")
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# Nombres sin información que ponen algunos navegadores (p. ej. Safari en iPhone)
_GENERIC_STEM = re.compile(r"^(image|video|photo|foto|img|file|tmp\w*|[0-9a-f-]{16,})(\s\(\d+\))?$", re.I)


class NotEnoughSpace(Exception):
    pass


class IncompleteUpload(Exception):
    pass


class InvalidUpload(Exception):
    pass


class UnknownUpload(Exception):
    pass


class OffsetMismatch(Exception):
    def __init__(self, offset: int):
        super().__init__(offset)
        self.offset = offset


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


def is_generic_name(name: str) -> bool:
    return bool(_GENERIC_STEM.match(Path(name).stem))


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


@dataclass
class Upload:
    id: str
    name: str
    size: int
    last_modified_ms: int
    dest: Path
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def part(self) -> Path:
        return self.dest / f".{self.id}{PART_SUFFIX}"


@dataclass
class UploadState:
    offset: int
    saved: Path | None = None
    skipped: bool = False
    name: str = ""
    size: int = 0

    @property
    def done(self) -> bool:
        return self.saved is not None


class Storage:
    def __init__(self, dest_dir: Path | str, index_path: Path | None = None):
        self._lock = threading.Lock()
        self._dest_dir = Path(dest_dir)
        self._uploads: dict[str, Upload] = {}
        self._index_path = index_path
        self._sent: dict[str, str] = self._load_index()
        self.cleanup_stale_parts()

    # -- carpeta destino ------------------------------------------------------

    @property
    def dest_dir(self) -> Path:
        with self._lock:
            return self._dest_dir

    @dest_dir.setter
    def dest_dir(self, value: Path | str) -> None:
        with self._lock:
            self._dest_dir = Path(value)
        self.cleanup_stale_parts()

    def free_space(self) -> int:
        dest = self.dest_dir
        dest.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(dest).free

    def cleanup_stale_parts(self) -> None:
        """Borra temporales de envíos abandonados hace días."""
        try:
            limit = time.time() - STALE_PART_SECONDS
            for part in self.dest_dir.glob(f".*{PART_SUFFIX}"):
                if part.stat().st_mtime < limit:
                    part.unlink(missing_ok=True)
        except OSError:
            pass

    # -- índice de archivos ya recibidos (para no duplicar) ---------------------

    def _load_index(self) -> dict[str, str]:
        if not self._index_path:
            return {}
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_index(self) -> None:
        if not self._index_path:
            return
        if len(self._sent) > MAX_INDEX_ENTRIES:
            for key in list(self._sent)[: len(self._sent) - MAX_INDEX_ENTRIES]:
                del self._sent[key]
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._sent), encoding="utf-8")
            os.replace(tmp, self._index_path)
        except OSError:
            pass

    def _already_sent(self, upload_id: str, size: int) -> Path | None:
        saved = self._sent.get(upload_id)
        if saved:
            path = Path(saved)
            try:
                if path.is_file() and path.stat().st_size == size:
                    return path
            except OSError:
                pass
            del self._sent[upload_id]
        return None

    # -- envíos -----------------------------------------------------------------

    def start(self, upload_id: str, raw_name: str, size: int, last_modified_ms: int = 0) -> UploadState:
        """Abre (o retoma) un envío y devuelve cuántos bytes hay ya guardados."""
        if not UPLOAD_ID.match(upload_id or "") or size <= 0:
            raise InvalidUpload()

        with self._lock:
            saved = self._already_sent(upload_id, size)
            if saved:
                return UploadState(offset=size, saved=saved, skipped=True, name=saved.name, size=size)
            upload = self._uploads.get(upload_id)
            if upload is None or upload.size != size:
                upload = Upload(upload_id, sanitize_filename(raw_name), size, last_modified_ms, self._dest_dir)
                self._uploads[upload_id] = upload

        with upload.lock:
            upload.dest.mkdir(parents=True, exist_ok=True)
            offset = upload.part.stat().st_size if upload.part.exists() else 0
            if offset > size:
                upload.part.unlink()
                offset = 0
            if size - offset > shutil.disk_usage(upload.dest).free - SPACE_MARGIN:
                raise NotEnoughSpace()
            upload.part.touch()
        return UploadState(offset=offset, name=upload.name, size=size)

    def append(self, upload_id: str, offset: int, stream: BinaryIO, length: int) -> UploadState:
        """Añade un trozo en la posición indicada. Al completar el archivo lo guarda."""
        upload = self._uploads.get(upload_id)
        if upload is None:
            raise UnknownUpload()
        with upload.lock:
            if not upload.part.exists():
                raise UnknownUpload()
            current = upload.part.stat().st_size
            if offset != current:
                raise OffsetMismatch(current)
            if length <= 0 or offset + length > upload.size:
                raise InvalidUpload()

            with open(upload.part, "ab", buffering=CHUNK_SIZE) as f:
                remaining = length
                while remaining > 0:
                    chunk = stream.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise IncompleteUpload()  # lo recibido se queda: se retomará desde ahí
                    f.write(chunk)
                    remaining -= len(chunk)

            if offset + length < upload.size:
                return UploadState(offset=offset + length, name=upload.name, size=upload.size)
            return UploadState(offset=upload.size, saved=self._finalize(upload), name=upload.name, size=upload.size)

    def _finalize(self, upload: Upload) -> Path:
        timestamp = capture_time(upload.part)
        if timestamp is None and upload.last_modified_ms > 0:
            timestamp = upload.last_modified_ms / 1000
        if timestamp is not None:
            try:
                os.utime(upload.part, (timestamp, timestamp))
            except (OSError, OverflowError, ValueError):
                timestamp = None

        name = upload.name
        if timestamp is not None and is_generic_name(name):
            stamp = datetime.fromtimestamp(timestamp).strftime("%Y%m%d_%H%M%S")
            name = f"IMG_{stamp}{Path(name).suffix.lower()}"

        with self._lock:
            final = unique_path(upload.dest, name)
            os.rename(upload.part, final)
            self._uploads.pop(upload.id, None)
            self._sent[upload.id] = str(final)
            self._save_index()
        return final
