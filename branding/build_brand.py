"""Genera todas las variantes del logo a partir de logo.svg y logo-small.svg.

Uso (desde la raíz del repo):
    desktop\\.venv\\Scripts\\python.exe branding\\build_brand.py

Necesita Microsoft Edge o Google Chrome (para rasterizar los SVG) y Pillow (para el .ico).
Después copia cada archivo a la app de escritorio y a la landing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
EXPORT = ROOT / "export"

BROWSERS = [
    Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
]

WORDMARK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 320" width="1200" height="320">
  <defs>
    <linearGradient id="word" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#4353ff"/>
      <stop offset="1" stop-color="#19c3fb"/>
    </linearGradient>
  </defs>
  <svg x="20" y="20" width="280" height="280" viewBox="0 0 512 512">{icon}</svg>
  <text x="340" y="205" font-family="Segoe UI, Inter, system-ui, sans-serif" font-size="138" font-weight="700" letter-spacing="-3"><tspan fill="{text_color}">Photo</tspan><tspan fill="url(#word)">Bridge</tspan></text>
</svg>
"""


def find_browser() -> Path:
    for candidate in BROWSERS:
        if candidate.is_file():
            return candidate
    raise SystemExit("No se encontró Edge ni Chrome para rasterizar los SVG.")


def svg_inner(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return re.search(r"<svg[^>]*>(.*)</svg>", text, re.S).group(1)


def render(browser: Path, svg: Path, out: Path, width: int, height: int) -> None:
    """Rasteriza un SVG a PNG con fondo transparente usando el navegador en modo headless."""
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(
            "<!doctype html><html><body style='margin:0;background:transparent'>"
            f"<img src='{svg.as_uri()}' width='{width}' height='{height}' style='display:block'>"
            "</body></html>",
            encoding="utf-8",
        )
        subprocess.run(
            [
                str(browser),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--default-background-color=00000000",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                f"--window-size={width},{height}",
                f"--screenshot={out}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    print(f"  {out.relative_to(REPO)}")


def main() -> None:
    browser = find_browser()
    EXPORT.mkdir(exist_ok=True)

    icon = svg_inner(ROOT / "logo.svg")
    (ROOT / "logo-horizontal.svg").write_text(WORDMARK.format(icon=icon, text_color="#1c1f2a"), encoding="utf-8")
    (ROOT / "logo-horizontal-white.svg").write_text(WORDMARK.format(icon=icon, text_color="#ffffff"), encoding="utf-8")

    print("Exportando PNG:")
    for size in (1024, 512, 256, 180, 128, 64):
        render(browser, ROOT / "logo.svg", EXPORT / f"logo-{size}.png", size, size)
    for size in (48, 32, 16):
        render(browser, ROOT / "logo-small.svg", EXPORT / f"logo-{size}.png", size, size)
    render(browser, ROOT / "logo-horizontal.svg", EXPORT / "logo-horizontal.png", 2400, 640)
    render(browser, ROOT / "logo-horizontal-white.svg", EXPORT / "logo-horizontal-white.png", 2400, 640)

    print("Generando .ico:")
    sizes = [256, 128, 64, 48, 32, 16]
    images = [Image.open(EXPORT / f"logo-{s}.png").convert("RGBA") for s in sizes]
    ico = EXPORT / "photobridge.ico"
    images[0].save(ico, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[1:])
    print(f"  {ico.relative_to(REPO)}")

    print("Copiando a la app y a la landing:")
    copies = {
        ROOT / "logo.svg": [REPO / "desktop/photobridge/web/icon.svg", REPO / "landing/public/logo.svg"],
        ROOT / "logo-small.svg": [REPO / "desktop/photobridge/web/favicon.svg", REPO / "landing/public/favicon.svg"],
        EXPORT / "logo-256.png": [REPO / "desktop/photobridge/web/app-icon-256.png"],
        EXPORT / "logo-32.png": [REPO / "desktop/photobridge/web/app-icon-32.png"],
        EXPORT / "logo-64.png": [REPO / "desktop/photobridge/web/app-icon-64.png"],
        EXPORT / "logo-180.png": [REPO / "desktop/photobridge/web/apple-touch-icon.png", REPO / "landing/public/apple-touch-icon.png"],
        EXPORT / "photobridge.ico": [REPO / "desktop/photobridge.ico"],
        EXPORT / "logo-horizontal.png": [REPO / "landing/public/og-image.png"],
    }
    for source, targets in copies.items():
        for target in targets:
            shutil.copyfile(source, target)
            print(f"  {target.relative_to(REPO)}")


if __name__ == "__main__":
    main()
