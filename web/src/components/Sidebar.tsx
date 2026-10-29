import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useRoute } from "../router";
import { adminPageKey } from "./AdminConsole";
import type { Health, SessionSummary, Space } from "../types";
import { IconChevron, IconPlus, Logo } from "./Icons";

/**
 * 侧栏（第 34 步重构）：一个侧栏装两件事，中间用横线隔开。
 *
 *  上：控制台的几个入口（一级菜单）——点击新标签打开 /console/*，聊天不被打断；
 *  下：空间 + 该空间下的历史会话（二级）——默认空间兜底全部会话，默认收起，
 *      和 WorkBuddy 的「空间」一个套路：先选项目，再选那次对话。
 *
 * 为什么不把控制台页面直接塞进这个 SPA：控制台是"翻旧账"的地方，聊天是"干活"的地方，
 * 同屏互相挤；两个标签页各自占满窗口，切回来聊天记录还在（这条是用户第 29 步拍的板）。
 */

/**
 * 一级菜单的文案统一成「名词+看什么」的形式（数据概览 / 会话分析 / 记忆数据）：
 * 光写「总览」「会话」「记忆」，用户得点进去才知道那页到底放着什么。
 */
const NAV: Array<{ href: string; label: string; icon: string }> = [
  { href: "/console", label: "数据概览", icon: "▦" },
  { href: "/console/calls", label: "调用流水", icon: "☰" },
  { href: "/console/tools", label: "工具调用", icon: "⚒" },
  { href: "/console/sessions", label: "会话分析", icon: "💬" },
  { href: "/console/memory", label: "记忆数据", icon: "✦" },
  { href: "/console/extensions", label: "技能与 MCP", icon: "⚡" },
];

interface SidebarProps {
  sessions: SessionSummary[];
  spaces: Space[];
  activeId: string | null;
  /** 新会话归到哪个空间 */
  activeSpaceId: number;
  health: Health | null;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onClose: () => void;
  onRename: (sessionId: string, alias: string) => Promise<void>;
  onMoveSession: (sessionId: string, spaceId: number) => Promise<void>;
  onSelectSpace: (spaceId: number) => void;
  onCreateSpace: (name: string) => Promise<void>;
  onUpdateSpace: (spaceId: number, patch: { name?: string; folder?: string }) => Promise<void>;
  onDeleteSpace: (spaceId: number) => Promise<void>;
}

/** 会话列表里的时间：今天显示时分，昨天显示「昨天」，更早显示月日。 */
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

/** 目录太长时只留末尾两段：/Users/x/Desktop/AI软件开发 → …/AI软件开发 */
function shortPath(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts.length <= 2 ? path : `…/${parts.slice(-2).join("/")}`;
}

