import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  answerApproval,
  ask,
  fetchHealth,
  fetchSessionMessages,
  fetchSessionTodos,
  fetchSessions,
  saveSettings,
} from "./api";
import { ApprovalBar } from "./components/ApprovalBar";
import { Composer } from "./components/Composer";
import { Drawer, type DrawerMode } from "./components/Drawer";
import { MessageList } from "./components/MessageList";
import { Sidebar } from "./components/Sidebar";
import { TodoPanel } from "./components/TodoPanel";
import { Topbar } from "./components/Topbar";
import { historyToMessages, initialState, newId, reducer } from "./state";
import type { Block, Health } from "./types";

type ToolBlock = Extract<Block, { kind: "tool" }>;

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [health, setHealth] = useState<Health | null>(null);
  const [draft, setDraft] = useState("");
  const [drawer, setDrawer] = useState<DrawerMode>(null);
  const [activeTool, setActiveTool] = useState<ToolBlock | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // 停止按钮要能中断正在读的流；请求本身也靠它取消，避免后端继续跑
  const abortRef = useRef<AbortController | null>(null);

  const refreshSessions = useCallback(async () => {
    try {
      dispatch({ type: "sessions", sessions: await fetchSessions() });
    } catch {
      // 列表拉不到不影响正在进行的对话，静默即可
    }
  }, []);

  // 清单是锦上添花的信息：拉不到就静默不显示，别为它报错打扰对话
  const restoreTodos = useCallback(async (sessionId: string) => {
    try {
      dispatch({ type: "todos", items: await fetchSessionTodos(sessionId) });
    } catch {
      /* 面板缺席不影响对话 */
    }
  }, []);

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch((err: unknown) =>
        dispatch({ type: "error", message: `连不上服务端：${String(err)}` }),
      );

    // ?session=xxx 深链：刷新页面不掉上下文，链接也能直接发给别人（本机范围内）
    const params = new URLSearchParams(window.location.search);
    const deepLink = params.get("session");
    if (deepLink) {
      fetchSessionMessages(deepLink)
        .then((rows) =>
          dispatch({ type: "open", sessionId: deepLink, messages: historyToMessages(rows) }),
        )
        .catch(() => undefined);
      void restoreTodos(deepLink);
    }
    // ?usage=1 直接打开用量面板：面板也可以被分享/收藏
    if (params.get("usage")) setDrawer("usage");

    void refreshSessions();
  }, [refreshSessions, restoreTodos]);

  function rememberInUrl(sessionId: string | null) {
    const url = sessionId ? `?session=${encodeURIComponent(sessionId)}` : window.location.pathname;
    window.history.replaceState(null, "", url);
  }

  async function openSession(sessionId: string) {
    if (state.streaming) return;
    try {
      const rows = await fetchSessionMessages(sessionId);
      dispatch({ type: "open", sessionId, messages: historyToMessages(rows) });
      rememberInUrl(sessionId);
      setSidebarOpen(false);
      void restoreTodos(sessionId);
    } catch (err) {
      dispatch({ type: "error", message: `读取会话失败：${String(err)}` });
    }
  }

  function newChat() {
    if (state.streaming) return;
    dispatch({ type: "new-session" });
    rememberInUrl(null);
    setSidebarOpen(false);
  }

  async function send(text?: string) {
    const question = (text ?? draft).trim();
    if (!question || state.streaming) return;

    const assistantId = newId();
    setDraft("");
    dispatch({ type: "send", text: question, assistantId });

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await ask({
        message: question,
        sessionId: state.sessionId,
        signal: controller.signal,
        // 新会话的 id 只出现在响应头，必须马上记下来，否则下一轮又开新会话
        onSession: (sessionId) => {
          dispatch({ type: "session-id", sessionId });
          rememberInUrl(sessionId);
        },
        onEvent: (event) => dispatch({ type: "event", event, assistantId }),
      });
    } catch (err) {
      const aborted = err instanceof DOMException && err.name === "AbortError";
      dispatch({
        type: "event",
        assistantId,
        event: {
          type: aborted ? "notice" : "error",
          message: aborted ? "已停止本轮回答" : err instanceof Error ? err.message : String(err),
        },
      });
    } finally {
      dispatch({ type: "stream-end" });
      abortRef.current = null;
      void refreshSessions();
    }
  }

  async function answer(callId: string, allow: boolean) {
    // 先收卡片再发请求：按钮点两次不该弹两次，也不该让人以为没点上
    dispatch({ type: "approval", callId });
    try {
      await answerApproval(callId, allow);
    } catch (err) {
      dispatch({ type: "error", message: `审批回传失败：${String(err)}` });
    }
  }

  async function onSaveSettings(input: { model: string; workspace: string }) {
    const next = await saveSettings(input);
    setHealth(next);
  }

  // 标题取第一句用户提问，和左侧会话列表的规则一致（后端也是这么取 title 的）
  const firstUserBlock = state.messages.find((m) => m.role === "user")?.blocks[0];
  const rawTitle = firstUserBlock?.kind === "text" ? firstUserBlock.text : "新对话";

  return (
    <div className={`app${sidebarOpen ? " app--sidebar-open" : ""}`}>
      <Sidebar
        sessions={state.sessions}
        activeId={state.sessionId}
        health={health}
        onSelect={openSession}
        onNew={newChat}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="main">
        <Topbar
          health={health}
          title={rawTitle.length > 40 ? `${rawTitle.slice(0, 40)}…` : rawTitle}
          onOpenSettings={() => setDrawer("settings")}
          onOpenUsage={() => setDrawer("usage")}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
        />

        <main className="content">
          <TodoPanel items={state.todos} />
          <MessageList
            messages={state.messages}
            onOpenTool={(block) => {
              setActiveTool(block);
              setDrawer("tool");
            }}
            onSuggest={(text) => setDraft(text)}
          />
        </main>

        <div className="footer">
          {state.error && (
            <div className="banner banner--error">
              <span>{state.error}</span>
              <button className="icon-btn" onClick={() => dispatch({ type: "clear-error" })}>
                ×
              </button>
            </div>
          )}

          <ApprovalBar pending={state.pending} onAnswer={answer} />

          <Composer
            value={draft}
            streaming={state.streaming}
            disabled={false}
            onChange={setDraft}
            onSend={() => void send()}
            onStop={() => abortRef.current?.abort()}
          />

          {(state.totalTokens > 0 || health) && (
            <div className="meter">
              {state.lastTurnTokens > 0 && <span>本轮 {state.lastTurnTokens} tokens</span>}
              {state.totalTokens > 0 && <span>本次累计 {state.totalTokens}</span>}
              {health && <span>{health.workspace}</span>}
            </div>
          )}
        </div>
      </div>

      {sidebarOpen && <div className="scrim" onClick={() => setSidebarOpen(false)} />}

      <Drawer
        mode={drawer}
        tool={activeTool}
        health={health}
        sessionId={state.sessionId}
        onClose={() => setDrawer(null)}
        onSaveSettings={onSaveSettings}
      />
    </div>
  );
}
