import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
import { securityHeaders } from "./security-headers.ts";
export { securityHeaders } from "./security-headers.ts";
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