function FolderIcon({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.2c.5 0 1 .2 1.4.6l1.3 1.3h7.1A2.5 2.5 0 0 1 21 9.4v7.1a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5v-9Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** 会话项：标题 + 时间/条数 + 悬停「⋯」（改名 / 移动到空间）。 */
function SessionItem({
  session,
  active,
  spaces,
  onSelect,
  onRename,
  onMove,
}: {
  session: SessionSummary;
  active: boolean;
  spaces: Space[];
  onSelect: () => void;
  onRename: (alias: string) => Promise<void>;
  onMove: (spaceId: number) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [menu, setMenu] = useState<"root" | "move" | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // 点外面收起菜单：菜单开着时全局监听，关掉就摘
  useEffect(() => {
    if (!menu) return;
    function onDocClick(ev: MouseEvent) {
      if (!boxRef.current?.contains(ev.target as Node)) setMenu(null);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [menu]);

  const name = session.alias || session.title || "（无标题）";

  async function commit() {
    if (saving) return;
    const next = draft.trim();
    if (next === (session.alias || "")) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await onRename(next);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <div className="session session--editing">
        <input
          autoFocus
          className="session__input"
          value={draft}
          maxLength={60}
          disabled={saving}
          placeholder="起个名字，留空恢复首句"
          onChange={(ev) => setDraft(ev.target.value)}
          onClick={(ev) => ev.stopPropagation()}
          onKeyDown={(ev) => {
            if (ev.key === "Enter") void commit();
            if (ev.key === "Escape") setEditing(false);
          }}
        />
        <span className="session__hint">回车保存 · Esc 取消</span>
      </div>
    );
  }

  return (
    <div
      ref={boxRef}
      role="button"
      tabIndex={0}
      className={`session session--child${active ? " session--active" : ""}`}
      onClick={onSelect}
      onKeyDown={(ev) => {
        if (ev.key === "Enter") onSelect();
      }}
      title={session.alias ? `${session.alias}\n${session.title}` : name}
    >
      <span className="session__title">{name}</span>
      <span className="session__row">
        <span className="session__meta">
          {formatTime(session.updated_at)}
          <em>{session.messages} 条</em>
        </span>
        <button
          className="session__more"
          title="更多"
          onClick={(ev) => {
            ev.stopPropagation();
            setMenu(menu ? null : "root");
          }}
        >
          ⋯
        </button>
      </span>

      {menu && (
        <div className="smenu" onClick={(ev) => ev.stopPropagation()}>
          {menu === "root" ? (
            <>
              <button
                className="smenu__item"
                onClick={() => {
                  setMenu(null);
                  setDraft(session.alias || "");
                  setEditing(true);
                }}
              >
                改名
              </button>
              <button className="smenu__item" onClick={() => setMenu("move")}>
                移动到空间 ›
              </button>
            </>
          ) : (
            <>
              <button className="smenu__back" onClick={() => setMenu("root")}>
                ‹ 返回
              </button>
              {spaces.map((space) => (
                <button
                  key={space.id}
                  className={`smenu__item${space.id === session.space_id ? " is-current" : ""}`}
                  disabled={space.id === session.space_id}
                  onClick={() => {
                    setMenu(null);
                    if (space.id !== session.space_id) void onMove(space.id);
                  }}
                >
                  {space.name}
                  {space.id === session.space_id && <span className="smenu__tag">当前</span>}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** 空间块：标题行（折叠开关 + 名字 + 会话数 + ⋯）+ 展开后的会话列表。 */
function SpaceBlock({
  space,
  sessions,
  open,
  activeId,
  allSpaces,
  onToggle,
  onSelect,
  onRename,
  onMoveSession,
  onUpdate,
  onDelete,
}: {
  space: Space;
  sessions: SessionSummary[];
  open: boolean;
  activeId: string | null;
  allSpaces: Space[];
  onToggle: () => void;
  onSelect: (sessionId: string) => void;
  onRename: (sessionId: string, alias: string) => Promise<void>;
  onMoveSession: (sessionId: string, spaceId: number) => Promise<void>;
  onUpdate: (patch: { name?: string; folder?: string }) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [menu, setMenu] = useState<"root" | "rename" | "folder" | null>(null);
  const [draft, setDraft] = useState("");
  const isDefault = space.id === 1;
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    function onDocClick(ev: MouseEvent) {
      if (!boxRef.current?.contains(ev.target as Node)) setMenu(null);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [menu]);

  async function saveRename() {
    const next = draft.trim();
    setMenu(null);
    if (next && next !== space.name) await onUpdate({ name: next });
  }

  async function saveFolder() {
    const next = draft.trim();
    setMenu(null);
    if (next === space.folder) return;
    // 绑定 = 把这个目录变成该空间所有会话的工作目录：读写文件不再逐次弹审批，
    // 所以必须让用户明确知道后果。解绑（next 为空）不需要确认。
    if (
      next &&
      !window.confirm(
        `把「${space.name}」绑定到 ${next}？\n\n之后这个空间里的会话以它为工作目录：` +
          `相对路径相对它解析、读写它里面的文件不再逐次询问（.env / .git 仍然拒绝）。`,
      )
    ) {
      return;
    }
    await onUpdate({ folder: next });
  }

  return (
    <div className="space" ref={boxRef}>
      <div
        role="button"
        tabIndex={0}
        className={`space__head${open ? " is-open" : ""}`}
        onClick={onToggle}
        onKeyDown={(ev) => {
          if (ev.key === "Enter") onToggle();
        }}
      >
        <span className={`space__caret${open ? " is-open" : ""}`}>
          <IconChevron size={12} />
        </span>
        <span className="space__icon">
          <FolderIcon />
        </span>
        <span className="space__name">{space.name}</span>
        <span className="space__count">{sessions.length}</span>
        <button
          className="space__more"
          title="空间设置"
          onClick={(ev) => {
            ev.stopPropagation();
            setMenu(menu ? null : "root");
          }}
        >
          ⋯
        </button>
      </div>

      {space.folder && (
        <div className="space__folder" title={`该空间里的会话以 ${space.folder} 为工作目录`}>
          <FolderIcon size={11} />
          {shortPath(space.folder)}
        </div>
      )}

      {menu === "root" && (
        <div className="smenu" onClick={(ev) => ev.stopPropagation()}>
          {!isDefault && (
            <button
              className="smenu__item"
              onClick={() => {
                setDraft(space.name);
                setMenu("rename");
              }}
            >
              重命名
            </button>
          )}
          <button
            className="smenu__item"
            onClick={() => {
              setDraft(space.folder);
              setMenu("folder");
            }}
          >
            {space.folder ? "更换绑定目录" : "绑定本地目录"}
          </button>
          {space.folder && (
            <button
              className="smenu__item"
              onClick={() => {
                setMenu(null);
                void onUpdate({ folder: "" });
              }}
            >
              解绑目录
            </button>
          )}
          {!isDefault && (
            <button
              className="smenu__item smenu__item--danger"
              onClick={() => {
                setMenu(null);
                if (window.confirm(`删除空间「${space.name}」？里面的会话会退回默认空间。`)) {
                  void onDelete();
                }
              }}
            >
              删除空间
            </button>
          )}
        </div>
      )}

      {(menu === "rename" || menu === "folder") && (
        <div className="space__edit" onClick={(ev) => ev.stopPropagation()}>
          <input
            autoFocus
            className="session__input"
            value={draft}
            placeholder={
              menu === "rename" ? "空间名，回车保存" : "本地目录绝对路径，如 /Users/you/Desktop/proj"
            }
            onChange={(ev) => setDraft(ev.target.value)}
            onKeyDown={(ev) => {
              if (ev.key === "Enter") void (menu === "rename" ? saveRename() : saveFolder());
              if (ev.key === "Escape") setMenu(null);
            }}
          />
          <span className="session__hint">
            {menu === "rename"
              ? "回车保存 · Esc 取消"
              : "回车绑定 · 该空间的会话以它为工作目录"}
          </span>
        </div>
      )}

      {open && (
        <div className="space__sessions">
          {sessions.length === 0 && (
            <p className="space__empty">
              {space.id === 1 ? "还没有任何会话，点上方「新对话」开始" : "这个空间还没有会话"}
            </p>
          )}
          {sessions.map((session) => (
            <SessionItem
              key={session.session_id}
              session={session}
              active={session.session_id === activeId}
              spaces={allSpaces}
              onSelect={() => onSelect(session.session_id)}
              onRename={(alias) => onRename(session.session_id, alias)}
              onMove={(spaceId) => onMoveSession(session.session_id, spaceId)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function Sidebar({
  sessions,
  spaces,
  activeId,
  activeSpaceId,
  health,
  onSelect,
  onNew,
  onClose,
  onRename,
  onMoveSession,
  onSelectSpace,
  onCreateSpace,
  onUpdateSpace,
  onDeleteSpace,
}: SidebarProps) {
  // 默认全部收起：历史会话不抢视线，点开哪个空间才展开哪个
  const [expanded, setExpanded] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  // 当前是否停在某个控制台页：是的话侧栏对应菜单亮起来，
  // 用户才知道「我现在看的就是这个菜单对应的内容」
  const route = useRoute();
  const onConsole = route.startsWith("/console") || route.startsWith("/extensions");
  const activeConsoleKey = onConsole ? adminPageKey(route) : null;

  /** 按空间分组；后端已经按更新时间倒序，这里保持顺序 */
  const grouped = useMemo(() => {
    const map = new Map<number, SessionSummary[]>();
    for (const space of spaces) map.set(space.id, []);
    for (const session of sessions) {
      const bucket = map.get(session.space_id) ?? map.get(1);
      bucket?.push(session);
    }
    return map;
  }, [sessions, spaces]);

  const activeSpaceName = spaces.find((s) => s.id === activeSpaceId)?.name ?? "默认空间";

  /**
   * 会话变了就把它所在的空间展开——打开页面停在最近会话时，
   * 侧栏必须能看见"我在哪"，否则会话在眼前而侧栏全收着，等于没定位。
   *
   * 只用 ref 记住"已经跟随过的会话"：同一个会话反复渲染不重复展开，
   * 用户手动收起后也不会立刻又被撑开；换成另一个会话才重新跟随。
   */
  const followedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!activeId || followedRef.current === activeId) return;
    const owner = sessions.find((s) => s.session_id === activeId);
    if (!owner) return; // 列表还没刷到这条，等下一轮
    followedRef.current = activeId;
    // 空间被删/数据脏时 space_id 可能指向不存在的空间：退回第一个（默认空间），
    // 否则 setExpanded 一个不存在的 id，界面会表现为"什么都没展开"
    const exists = spaces.some((s) => s.id === owner.space_id);
    const target = exists ? owner.space_id : (spaces[0]?.id ?? 1);
    setExpanded(target);
    onSelectSpace(target);
  }, [activeId, sessions, spaces, onSelectSpace]);

  /**
   * 一个会话都没有时（全新用户 / 刚清空），展开默认空间把空态露出来。
   * 全收着的界面在这里最糟：用户看不出"空间是空的"还是"列表加载失败"。
   */
  const bootRef = useRef(false);
  useEffect(() => {
    if (bootRef.current || spaces.length === 0) return;
    bootRef.current = true;
    if (sessions.length === 0) setExpanded(spaces[0].id);
  }, [spaces, sessions]);

  async function submitNewSpace() {
    const name = newName.trim();
    setCreating(false);
    setNewName("");
    if (!name) return;
    await onCreateSpace(name);
  }

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

      <nav className="sidenav">
        {NAV.map((item) => (
          <Link
            key={item.href}
            className={`sidenav__item${activeConsoleKey === adminPageKey(item.href) ? " is-active" : ""}`}
            to={item.href}
            title={`打开控制台 · ${item.label}`}
          >
            <span className="sidenav__icon" aria-hidden>
              {item.icon}
            </span>
            {item.label}
          </Link>
        ))}
      </nav>

      <div className="sidebar__divider" />

      {/* 新对话放在下半区顶部：它归属「会话」，不在菜单区——
          贴着空间列表，按下去的因果一眼可见（新会话就落在下面这个空间里） */}
      <button className="new-chat new-chat--lower" onClick={onNew} title={`新会话会放进「${activeSpaceName}」`}>
        <IconPlus size={15} />
        新对话
        {activeSpaceId !== 1 && <span className="new-chat__space">{activeSpaceName}</span>}
      </button>

      <div className="spaces__head">
        <span>空间</span>
        <button className="spaces__add" title="新建空间" onClick={() => setCreating(true)}>
          <IconPlus size={13} />
        </button>
      </div>

      <div className="spaces">
        {spaces.map((space) => (
          <SpaceBlock
            key={space.id}
            space={space}
            sessions={grouped.get(space.id) ?? []}
            open={expanded === space.id}
            activeId={activeId}
            allSpaces={spaces}
            onToggle={() => {
              const next = expanded === space.id ? null : space.id;
              setExpanded(next);
              if (next !== null) onSelectSpace(next);
            }}
            onSelect={onSelect}
            onRename={onRename}
            onMoveSession={onMoveSession}
            onUpdate={(patch) => onUpdateSpace(space.id, patch)}
            onDelete={() => onDeleteSpace(space.id)}
          />
        ))}

        {creating && (
          <div className="space__edit">
            <input
              autoFocus
              className="session__input"
              value={newName}
              placeholder="空间名，如「支付项目」"
              onChange={(ev) => setNewName(ev.target.value)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter") void submitNewSpace();
                if (ev.key === "Escape") {
                  setCreating(false);
                  setNewName("");
                }
              }}
            />
            <span className="session__hint">回车创建 · Esc 取消；建完可在 ⋯ 里绑定本地目录</span>
          </div>
        )}
      </div>

      <footer className="sidebar__footer">
        <span className={`dot ${health?.ok ? "dot--ok" : "dot--bad"}`} />
        <span className="sidebar__model" title={health?.model ?? ""}>
          {health?.model ?? "未连接"}
        </span>
      </footer>
    </aside>
  );
}
