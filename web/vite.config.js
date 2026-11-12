import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app is served by FastAPI from web/dist, so the base path is "/".
// During local development, /ask and /search are proxied to the API.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/ask": "http://127.0.0.1:8000",
      "/search": "http://127.0.0.1:8000",
      "/feedback": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
      "/readyz": "http://127.0.0.1:8000",
    },
  },
});
