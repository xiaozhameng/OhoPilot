import type { Health } from "../types";
import { IconChart, IconMenu, IconSettings } from "./Icons";

interface TopbarProps {
  health: Health | null;
  title: string;
  onOpenSettings: () => void;
  onOpenUsage: () => void;
  onToggleSidebar: () => void;
}

/** 顶栏只放「你现在在什么环境里」这类信息：
 *  模型、工作目录、工具数。切换入口收在设置抽屉里，顶栏保持干净。 */
export function Topbar({
  health,
  title,
  onOpenSettings,
  onOpenUsage,
  onToggleSidebar,
}: TopbarProps) {
  return (
    <header className="topbar">
      <button className="icon-btn topbar__menu" onClick={onToggleSidebar} title="会话列表">
        <IconMenu size={16} />
      </button>

      <div className="topbar__title">{title}</div>

      <div className="topbar__meta">
        {health && (
          <>
            <span className="pill" title="当前模型">
              {health.model}
            </span>
            <span className="pill pill--path" title={health.workspace}>
              {health.workspace}
            </span>
            <span className="pill pill--dim" title="已注册工具数">
              {health.tools.length} 个工具
            </span>
          </>
        )}
        <a className="pill pill--link" href="/console" title="开发者控制台（跨会话总账）">
          控制台
        </a>
        <button className="icon-btn" onClick={onOpenUsage} title="Token 用量（开发者面板）">
          <IconChart size={15} />
        </button>
        <button className="icon-btn" onClick={onOpenSettings} title="设置">
          <IconSettings size={15} />
        </button>
      </div>
    </header>
  );
}
