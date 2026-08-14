import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// WebUI 独立部署（移动端优先）。世界服务地址经 VITE_WORLD_API 配置
// （默认 http://localhost:6288，需后端 allowed_origins 放行本开发源）。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    host: true,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
