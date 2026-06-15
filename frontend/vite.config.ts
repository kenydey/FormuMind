import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Pure client-side SPA (no SSR) to keep future 3D canvas integration simple.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
