import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  answerApproval,
  ask,
  createSpace,
  deleteSpace,
  fetchHealth,
  fetchSessionMessages,
  fetchSessionTodos,
  fetchSessions,
  fetchSpaces,
  moveSession,
  renameSession,
  saveSettings,
  updateSpace,
  uploadFile,
} from "./api";
import { ApprovalBar } from "./components/ApprovalBar";
import { Composer } from "./components/Composer";
import { ConsolePanel } from "./components/AdminConsole";
import { Drawer, type DrawerMode } from "./components/Drawer";
import { MessageList } from "./components/MessageList";
import { Sidebar } from "./components/Sidebar";
import { TodoPanel } from "./components/TodoPanel";
import { Topbar } from "./components/Topbar";
import { historyToMessages, initialState, newId, reducer } from "./state";
import { navigate, useRoute } from "./router";
import type { Block, Health } from "./types";

type ToolBlock = Extract<Block, { kind: "tool" }>;

export default function App() {
  // 自己订阅路由：侧栏点菜单后 App 必须立刻响应（main 区换内容），
  // 靠外部传 prop 的话，外层不重渲染 App 就永远是旧地址
  const route = useRoute();
  const [state, dispatch] = useReducer(reducer, initialState);
  const [health, setHealth] = useState<Health | null>(null);
  const [draft, setDraft] = useState("");
  const [drawer, setDrawer] = useState<DrawerMode>(null);
  const [activeTool, setActiveTool] = useState<ToolBlock | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // 新会话进哪个空间：点开哪个空间就把它设为"当前"，新对话自动归进去
  const [activeSpaceId, setActiveSpaceId] = useState(1);

  // 停止按钮要能中断正在读的流；请求本身也靠它取消，避免后端继续跑
  const abortRef = useRef<AbortController | null>(null);

  const refreshSessions = useCallback(async () => {
    try {
      // 会话和空间一起拉：侧栏按空间分组渲染，缺一边都拼不出树
      const [sessions, spaces] = await Promise.all([fetchSessions(), fetchSpaces()]);
      dispatch({ type: "sessions", sessions });
      dispatch({ type: "spaces", spaces });
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

    // ?usage=1 直接打开用量面板：面板也可以被分享/收藏
    if (new URLSearchParams(window.location.search).get("usage")) setDrawer("usage");

    void refreshSessions();
  }, [refreshSessions]);

  // ?session=xxx 深链。现在有两种进来方式，都得管：
  //   ① 首次加载 / 刷新页面（App 刚挂载）；
  //   ② 在控制台点「回到工作台接着聊」——那是客户端切换，App 一直挂着没重新挂载，
  //      只能靠 route 变化发现"地址带了新的会话号"。
  // 用 ref 记住处理过的会话号，避免同一个地址反复打开。
  const linkedRef = useRef<string | null>(null);
  useEffect(() => {
    if (route.startsWith("/console") || route.startsWith("/extensions")) return;
    const deepLink = new URLSearchParams(window.location.search).get("session");
    if (!deepLink || deepLink === linkedRef.current) return;
    linkedRef.current = deepLink;
    void openSession(deepLink);
  }, [route]);

  /**
   * 打开页面就落到「最后一次沟通的会话」上。
   *
   * 为什么要有这条：URL 里没带会话时（直接开首页、或点了「新对话」后刷新），
   * 以前会停在空白欢迎页——用户得自己在侧栏里翻出昨天聊到哪了。会话本身在库里，
   * 直接接着上次的往下聊才是这个产品的默认姿势。
   *
   * 三个守卫：
   *  - 只跑一次（bootOpened）：发完消息会重拉列表，那时不该把视图抢回去；
   *  - 地址里已经有 ?session= 就交给上面的深链逻辑，别两个一起开；
   *  - 列表为空（全新用户）什么都不做，交给欢迎页。
   */
  const bootOpened = useRef(false);
  useEffect(() => {
    if (bootOpened.current) return;
    if (state.sessions.length === 0) return;
    bootOpened.current = true;
    if (new URLSearchParams(window.location.search).get("session")) return;
    if (state.sessionId) return;
    void openSession(state.sessions[0].session_id); // 列表按最后活跃时间倒序
  }, [state.sessions]);

  function rememberInUrl(sessionId: string | null) {
    // 控制台视图下别动地址：那是控制台自己的路由（/console/xxx），
    // 在这里拼 ?session= 会把用户刷新后的落点带到错的页面上
    if (window.location.pathname.startsWith("/console")) return;
    const url = sessionId ? `?session=${encodeURIComponent(sessionId)}` : window.location.pathname;
    window.history.replaceState(null, "", url);
  }

  async function openSession(sessionId: string) {
    if (state.streaming) return;
    // 在控制台视图里点会话：先把路由带回聊天页再打开——
    // openSession 本身只改 state 不改路由，不切回去的话会话在背后打开了、main 还停在控制台
    if (route.startsWith("/console") || route.startsWith("/extensions")) {
      navigate(`/?session=${encodeURIComponent(sessionId)}`);
      return; // 路由变化会触发下面的深链 effect，由它接手打开
    }
    try {
      const rows = await fetchSessionMessages(sessionId);
      dispatch({ type: "open", sessionId, messages: historyToMessages(rows) });
      rememberInUrl(sessionId);
      setSidebarOpen(false);
      // 打开哪个空间的会话，就把"当前空间"切过去：接着聊的新会话落在同一个空间
      const owner = state.sessions.find((s) => s.session_id === sessionId);
      if (owner) setActiveSpaceId(owner.space_id);
      void restoreTodos(sessionId);
    } catch (err) {
      dispatch({ type: "error", message: `读取会话失败：${String(err)}` });
    }
  }

  function newChat() {
    if (state.streaming) return;
    dispatch({ type: "new-session" });
    // 同 openSession：控制台视图里点「新对话」要先切回聊天页
    if (route.startsWith("/console") || route.startsWith("/extensions")) {
      navigate("/");
      setSidebarOpen(false);
      return;
    }
    rememberInUrl(null);
    setSidebarOpen(false);
  }

  /** 侧栏就地改名：接口成功才更新列表；失败让 SessionItem 保持在编辑态并冒错误。 */
  async function rename(sessionId: string, alias: string) {
    try {
      const result = await renameSession(sessionId, alias);
      dispatch({ type: "session-alias", sessionId, alias: result.alias });
    } catch (err) {
      dispatch({ type: "error", message: `改名失败：${err instanceof Error ? err.message : String(err)}` });
      throw err;
    }
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
        // 只有开新会话时才带空间；老会话的归属不该被当前选中的空间悄悄改掉
        spaceId: state.sessionId ? undefined : activeSpaceId,
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

  async function answer(
    callId: string,
    allow: boolean,
    kind: "once" | "file" | "dir" = "once",
    path = "",
  ) {
    // 先收卡片再发请求：按钮点两次不该弹两次，也不该让人以为没点上
    dispatch({ type: "approval", callId });
    try {
      await answerApproval(callId, allow, kind, path);
    } catch (err) {
      dispatch({ type: "error", message: `审批回传失败：${String(err)}` });
    }
  }

  // ---- 空间：改动后统一重拉（会话数、分组都要跟着变） ----
  async function handleCreateSpace(name: string) {
    try {
      await createSpace(name);
      await refreshSessions();
    } catch (err) {
      dispatch({ type: "error", message: `新建空间失败：${err instanceof Error ? err.message : String(err)}` });
    }
  }

  async function handleUpdateSpace(spaceId: number, patch: { name?: string; folder?: string }) {
    try {
      await updateSpace(spaceId, patch);
      await refreshSessions();
    } catch (err) {
      dispatch({ type: "error", message: `更新空间失败：${err instanceof Error ? err.message : String(err)}` });
    }
  }

  async function handleDeleteSpace(spaceId: number) {
    try {
      await deleteSpace(spaceId);
      if (activeSpaceId === spaceId) setActiveSpaceId(1);
      await refreshSessions();
    } catch (err) {
      dispatch({ type: "error", message: `删除空间失败：${err instanceof Error ? err.message : String(err)}` });
    }
  }

  async function handleMoveSession(sessionId: string, spaceId: number) {
    try {
      await moveSession(sessionId, spaceId);
      await refreshSessions();
    } catch (err) {
      dispatch({ type: "error", message: `移动会话失败：${err instanceof Error ? err.message : String(err)}` });
    }
  }

  async function onSaveSettings(input: { model: string; workspace: string }) {
    const next = await saveSettings(input);
    setHealth(next);
  }

  // 上传成功后把绝对路径回填进输入框：文件存在数据家目录（~/.ohopilot/uploads），
  // 不在 workspace 内，相对路径会解析错，必须用绝对路径；模型看到路径就会用 fs_read_document 去读。
  // 不自动发送——用户通常还想先说清楚要拿这份文件干什么。
  async function handleUpload(file: File) {
    try {
      const path = await uploadFile(file);
      setDraft((prev) => `${prev ? prev + "\n" : ""}（附件已上传到数据目录：${path}）`);
    } catch (err) {
      dispatch({ type: "error", message: `上传失败：${err instanceof Error ? err.message : String(err)}` });
    }
  }

  // 标题取第一句用户提问，和左侧会话列表的规则一致（后端也是这么取 title 的）
  const firstUserBlock = state.messages.find((m) => m.role === "user")?.blocks[0];
  const rawTitle = firstUserBlock?.kind === "text" ? firstUserBlock.text : "新对话";

  // 侧栏菜单点的是控制台页：main 区直接换成那页的内容，侧栏原地不动——
  // 「跳回控制台全屏页」的旧模式已退役，聊天状态（流式输出、输入框）都保在 state 里不丢
  const onConsole = route.startsWith("/console") || route.startsWith("/extensions");

  return (
    <div className={`app${sidebarOpen ? " app--sidebar-open" : ""}`}>
      <Sidebar
        sessions={state.sessions}
        spaces={state.spaces}
        activeId={state.sessionId}
        activeSpaceId={activeSpaceId}
        health={health}
        onSelect={openSession}
        onNew={newChat}
        onClose={() => setSidebarOpen(false)}
        onRename={rename}
        onMoveSession={handleMoveSession}
        onSelectSpace={setActiveSpaceId}
        onCreateSpace={handleCreateSpace}
        onUpdateSpace={handleUpdateSpace}
        onDeleteSpace={handleDeleteSpace}
      />

      <div className="main">
        {onConsole ? (
          <ConsolePanel />
        ) : (
          <>
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
                onUpload={handleUpload}
              />

              {(state.totalTokens > 0 || health) && (
                <div className="meter">
                  {state.lastTurnTokens > 0 && <span>本轮 {state.lastTurnTokens} tokens</span>}
                  {state.totalTokens > 0 && <span>本次累计 {state.totalTokens}</span>}
                  {health && <span>{health.workspace}</span>}
                </div>
              )}
            </div>
          </>
        )}
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
