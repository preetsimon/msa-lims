import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    // The API is proxied rather than called cross-origin, so the browser sees
    // one origin and there is no CORS configuration to get wrong in dev and
    // then differently wrong in production.
    proxy: {
      "/api": { target: "http://localhost:8002", changeOrigin: true },
      "/health": { target: "http://localhost:8002", changeOrigin: true },
    },
  },
});
