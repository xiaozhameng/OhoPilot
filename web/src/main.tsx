import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

/**
 * 整个应用就是一个视图：侧栏常驻，main 区在「聊天」和「控制台面板」之间切换
 * （切换逻辑在 App 里，由 router.tsx 的 useRoute 驱动）。
 *
 * 服务端把 /console、/extensions 也指向这份 index.html，所以直接打开
 * /console/xxx 刷新也是同一个入口——App 按地址直接渲染出控制台面板。
 */

const root = document.getElementById("root");
if (!root) throw new Error("index.html 里缺少 #root");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
