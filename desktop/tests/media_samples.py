"""Genera archivos JPEG, HEIC y MP4 mínimos con metadatos conocidos para los tests."""

import struct

QT_EPOCH_OFFSET = 2082844800


def tiff_with_date(date: str = "2024:07:14 18:30:00", offset: str | None = "+02:00") -> bytes:
    """Bloque TIFF little-endian: IFD0 -> Exif IFD con DateTimeOriginal (y OffsetTimeOriginal)."""
    exif_entries = [(0x9003, date.encode() + b"\0")]
    if offset:
        exif_entries.append((0x9011, offset.encode() + b"\0"))

    ifd0_at = 8
    ifd0_size = 2 + 12 + 4
    exif_at = ifd0_at + ifd0_size
    exif_size = 2 + 12 * len(exif_entries) + 4
    data_at = exif_at + exif_size

    out = bytearray(b"II*\0" + struct.pack("<I", ifd0_at))
    out += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, exif_at) + struct.pack("<I", 0)
    blobs = b""
    out += struct.pack("<H", len(exif_entries))
    for tag, value in exif_entries:
        out += struct.pack("<HHII", tag, 2, len(value), data_at + len(blobs))
        blobs += value
    out += struct.pack("<I", 0)
    return bytes(out + blobs)


def jpeg_with_exif(**kwargs) -> bytes:
    payload = b"Exif\0\0" + tiff_with_date(**kwargs)
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return b"\xff\xd8" + app1 + b"\xff\xda\x00\x02" + b"\x00" * 64 + b"\xff\xd9"


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def full_box(kind: bytes, version: int, payload: bytes) -> bytes:
    return box(kind, bytes([version, 0, 0, 0]) + payload)


def mp4_with_creation(epoch: int, brand: bytes = b"isom", mdat_first: bool = True) -> bytes:
    ftyp = box(b"ftyp", brand + b"\0\0\0\0" + brand)
    mvhd = full_box(b"mvhd", 0, struct.pack(">II", epoch + QT_EPOCH_OFFSET, epoch + QT_EPOCH_OFFSET) + b"\0" * 88)
    moov = box(b"moov", mvhd)
    mdat = box(b"mdat", b"\0" * 1024)
    return ftyp + (mdat + moov if mdat_first else moov + mdat)


def heic_with_exif(**kwargs) -> bytes:
    exif_item = struct.pack(">I", 6) + b"Exif\0\0" + tiff_with_date(**kwargs)
    ftyp = box(b"ftyp", b"heic" + b"\0\0\0\0" + b"mif1heic")

    def build_meta(exif_offset: int) -> bytes:
        infe = full_box(b"infe", 2, struct.pack(">HH", 1, 0) + b"Exif" + b"\0")
        iinf = full_box(b"iinf", 0, struct.pack(">H", 1) + infe)
        iloc = full_box(
            b"iloc",
            0,
            bytes([0x44, 0x00])  # offset_size=4, length_size=4, base_offset_size=0
            + struct.pack(">H", 1)  # item_count
            + struct.pack(">HHH", 1, 0, 1)  # item_ID, data_reference_index, extent_count
            + struct.pack(">II", exif_offset, len(exif_item)),
        )
        hdlr = full_box(b"hdlr", 0, b"\0\0\0\0pict" + b"\0" * 12 + b"\0")
        return full_box(b"meta", 0, hdlr + iinf + iloc)

    meta_len = len(build_meta(0))
    exif_offset = len(ftyp) + meta_len + 8  # justo tras la cabecera de mdat
    return ftyp + build_meta(exif_offset) + box(b"mdat", exif_item)
