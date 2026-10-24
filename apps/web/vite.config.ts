import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// In development Vite proxies the API so the app, cookies and WebSocket share one origin,
// the same way Traefik serves them in Docker Compose.
const api = process.env.NEXUS_API_URL ?? process.env.JARVIS_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg"],
      manifest: {
        name: "Nexus",
        short_name: "Nexus",
        description: "Your own assistant and its agent teams",
        theme_color: "#05070d",
        background_color: "#05070d",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
          { src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "maskable" },
        ],
      },
      workbox: {
        // Push notifications and taps on them (public/push-sw.js).
        importScripts: ["push-sw.js"],
        // Never serve the API, auth redirects or the socket from the service worker cache.
        navigateFallbackDenylist: [/^\/api/, /^\/auth/, /^\/ws/, /^\/privacy/, /^\/terms/],
        // The 3D scene's chunk (three.js) is over the 2 MiB default; precache it for offline use.
        maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
      },
    }),
  ],
  // The orb's lazy chunk carries three.js, which is about 1 MB minified on its own.
  build: { chunkSizeWarningLimit: 1100 },
  server: {
    host: true,
    proxy: {
      "/api": api,
      "/auth": api,
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
});
