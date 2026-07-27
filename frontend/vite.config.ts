// defineConfig comes from vitest/config, not vite: vitest 4 dropped the type
// augmentation that `/// <reference types="vitest" />` relied on to teach vite's
// UserConfig about the `test` key, so a clean install failed `tsc -b`.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Pure client-side SPA (no SSR) to keep future 3D canvas integration simple.
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    include: ["ag-grid-react", "ag-grid-community"],
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
