"""Miniaturas de fotos y vídeos generadas por el propio Windows.

Usa la misma API que el Explorador de archivos (IShellItemImageFactory), así que sirve
para JPG, PNG, vídeos MP4/MOV y, si Windows tiene la extensión HEIF, también HEIC, sin
añadir librerías al programa. Devuelve la imagen en formato PPM, que Tkinter muestra
directamente. En otros sistemas, o si Windows no sabe generar la miniatura, devuelve None.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Thumbnail:
    width: int
    height: int
    ppm: bytes


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        @classmethod
        def from_string(cls, text: str) -> "_GUID":
            u = uuid.UUID(text)
            return cls(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))

    class _SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class _BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", ctypes.c_long),
            ("bmWidth", ctypes.c_long),
            ("bmHeight", ctypes.c_long),
            ("bmWidthBytes", ctypes.c_long),
            ("bmPlanes", wintypes.WORD),
            ("bmBitsPixel", wintypes.WORD),
            ("bmBits", ctypes.c_void_p),
        ]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    _IID_IMAGE_FACTORY = _GUID.from_string("bcc18b79-ba16-442f-80c4-8a59c30c463b")
    _SIIGBF_RESIZETOFIT = 0x00
    _SIIGBF_THUMBNAILONLY = 0x08

    _shell32 = ctypes.windll.shell32
    _ole32 = ctypes.windll.ole32
    _gdi32 = ctypes.windll.gdi32
    _user32 = ctypes.windll.user32

    _shell32.SHCreateItemFromParsingName.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)
    ]
    _shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    _gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    _gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT
    ]
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.GetDC.restype = wintypes.HDC
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

    # Métodos de la interfaz COM (posición en la vtable)
    _Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    _GetImage = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, _SIZE, ctypes.c_int, ctypes.POINTER(wintypes.HBITMAP)
    )

    def init_thread() -> None:
        """Llamar una vez en cada hilo que vaya a pedir miniaturas."""
        _ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED

    def _method(obj: ctypes.c_void_p, index: int, prototype):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return prototype(vtable[index])

    def _bitmap_pixels(hbitmap: int) -> tuple[int, int, bytes] | None:
        info = _BITMAP()
        if not _gdi32.GetObjectW(hbitmap, ctypes.sizeof(info), ctypes.byref(info)):
            return None
        width, height = info.bmWidth, abs(info.bmHeight)
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width, -height, 1, 32, 0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        dc = _user32.GetDC(None)
        try:
            lines = _gdi32.GetDIBits(dc, hbitmap, 0, height, buffer, ctypes.byref(header), 0)
        finally:
            _user32.ReleaseDC(None, dc)
        return (width, height, buffer.raw) if lines == height else None

    def windows_thumbnail(path: Path, size: int, background: tuple[int, int, int] = (255, 255, 255)) -> Thumbnail | None:
        factory = ctypes.c_void_p()
        hr = _shell32.SHCreateItemFromParsingName(
            str(Path(path).resolve()), None, ctypes.byref(_IID_IMAGE_FACTORY), ctypes.byref(factory)
        )
        if hr < 0 or not factory:
            return None
        hbitmap = wintypes.HBITMAP()
        try:
            hr = _method(factory, 3, _GetImage)(
                factory, _SIZE(size, size), _SIIGBF_RESIZETOFIT | _SIIGBF_THUMBNAILONLY, ctypes.byref(hbitmap)
            )
        finally:
            _method(factory, 2, _Release)(factory)
        if hr < 0 or not hbitmap:
            return None
        try:
            pixels = _bitmap_pixels(hbitmap)
        finally:
            _gdi32.DeleteObject(hbitmap)
        if not pixels:
            return None
        width, height, bgra = pixels
        return Thumbnail(width, height, _to_ppm(width, height, bgra, background))

else:  # pragma: no cover - solo Windows sabe generar estas miniaturas

    def init_thread() -> None:
        pass

    def windows_thumbnail(path: Path, size: int, background: tuple[int, int, int] = (255, 255, 255)) -> Thumbnail | None:
        return None


def _to_ppm(width: int, height: int, bgra: bytes, background: tuple[int, int, int]) -> bytes:
    """BGRA (alfa premultiplicado) -> PPM RGB, fundiendo la transparencia con el fondo."""
    rgb = bytearray(width * height * 3)
    alpha = bgra[3::4]
    if alpha.count(255) == len(alpha) or alpha.count(0) == len(alpha):
        # Opaca (o sin canal alfa): solo reordenar los canales
        rgb[0::3] = bgra[2::4]
        rgb[1::3] = bgra[1::4]
        rgb[2::3] = bgra[0::4]
    else:
        br, bg, bb = background
        for i, a in enumerate(alpha):
            rest = 255 - a
            p = i * 4
            q = i * 3
            rgb[q] = min(255, bgra[p + 2] + br * rest // 255)
            rgb[q + 1] = min(255, bgra[p + 1] + bg * rest // 255)
            rgb[q + 2] = min(255, bgra[p] + bb * rest // 255)
    return b"P6 %d %d 255\n" % (width, height) + bytes(rgb)
