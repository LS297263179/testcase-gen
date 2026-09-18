import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// V2 前端构建配置：
// - 产物输出到仓库 static/v2/（固定文件名，Flask 模板 v2.html 直接引用，服务端部署无需 Node）
// - 开发态 vite dev server 代理 /api 与 /static 到本机 Flask（默认 5000）
const API_TARGET = "http://127.0.0.1:5000"; // 本机 Flask 默认端口（python start.py -p 5000）

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../static/v2",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: "index.html",
      output: {
        entryFileNames: "v2_react.js",
        chunkFileNames: "v2_react-[name].js",
        assetFileNames: "v2_react.[ext]",
      },
    },
  },
  server: {
    port: 5174,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/login": { target: API_TARGET, changeOrigin: true },
    },
  },
});
