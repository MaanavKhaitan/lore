import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Python (lore/viz/html.py) is the inliner: the build emits exactly one JS
// and one CSS file with fixed names; scripts/sync-assets.mjs copies them into
// src/lore/viz/assets/ and rewrites index.html into the marker template.
export default defineConfig({
  plugins: [react()],
  build: {
    cssCodeSplit: false,
    // Inline the woff2 fonts as base64 into viz.css — the export must stay a
    // single self-contained file with no network requests.
    assetsInlineLimit: 200_000,
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
        entryFileNames: "viz.js",
        assetFileNames: "viz[extname]",
      },
    },
  },
});
