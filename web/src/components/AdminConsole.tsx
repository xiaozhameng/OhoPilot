import { Suspense, lazy } from "react";
import { Link, useRoute } from "../router";
import { CallsPage } from "./admin/CallsPage";
import { ToolsPage } from "./admin/ToolsPage";
import { SessionsPage } from "./admin/SessionsPage";
import { MemoryPage } from "./admin/MemoryPage";
import { ExtensionsPage } from "./ExtensionsPage";

/** 总览页懒加载：只有它用 recharts，单独拆包后其余页面不用背 ~400KB 的图表库。 */
const OverviewPage = lazy(() =>
  import("./admin/OverviewPage").then((m) => ({ default: m.OverviewPage })),
);

/** 按 pathname 挑出该渲染的页面；不认识的一律回落总览 */
export function adminPageKey(pathname: string): string {
  if (pathname.startsWith("/console/calls") || pathname.startsWith("/calls")) return "calls";
  if (pathname.startsWith("/console/tools")) return "tools";
  if (pathname.startsWith("/console/sessions")) return "sessions";
  if (pathname.startsWith("/console/memory")) return "memory";
  if (pathname.startsWith("/console/extensions") || pathname.startsWith("/extensions"))
    return "extensions";
  return "overview";
}

/**
 * 控制台的页面清单：和聊天侧栏的六个菜单一一对应。
 * match 用于「当前地址算不算这页」的判断，顺序即优先级——
 * /console/calls 必须先于 /console 判断，否则会被总览吃掉。
 */
const NAV: Array<{ href: string; label: string; match: string; icon: string }> = [
  { href: "/console", label: "数据概览", match: "/console", icon: "▦" },
  { href: "/console/calls", label: "调用流水", match: "/console/calls", icon: "☰" },
  { href: "/console/tools", label: "工具调用", match: "/console/tools", icon: "⚒" },
  { href: "/console/sessions", label: "会话分析", match: "/console/sessions", icon: "💬" },
  { href: "/console/memory", label: "记忆数据", match: "/console/memory", icon: "✦" },
  { href: "/console/extensions", label: "技能与 MCP", match: "/console/extensions", icon: "⚡" },
];

/**
 * 控制台面板：渲染在聊天窗口的 main 区里（不是独立页面）。
 *
 * 为什么从全屏页改成内嵌面板：侧栏点「数据概览」这类菜单时，用户预期的是
 * 「右边这一块变成那页」，左侧会话树原地不动——切回来不用找路。
 * 原来的全屏 .admin 布局（自带的左侧导航 + 返回工作台）因此整个退役：
 * 导航职责由侧栏 + 这排 tab 分担，「返回」动作就是点侧栏里任意会话。
 */
export function ConsolePanel() {
  const key = adminPageKey(useRoute());

  const page =
    key === "calls" ? (
      <CallsPage />
    ) : key === "tools" ? (
      <ToolsPage />
    ) : key === "sessions" ? (
      <SessionsPage />
    ) : key === "memory" ? (
      <MemoryPage />
    ) : key === "extensions" ? (
      <ExtensionsPage />
    ) : (
      <OverviewPage />
    );

  return (
    <div className="cpanel">
      <nav className="cpanel__tabs">
        {NAV.map((item) => (
          <Link
            key={item.href}
            to={item.href}
            className={`cpanel__tab${key === adminPageKey(item.match) ? " is-active" : ""}`}
          >
            <span className="cpanel__tab-icon" aria-hidden>
              {item.icon}
            </span>
            {item.label}
          </Link>
        ))}
      </nav>
      <div className="cpanel__body">
        <Suspense fallback={<p className="settings__note">加载中…</p>}>{page}</Suspense>
      </div>
    </div>
  );
}
