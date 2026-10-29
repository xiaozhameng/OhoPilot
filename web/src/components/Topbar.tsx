import { Link } from "../router";
import type { Health } from "../types";
import { IconChart, IconMenu, IconSettings } from "./Icons";

interface TopbarProps {
  health: Health | null;
  title: string;
  /** 当前会话实际生效的工作目录（第 39 步）。不传就退回 health.workspace。 */
  workspace: WorkspaceInfo;
  onOpenSettings: () => void;
  onOpenUsage: () => void;
  onToggleSidebar: () => void;
}

/**
 * 顶栏显示的是「这一轮真正会用到的目录」，不是进程默认值：
 * 空间绑了目录时两者不一样，显示错了用户会以为文件写到了别处而找不到。
 * space 非空表示这个目录来自空间绑定（而不是设置抽屉里那个默认值）。
 */
export interface WorkspaceInfo {
  path: string;
  space?: string;
}

/** 顶栏只放「你现在在什么环境里」这类信息：
 *  模型、工作目录、工具数。切换入口收在设置抽屉里，顶栏保持干净。 */
export function Topbar({
  health,
  title,
  workspace,
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
            <span
              className={`pill pill--path${workspace.space ? " pill--path-space" : ""}`}
              title={
                workspace.space
                  ? `工作目录来自空间「${workspace.space}」的绑定：${workspace.path}`
                  : `默认工作目录（设置里可改）：${workspace.path}`
              }
            >
              {workspace.path}
            </span>
            <span className="pill pill--dim" title="已注册工具数">
              {health.tools.length} 个工具
            </span>
          </>
        )}
        {/* 同窗口切到控制台（客户端路由，不刷新）：整页刷新会打断正在跑的回答。
            扩展中心已并入控制台后台（技能与 MCP 是侧栏里的一页），只留一个入口 */}
        <Link className="pill pill--link" to="/console" title="控制台：用量分析 + 技能与 MCP">
          控制台
        </Link>
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
