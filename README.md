# PhotoBridge

Pasa fotos y vídeos del móvil al ordenador por la wifi de casa. Sin cables, sin cuentas y sin nube.

1. Abres **PhotoBridge.exe** en el ordenador y aparece un código QR.
2. Escaneas el QR con la cámara del móvil y se abre una página web servida por tu propio PC.
3. Eliges las fotos, pulsas **Enviar** y llegan a `Imágenes\PhotoBridge`.

El móvil y el ordenador tienen que estar en la **misma red**. En el móvil no se instala nada.

## Estructura

```
desktop/                 App de escritorio (Python)
  photobridge/
    app.py               Punto de entrada (ventana o modo consola)
    gui.py               Ventana Tkinter: QR, contador y carpeta destino
    server.py            Servidor HTTP local (web del móvil + API de subida)
    storage.py           Guardado seguro: nombres válidos, sin sobrescribir, sin archivos a medias
    network.py           IP local y filtro de clientes (solo red local)
    config.py            Token, puerto y carpeta (%APPDATA%\PhotoBridge\config.json)
    qr.py                Generación del QR
    web/                 Página que ve el móvil (HTML/CSS/JS sin dependencias)
  tests/                 Tests (unittest)
  build.ps1              Genera dist\PhotoBridge.exe con PyInstaller
landing/                 Web pública de descarga (Astro)
```

## App de escritorio

Requisitos: Python 3.11 o superior.

```powershell
cd desktop
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt

# Abrir con ventana
.\.venv\Scripts\python.exe -m photobridge

# Modo consola (muestra el QR en la terminal)
.\.venv\Scripts\python.exe -m photobridge --no-gui

# Tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .

# Generar el .exe (pasa los tests y deja el resultado en dist\PhotoBridge.exe)
.\build.ps1
```

### Cómo funciona por dentro

- Al arrancar, el servidor escucha en el puerto **8765** (o en uno libre si está ocupado) en todas las interfaces.
- El QR contiene `http://<IP-local>:<puerto>/?t=<token>`. El token se genera la primera vez y se guarda en la configuración.
- El móvil sube cada archivo con `POST /api/upload`. El cuerpo es el archivo tal cual, y van en cabeceras el token, el nombre y la fecha original (`X-Token`, `X-File-Name`, `X-Last-Modified`). No se usa multipart, así que el servidor escribe directamente al disco por bloques.
- Seguridad: se rechaza todo lo que no traiga el token o no venga de una IP privada. Los nombres se sanean (sin rutas ni caracteres prohibidos en Windows), nunca se sobrescribe un archivo y los envíos interrumpidos se borran.

## Landing

```powershell
cd landing
npm install
npm run dev      # http://localhost:4321
npm run build    # genera landing/dist (estático, para Netlify, Vercel, GitHub Pages…)
```

Los botones de descarga apuntan a `https://github.com/Ivanrr462/PhotoBridge/releases/latest/download/PhotoBridge.exe` (definido en `landing/src/config.ts`). GitHub redirige ese enlace al `.exe` de la última Release, así que el navegador lo descarga directamente, sin abrir la página de GitHub.

## Publicar una versión nueva

1. Sube el número de versión en `desktop/photobridge/__init__.py` y en `landing/src/config.ts` (`APP_VERSION`).
2. Haz commit, crea la etiqueta y súbela:

   ```powershell
   git tag v0.1.1
   git push origin main v0.1.1
   ```

3. El workflow [`.github/workflows/release.yml`](.github/workflows/release.yml) pasa los tests, compila `PhotoBridge.exe` en Windows y publica la Release con el `.exe` adjunto. El enlace de descarga de la landing apunta solo a la nueva versión.

## Siguientes pasos

- Firmar el `.exe` con un certificado de código, para quitar el aviso de SmartScreen.
- Opción de arrancar con Windows y minimizar a la bandeja del sistema.
- Versión para macOS (el código es multiplataforma; falta el empaquetado).
- Enviar también del PC al móvil.
