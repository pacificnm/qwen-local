import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // on-host dev only; production goes through the nginx /api/ proxy
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
