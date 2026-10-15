import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build lands inside the Python package so `bf serve` ships it without Node.
// In dev, API calls are proxied to a running `bf serve` (default port 8770).
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/break_forecast/web_dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8770",
      "/runs": "http://127.0.0.1:8770",
    },
  },
});
