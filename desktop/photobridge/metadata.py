"""Fecha de captura real a partir de los metadatos del archivo.

El navegador del iPhone no envía la fecha original de las fotos (manda la del momento en
que se eligen), así que la leemos del propio archivo:

- JPEG: EXIF DateTimeOriginal (+ OffsetTimeOriginal si existe).
- HEIC/HEIF: el mismo bloque EXIF, guardado como elemento "Exif" dentro de la caja "meta".
- MOV/MP4: creation_time de la caja "mvhd" (UTC desde 1904).

Solo se usa la librería estándar. Ante cualquier formato raro se devuelve None.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

JPEG_SCAN = 256 * 1024
META_MAX = 4 * 1024 * 1024
EXIF_MAX = 1024 * 1024
QT_EPOCH_OFFSET = 2082844800  # segundos entre 1904-01-01 y 1970-01-01
MIN_YEAR = 1990

HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"avif"}
QT_TOP_LEVEL = {b"moov", b"mdat", b"wide", b"free", b"skip", b"ftyp"}


def capture_time(path: Path) -> float | None:
    """Marca de tiempo (epoch) de cuándo se hizo la foto o el vídeo, o None si no se sabe."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:2] == b"\xff\xd8":
                return _jpeg(f)
            if head[4:8] == b"ftyp":
                if head[8:12] in HEIF_BRANDS:
                    return _heif(f, path.stat().st_size)
                return _quicktime(f, path.stat().st_size)
            if head[4:8] in QT_TOP_LEVEL:
                return _quicktime(f, path.stat().st_size)
    except (OSError, struct.error, ValueError, IndexError, KeyError, OverflowError):
        return None
    return None


# ---------------------------------------------------------------------------- EXIF


