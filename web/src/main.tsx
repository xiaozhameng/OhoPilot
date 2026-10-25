import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ConsolePage } from "./components/ConsolePage";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html 里缺少 #root");

// 同一个 SPA 的两个入口（服务端把 /console 也指向这份 index.html）：
// 主工作台管「这一次对话」，控制台管「一直以来的总账」。
const isConsole = window.location.pathname.startsWith("/console");

createRoot(root).render(
  <StrictMode>{isConsole ? <ConsolePage /> : <App />}</StrictMode>,
);
