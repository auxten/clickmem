import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// ClickMem dashboard SPA — served by FastAPI from /dashboard in production
// (the wheel bundles `dist/`). In dev, run `pnpm dev` and the proxy below
// forwards every `/v1/*` and `/sse` request to the local FastAPI server.
export default defineConfig({
  plugins: [react()],
  base: "/dashboard/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:9527",
      "/sse": {
        target: "http://127.0.0.1:9527",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
