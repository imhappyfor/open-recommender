import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  publicDir: false,
  server: {
    host: "127.0.0.1",
    port: 5173,
    forwardConsole: false,
    fs: {
      deny: [".env", ".env.*", "*.{crt,pem}", "**/.git/**", "**/*.{orf,orfb,key}"],
    },
  },
});
