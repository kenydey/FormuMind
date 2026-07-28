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
  build: {
    rollupOptions: {
      output: {
        // Split the heavy vendors out of the entry chunk. Everything landed in
        // one ~2.3 MB file, which the deployment target feels: this ships to
        // corporate intranets where first load is not on a fast link, and a
        // single chunk also means any app change re-downloads AG Grid, KaTeX
        // and Recharts along with it. These three are stable across releases,
        // so cache them separately.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          // React must be claimed first and explicitly. Otherwise whichever
          // vendor group happens to reach it first absorbs it — react-dom
          // landed inside vendor-ag-grid, which made the entry chunk import
          // 1.1 MB of grid code statically and undid the lazy loading.
          if (
            /node_modules\/(react|react-dom|scheduler)\//.test(id) ||
            id.includes("react/jsx-runtime")
          ) {
            return "vendor-react";
          }
          if (id.includes("ag-grid")) return "vendor-ag-grid";
          if (id.includes("recharts") || id.includes("d3-")) return "vendor-charts";
          if (
            id.includes("katex") ||
            id.includes("react-markdown") ||
            id.includes("remark") ||
            id.includes("rehype") ||
            id.includes("micromark") ||
            id.includes("mdast")
          ) {
            return "vendor-markdown";
          }
          return undefined;
        },
      },
    },
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
