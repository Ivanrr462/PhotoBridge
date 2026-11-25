"""Configuración persistente (token, puerto y carpeta de destino)."""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

APP_NAME = "PhotoBridge"
DEFAULT_PORT = 8765


def config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".config" / "photobridge"


def _windows_pictures_dir() -> Path | None:
    """Carpeta "Imágenes" real del usuario (respeta OneDrive y carpetas movidas)."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder_id = uuid.UUID("33E28130-4E1E-4676-835A-98395C3BC3BB")  # FOLDERID_Pictures
        guid = GUID(
            folder_id.time_low,
            folder_id.time_mid,
            folder_id.time_hi_version,
            (ctypes.c_ubyte * 8).from_buffer_copy(folder_id.bytes[8:]),
        )
        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
        )
        if result != 0:
            return None
        try:
            return Path(path_ptr.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
    except Exception:
        return None


def default_destination() -> Path:
    pictures = _windows_pictures_dir() if sys.platform == "win32" else None
    return (pictures or Path.home() / "Pictures") / APP_NAME


@dataclass
class Config:
    token: str
    port: int
    dest_dir: str

    @classmethod
    def load(cls) -> "Config":
        path = config_dir() / "config.json"
        data: dict = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        config = cls(
            token=data.get("token") or secrets.token_urlsafe(16),
            port=int(data.get("port") or DEFAULT_PORT),
            dest_dir=data.get("dest_dir") or str(default_destination()),
        )
        if config.as_dict() != data:
            config.save()
        return config

    def as_dict(self) -> dict:
        return asdict(self)

    def save(self) -> None:
        directory = config_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.json").write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
