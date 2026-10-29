import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8300",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        // 阶段 11：echarts/zrender 与其余 vendor 拆为独立 chunk，
        // 消除 >500kB 构建告警并改善缓存局部性
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("zrender")) return "zrender";
            if (id.includes("echarts")) return "echarts";
            return "vendor";
          }
        },
      },
    },
  },
});
