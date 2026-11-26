// Enlace de descarga directa: GitHub redirige "releases/latest/download/<archivo>"
// al .exe de la última Release publicada, así que el navegador lo descarga sin
// pasar por la página de GitHub. Las Releases las crea el workflow
// .github/workflows/release.yml al subir una etiqueta (por ejemplo v0.1.1).
export const REPO_URL = "https://github.com/Ivanrr462/PhotoBridge";
export const DOWNLOAD_URL = `${REPO_URL}/releases/latest/download/PhotoBridge.exe`;

export const APP_SIZE = "11 MB";
export const APP_VERSION = "0.6.0";
