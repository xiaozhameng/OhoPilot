import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ConsolePage } from "./components/ConsolePage";
import { ExtensionsPage } from "./components/ExtensionsPage";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html 里缺少 #root");

// 同一个 SPA 的三个入口（服务端把 /console、/extensions 也指向这份 index.html）：
// 主工作台管「这一次对话」，控制台管「开发者的总账」，扩展中心管「agent 的能力配置」。
const path = window.location.pathname;

createRoot(root).render(
  <StrictMode>
    {path.startsWith("/console") ? (
      <ConsolePage />
    ) : path.startsWith("/extensions") ? (
      <ExtensionsPage />
    ) : (
      <App />
    )}
  </StrictMode>,
);
