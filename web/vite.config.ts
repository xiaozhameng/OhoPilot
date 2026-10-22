import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 后端地址。默认 8737（和 config.py 里的 OHOPILOT_PORT 默认值一致）；
// start.sh --dev --port 9000 会通过环境变量把它换掉，否则代理会打到空端口。
const API = process.env.OHOPILOT_API ?? "http://127.0.0.1:8737";

export default defineConfig({
  plugins: [react()],

  // 构建产物直接落到 python 包里，由 FastAPI 托管：
  // 这样 `oho-server` 一条命令就同时是 API 和界面，不需要另起一个静态服务器。
  build: {
    outDir: "../ohopilot/server/static",
    emptyOutDir: true,
  },

  server: {
    port: 5173,
    // 固定监听 IPv4。vite 默认绑 ::1（IPv6），于是 http://127.0.0.1:5173 打不开、
    // 只有 http://localhost:5173 能开——两个地址表现不一致，很难排查。
    host: "127.0.0.1",
    // 开发时把 API 请求转给后端，前端代码里一律用相对路径（生产同源，不用改）
    proxy: {
      "/health": API,
      "/sessions": API,
      "/chat": API,
      "/approvals": API,
      "/settings": API,
    },
  },
});
