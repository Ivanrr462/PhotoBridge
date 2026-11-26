import { defineConfig } from "astro/config";

// Dirección pública de la web (se usa para las URLs absolutas, como la imagen al compartir).
// Orden: SITE_URL si se define a mano (p. ej. tu dominio propio) → dominio de producción de
// Vercel (lo pone Vercel automáticamente al compilar) → valor por defecto.
const vercelDomain = process.env.VERCEL_PROJECT_PRODUCTION_URL;
const site = process.env.SITE_URL || (vercelDomain ? `https://${vercelDomain}` : "https://photobridge.vercel.app");

export default defineConfig({ site });
