import type { Health, SessionSummary } from "../types";
import { IconPlus, Logo } from "./Icons";

interface SidebarProps {
  sessions: SessionSummary[];
  activeId: string | null;
  health: Health | null;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onClose: () => void;
}

/** 会话列表里的时间：今天显示时分，昨天显示「昨天」，更早显示月日。
 *  绝对时间戳（2026-10-22T10:06:59）在一列里读起来太重。 */
function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";

  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "昨天";

  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

export function Sidebar({ sessions, activeId, health, onSelect, onNew, onClose }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__logo">
          <Logo size={26} />
        </span>
        <div className="sidebar__title">
          <strong>OhoPilot</strong>
          <span>跑在你自己电脑上的 agent</span>
        </div>
        <button className="icon-btn sidebar__close" onClick={onClose} title="收起侧栏">
          ×
        </button>
      </div>

      <button className="new-chat" onClick={onNew}>
        <IconPlus size={15} />
        新对话
      </button>

      <div className="sidebar__section">最近</div>

      <nav className="session-list">
        {sessions.length === 0 && <p className="sidebar__empty">还没有会话，问一句试试。</p>}
        {sessions.map((session) => (
          <button
            key={session.session_id}
            className={`session${session.session_id === activeId ? " session--active" : ""}`}
            onClick={() => onSelect(session.session_id)}
            title={session.title || "（无标题）"}
          >
            <span className="session__title">{session.title || "（无标题）"}</span>
            <span className="session__meta">
              {formatTime(session.updated_at)}
              <em>{session.messages} 条</em>
            </span>
          </button>
        ))}
      </nav>

      <footer className="sidebar__footer">
        <span className={`dot ${health?.ok ? "dot--ok" : "dot--bad"}`} />
        <span className="sidebar__model" title={health?.model ?? ""}>
          {health?.model ?? "未连接"}
        </span>
      </footer>
    </aside>
  );
}
