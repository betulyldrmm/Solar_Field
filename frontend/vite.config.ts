import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: /api is proxied to the FastAPI backend, so no CORS setup is needed.
// Change the target if the backend runs elsewhere (e.g. the UNO Q's IP).
export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 600, // recharts alone is ~400 kB
    rollupOptions: { output: { manualChunks: { charts: ["recharts"] } } },
  },
  server: {
    port: 5173,
    proxy: { "/api": process.env.SFS_BACKEND_URL ?? "http://127.0.0.1:8000" },
  },
});
