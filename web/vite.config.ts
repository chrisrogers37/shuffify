import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
export const securityHeaders = {
  "Content-Security-Policy":
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://*.scdn.co https://*.spotifycdn.com; connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://accounts.spotify.com",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
};
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwind()],
  define: {
    "import.meta.env.VITE_FIXTURE_MODE": JSON.stringify(
      mode === "fixture" ? "true" : "false",
    ),
  },
  build: { assetsInlineLimit: 0, target: "es2022" },
  preview: { port: 4173, strictPort: true, headers: securityHeaders },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:5000",
      "/login": "http://127.0.0.1:5000",
      "/callback": "http://127.0.0.1:5000",
      "/logout": "http://127.0.0.1:5000",
      "/health": "http://127.0.0.1:5000",
    },
  },
}));
