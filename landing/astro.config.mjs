import { defineConfig } from "astro/config";

// Dirección pública de la web (se usa para las URLs absolutas: canónica e imagen al compartir).
// Se puede cambiar sin tocar el código con la variable de entorno SITE_URL.
const site = process.env.SITE_URL || "https://www.photobridge.es";

export default defineConfig({ site });
