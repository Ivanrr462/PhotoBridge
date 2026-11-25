"""Utilidades de red local."""

from __future__ import annotations

import ipaddress
import socket


def _is_lan(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private and not addr.is_loopback and not addr.is_link_local


def get_lan_ip() -> str | None:
    """IP del ordenador en la red local (la de la interfaz con salida por defecto)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Un socket UDP "conectado" no envía nada; solo sirve para que el sistema
        # elija la interfaz de red que se usaría para salir.
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        if _is_lan(ip):
            return ip
    except OSError:
        pass
    finally:
        sock.close()

    try:
        candidates = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        candidates = []
    for ip in candidates:
        if _is_lan(ip):
            return ip
    return None


def is_allowed_client(ip: str) -> bool:
    """Solo se aceptan conexiones desde la red local o el propio equipo."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_private or addr.is_loopback or addr.is_link_local


def computer_name() -> str:
    return socket.gethostname() or "tu ordenador"
