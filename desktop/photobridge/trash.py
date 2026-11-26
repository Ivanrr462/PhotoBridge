"""Enviar archivos a la Papelera de reciclaje de Windows (se pueden recuperar desde allí)."""

from __future__ import annotations

import sys
from pathlib import Path


class TrashError(OSError):
    pass


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    _FO_DELETE = 0x0003
    _FOF_SILENT = 0x0004
    _FOF_NOCONFIRMATION = 0x0010
    _FOF_ALLOWUNDO = 0x0040  # a la Papelera en lugar de borrar del todo
    _FOF_NOERRORUI = 0x0400

    def send_to_trash(paths: list[Path]) -> None:
        targets = [str(Path(p).resolve()) for p in paths if Path(p).exists()]
        if not targets:
            return
        # Lista de rutas separadas por \0 y terminada en \0\0
        buffer = ctypes.create_unicode_buffer("\0".join(targets) + "\0\0")
        operation = _SHFILEOPSTRUCTW()
        operation.wFunc = _FO_DELETE
        operation.pFrom = ctypes.cast(buffer, ctypes.c_wchar_p)
        operation.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
        if result != 0 or operation.fAnyOperationsAborted:
            raise TrashError(f"no se pudieron mover a la Papelera (código {result})")

else:  # pragma: no cover

    def send_to_trash(paths: list[Path]) -> None:
        raise TrashError("la Papelera solo está disponible en Windows")
