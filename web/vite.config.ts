/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Development only: proxy /api to the local FastAPI backend.
// Production deployment (Phase 10) serves the built assets same-origin
// behind Nginx, so no absolute API URL is hardcoded here.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // The dev proxy streams SSE responses incrementally by default, and
      // the backend already sends Cache-Control: no-cache and
      // X-Accel-Buffering: no on text/event-stream responses.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/**/*.test.ts"],
  },
});
