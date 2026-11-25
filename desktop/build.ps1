# Genera dist\PhotoBridge.exe (un único archivo, sin necesidad de tener Python instalado).
# Uso:  .\build.ps1
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { python -m venv .venv }
$python = ".\.venv\Scripts\python.exe"

& $python -m pip install --quiet --disable-pip-version-check -r requirements.txt -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias." }
& $python -m unittest discover -s tests -t .
if ($LASTEXITCODE -ne 0) { throw "Los tests han fallado; no se genera el ejecutable." }

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name PhotoBridge `
    --icon photobridge.ico `
    --add-data "photobridge/web;photobridge/web" `
    --exclude-module PIL `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller ha fallado." }

Write-Host ""
Write-Host "Listo: $PSScriptRoot\dist\PhotoBridge.exe" -ForegroundColor Green