def _parse_exif_datetime(value: str | None, offset: str | None) -> float | None:
    if not value:
        return None
    try:
        moment = datetime.strptime(value.strip()[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    if moment.year < MIN_YEAR:
        return None
    if offset and len(offset) >= 6 and offset[0] in "+-":
        try:
            hours, minutes = int(offset[1:3]), int(offset[4:6])
            delta = timedelta(hours=hours, minutes=minutes)
            moment = moment.replace(tzinfo=timezone(delta if offset[0] == "+" else -delta))
        except ValueError:
            pass
    return moment.timestamp()  # sin zona horaria: se interpreta como hora local del PC


def exif_capture_time(tiff: bytes) -> float | None:
    """Lee DateTimeOriginal de un bloque TIFF/EXIF (empieza por "II*" o "MM*")."""
    if tiff[:2] == b"II":
        endian = "<"
    elif tiff[:2] == b"MM":
        endian = ">"
    else:
        return None

    def u16(pos: int) -> int:
        return struct.unpack_from(endian + "H", tiff, pos)[0]

    def u32(pos: int) -> int:
        return struct.unpack_from(endian + "I", tiff, pos)[0]

    def entries(ifd: int) -> Iterator[tuple[int, int, int]]:
        for k in range(u16(ifd)):
            pos = ifd + 2 + 12 * k
            yield u16(pos), u32(pos + 4), pos + 8  # tag, count, posición del valor

    def ascii_value(count: int, value_pos: int) -> str:
        start = value_pos if count <= 4 else u32(value_pos)
        return tiff[start : start + count].split(b"\0")[0].decode("ascii", "ignore")

    fallback = exif_ifd = None
    for tag, count, pos in entries(u32(4)):
        if tag == 0x8769:  # puntero al Exif IFD
            exif_ifd = u32(pos)
        elif tag == 0x0132:  # DateTime (modificación)
            fallback = ascii_value(count, pos)

    original = offset = None
    if exif_ifd:
        for tag, count, pos in entries(exif_ifd):
            if tag == 0x9003:  # DateTimeOriginal
                original = ascii_value(count, pos)
            elif tag == 0x9011:  # OffsetTimeOriginal
                offset = ascii_value(count, pos)
    return _parse_exif_datetime(original, offset) or _parse_exif_datetime(fallback, None)


def _jpeg(f: BinaryIO) -> float | None:
    f.seek(0)
    data = f.read(JPEG_SCAN)
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker in (0xD9, 0xDA):  # fin de imagen / inicio de los datos
            return None
        length = struct.unpack_from(">H", data, pos + 2)[0]
        if marker == 0xE1 and data[pos + 4 : pos + 10] == b"Exif\0\0":
            return exif_capture_time(data[pos + 10 : pos + 2 + length])
        pos += 2 + length
    return None


# ------------------------------------------------------------------- ISO BMFF (HEIF, MP4, MOV)


def _boxes(f: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """Recorre las cajas entre start y end: (tipo, inicio del contenido, fin de la caja)."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, kind = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        yield kind, pos + header, pos + size
        pos += size


def _boxes_in(data: bytes, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        yield kind, pos + header, pos + size
        pos += size


def _quicktime(f: BinaryIO, file_size: int) -> float | None:
    for kind, start, end in _boxes(f, 0, file_size):
        if kind != b"moov":
            continue
        for sub, sub_start, _ in _boxes(f, start, end):
            if sub == b"mvhd":
                f.seek(sub_start)
                version = f.read(4)[0]
                if version == 1:
                    created = struct.unpack(">Q", f.read(8))[0]
                else:
                    created = struct.unpack(">I", f.read(4))[0]
                if created <= QT_EPOCH_OFFSET:
                    return None
                ts = created - QT_EPOCH_OFFSET
                return ts if datetime.fromtimestamp(ts, timezone.utc).year >= MIN_YEAR else None
        return None
    return None


def _uint(data: bytes, pos: int, size: int) -> int:
    return int.from_bytes(data[pos : pos + size], "big") if size else 0


def _heif(f: BinaryIO, file_size: int) -> float | None:
    meta = next(((s, e) for k, s, e in _boxes(f, 0, file_size) if k == b"meta"), None)
    if not meta or meta[1] - meta[0] > META_MAX:
        return None
    f.seek(meta[0])
    data = f.read(meta[1] - meta[0])
    children = {kind: (start, end) for kind, start, end in _boxes_in(data, 4, len(data))}  # 4: versión+flags

    # iinf: qué elemento es el bloque Exif
    exif_id = None
    if b"iinf" in children:
        start, end = children[b"iinf"]
        version = data[start]
        first = start + 4 + (2 if version == 0 else 4)
        for kind, s, _ in _boxes_in(data, first, end):
            if kind != b"infe" or data[s] < 2:
                continue
            id_size = 2 if data[s] == 2 else 4
            item_id = _uint(data, s + 4, id_size)
            item_type = data[s + 4 + id_size + 2 : s + 4 + id_size + 6]
            if item_type == b"Exif":
                exif_id = item_id
                break
    if exif_id is None or b"iloc" not in children:
        return None

    # iloc: dónde está ese elemento dentro del archivo
    start, _ = children[b"iloc"]
    version = data[start]
    pos = start + 4
    offset_size, length_size = data[pos] >> 4, data[pos] & 0x0F
    base_offset_size = data[pos + 1] >> 4
    index_size = data[pos + 1] & 0x0F if version in (1, 2) else 0
    pos += 2
    count_size = 2 if version < 2 else 4
    item_count = _uint(data, pos, count_size)
    pos += count_size
    for _ in range(item_count):
        item_id = _uint(data, pos, count_size)
        pos += count_size
        if version in (1, 2):
            pos += 2  # construction_method
        pos += 2  # data_reference_index
        base = _uint(data, pos, base_offset_size)
        pos += base_offset_size
        extents = _uint(data, pos, 2)
        pos += 2
        first_extent = None
        for _ in range(extents):
            pos += index_size
            ext_offset = _uint(data, pos, offset_size)
            ext_length = _uint(data, pos + offset_size, length_size)
            pos += offset_size + length_size
            if first_extent is None:
                first_extent = (base + ext_offset, ext_length)
        if item_id == exif_id and first_extent:
            location, length = first_extent
            f.seek(location)
            blob = f.read(min(length or EXIF_MAX, EXIF_MAX))
            skip = struct.unpack_from(">I", blob, 0)[0]  # cabecera "Exif\0\0" antes del TIFF
            return exif_capture_time(blob[4 + skip :])
    return None
