import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Served from https://<user>.github.io/<repo>/ on GitHub Pages, so assets use relative paths.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
});
