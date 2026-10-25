# OhoPilot 桌面壳（Tauri 2）

> **状态：脚手架已就绪，打包需要 Rust 工具链**（仓库 CI/沙箱里没有，所以 .app 需要你本机构建一次）。

## 壳的定位

壳 ≠ 重新实现一遍界面。它就是一个**指向本机服务的原生窗口**：

```
┌─ OhoPilot.app ────────────────┐
│  WebView 加载                  │
│  http://127.0.0.1:8737  ───────┼──→ oho-server（Python，内核常驻）
│  （网页工作台，100% 复用）      │         ├─ SQLite ~/.ohopilot/
└────────────────────────────────┘         └─ 工具/MCP/RAG……
```

好处：界面零改动（还是 web/ 那套 React）、同源没有 CORS 问题、后端升级不用重发壳。

## 构建 .app 的步骤（在你的机器上，一次性）

```bash
# 1. 装 Rust（约 5 分钟，rustup 会装 cargo）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# 2. 装 Tauri CLI（可以不进 PATH，npx 拉来用）
npm install -D @tauri-apps/cli@^2   # 在仓库根目录执行，会写进 package.json devDependencies

# 3. 先把内核跑起来（壳加载的就是它）
./start.sh --no-open

# 4. 开发模式（热重载窗口）
npx tauri dev

# 5. 打包 .app / .dmg
npx tauri build
# 产物在 src-tauri/target/release/bundle/
```

## 边界说明

- 窗口 URL 写死 `127.0.0.1:8737`（`tauri.conf.json`）。服务没起时窗口会显示打不开——这是刻意的：**壳不负责养内核**（免得出现双份 Python 进程）。将来第 28 步之后可以加「壳自动拉起内核子进程」，那是 `main.rs` 里加几行的事。
- `bundle.icon` 目前为空：打包前需要 `npx tauri icon path/to/icon.png` 生成一套图标，否则 bundle 阶段会报错。
