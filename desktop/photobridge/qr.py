"""Generación del código QR como matriz de módulos (sin depender de librerías de imagen)."""

from __future__ import annotations

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def qr_matrix(data: str, border: int = 2) -> list[list[bool]]:
    qr = qrcode.QRCode(border=border, error_correction=ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.get_matrix()


def print_qr(data: str) -> None:
    qr = qrcode.QRCode(border=2, error_correction=ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
