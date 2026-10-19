import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// In development Vite proxies the API so the app, cookies and WebSocket share one origin,
// the same way Traefik serves them in Docker Compose.
const api = process.env.JARVIS_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg"],
      manifest: {
        name: "Jarvis",
        short_name: "Jarvis",
        description: "Your personal supervisor agent",
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
        // Never serve the API, auth redirects or the socket from the service worker cache.
        navigateFallbackDenylist: [/^\/api/, /^\/auth/, /^\/ws/],
      },
    }),
  ],
  server: {
    host: true,
    proxy: {
      "/api": api,
      "/auth": api,
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
});
