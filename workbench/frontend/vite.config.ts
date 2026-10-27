import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist", emptyOutDir: true,
    // Icons shared by several pages go in one file instead of dozens of tiny ones
    rollupOptions: { output: { manualChunks: (id) => (id.includes("node_modules/lucide-react") ? "icons" : undefined) } },
  },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
