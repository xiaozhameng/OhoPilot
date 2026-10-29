"""装配层：把配置、工具、Agent、会话库拼成一个能直接用的 Runtime。

这一步之前，这些装配都住在 cli.py 里——只有一个入口时那是合理的。
第 15 步要加 HTTP 服务端，装配逻辑被需要第二次，才抽到这里。

边界很清楚：
  - Runtime 负责「一次问答怎么编排」（压缩摘要、跑 agent、双写存储）；
  - 入口（cli / server）只负责「怎么显示」（终端 print 或 SSE 推送）与「怎么问人」
    （终端 y/n 或 HTTP 弹窗，通过 approver 传进来）。

所以 Runtime 里不该出现任何 print / input / HTTP 相关的东西。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from ohopilot.config import Config
from ohopilot.core import events as ev
from ohopilot.core.agent import Agent, Approver
from ohopilot.memory.store import SessionStore
from ohopilot.memory.summarize import maybe_summarize
from ohopilot.memory.todo import TodoStore
from ohopilot.tools.base import ToolRegistry
from ohopilot.tools.docs import build_docs_tools
from ohopilot.tools.fs import build_fs_tools
from ohopilot.tools.kb import build_kb_tools
from ohopilot.tools.memory import MemoryAppendTool
from ohopilot.tools.search import build_search_tools
from ohopilot.tools.shell import ShellTool
from ohopilot.tools.skills import build_skill_tools
from ohopilot.tools.todo import build_todo_tools
from ohopilot.tools.web import build_web_tools
from ohopilot.workspace import use_workspace


@dataclass
class Runtime:
    """一套装配好的运行环境：一个进程里通常只有一个。"""

    cfg: Config
    registry: ToolRegistry
    agent: Agent
    store: SessionStore
    todos: TodoStore
    #: MCP 外部服务器连接表（名字 → client）。setup_mcp() 之后才有内容
    mcp_clients: dict = field(default_factory=dict)

    # ---------- 装配 ----------

    @classmethod
    def create(
        cls,
        approver: Approver | None = None,
        cfg: Config | None = None,
    ) -> "Runtime":
        """读配置 → 建目录 → 注册工具 → 建 Agent → 打开会话库。

        approver 由入口传入：终端是 y/n 回调，服务端是 HTTP 弹窗回调。
        不传就是「没人能批准」，Agent 那边会把所有危险操作默认拒绝。

        cfg 可以外部传（服务端要先读配置才能决定审批超时等参数），
        不传就自己读——CLI 走这条。
        """
        cfg = cfg or Config.load()
        cfg.ensure_dirs()
        todos = TodoStore(cfg.db_path)
        registry = ToolRegistry(
            [
                *build_fs_tools(cfg),
                *build_docs_tools(cfg),
                *build_kb_tools(cfg),
                *build_web_tools(),
                *build_search_tools(cfg),
                *build_skill_tools(cfg),
                *build_todo_tools(todos),
                MemoryAppendTool(cfg),
                ShellTool(cfg),
            ]
        )
        agent = Agent(cfg, registry, approver=approver)
        store = SessionStore(cfg.db_path)
        return cls(cfg=cfg, registry=registry, agent=agent, store=store, todos=todos)

    # ---------- 会话 ----------

    def new_session(self) -> str:
        return self.store.create_session()

    def latest_session(self) -> str | None:
        return self.store.latest_session()

    def load_history(self, session_id: str) -> list[dict[str, str]]:
        return self.store.load_history(session_id)

    # ---------- 工作目录 ----------

    def session_workspace(self, session_id: str) -> tuple[Path | None, str]:
        """这个会话该用哪个工作目录：返回 (目录, 要告诉用户的提示)。

        目录为 None 表示"不切，用进程默认值"（空间没绑目录时的常态）。
        绑定目录不存在时也返回 None + 一句提示：降级继续，而不是让这轮对话
        直接失败——目录被改名、外接盘没挂上，都是日常会发生的事。
        """
        raw = self.store.session_folder(session_id)
        if not raw:
            return None, ""
        path = Path(raw).expanduser()
        if not path.is_dir():
            return None, f"空间绑定的目录不存在（{path}），这一轮改用默认工作目录"
        return path.resolve(), ""

    def close(self) -> None:
        self.store.close()
        self.todos.close()

    # ---------- MCP 外部工具 ----------

    async def setup_mcp(self) -> None:
        """连接 ~/.ohopilot/mcp.json 里配置的外部服务器，把工具注册进 registry。

        必须在事件循环里调用（服务端 lifespan / CLI 的 async main）——
        MCP 子进程绑定在创建它的循环上，之后所有调用都得在同一个循环里。
        没有配置文件 = 什么都不发生，零成本。
        """
        from ohopilot.mcp.bridge import connect_all
        from ohopilot.mcp.client import load_mcp_config

        specs = load_mcp_config(self.cfg.home / "mcp.json")
        if not specs:
            return
        self.mcp_clients, tools = await connect_all(specs)
        for tool in tools:
            self.registry.register(tool)

    async def shutdown_mcp(self) -> None:
        for client in self.mcp_clients.values():
            await client.stop()
        self.mcp_clients.clear()

    # ---------- 一轮问答 ----------

    async def ask(
        self,
        session_id: str,
        question: str,
        history: list[dict[str, Any]],
    ) -> AsyncIterator[ev.Event]:
        """跑一轮，把事件原样吐给调用方渲染；收尾时双写存储。

        history 会被原地修改（追加这一问一答），所以调用方传进来的 list
        跑完之后就是最新的上下文——这是刻意的：入口不需要知道怎么维护它。
        """
        # 未压缩的消息太多了，先压一批早期对话，再把 history 换成「摘要 + 最近几条」。
        # 用切片赋值，保证调用方手里的还是同一个 list 对象。
        if await maybe_summarize(self.cfg, self.store, session_id):
            history[:] = self.store.load_history(session_id)
            yield ev.notice("早期对话已压缩成摘要，上下文已瘦身")

        # 清单按会话隔离，而工具是全局单例——每轮开始先把会话号绑给 store。
        self.todos.set_session(session_id)

        # 工作目录按会话切（第 39 步）：会话所属空间绑了本地目录，这一轮的文件
        # 读写、shell 的 cwd、系统提示里告诉模型的路径，全部以它为边界。
        # 用 ContextVar 而不是直接改 cfg.workspace——服务端的请求是并发的，
        # 改共享的 cfg 会让两个会话互相踩。
        folder, note = self.session_workspace(session_id)
        if note:
            yield ev.notice(note)

        with use_workspace(folder):
            answer: list[str] = []

            async for event in self.agent.run(question, history):
                if event.type == "text":
                    answer.append(event.data["delta"])
                elif event.type == "tool_result":
                    # 每次工具执行都记一笔流水（第 26 步控制台的「工具调用排行」数据源）。
                    # 耗时由 agent 掐表带在事件里；错误样本从结果原文截 200 字——
                    # 控制台排障时看的就是这一段，不是光秃秃的「失败了」
                    is_err = bool(event.data.get("is_error", False))
                    self.store.record_tool_call(
                        session_id,
                        event.data.get("name", ""),
                        is_err,
                        duration_ms=event.data.get("duration_ms"),
                        error_excerpt=event.data.get("content", "")[:200] if is_err else None,
                        round_no=event.data.get("round_no", 0),
                    )
                    if event.data.get("name", "").startswith("todo_"):
                        # 清单工具刚改完库，追加一条全量快照事件给界面。
                        # 拦截在这里而不是让工具自己发事件：工具只管干活，
                        # 「怎么通知界面」是 Runtime（编排层）的事——和 usage 落库同一个套路。
                        yield ev.todo(self.todos.list_items())
                elif event.type == "usage":
                    # agent 不知道会话号，落库只能在这里做。存一份"每次模型调用"
                    # 的流水，开发者面板的图表全靠它；agent 主循环保持零存储依赖。
                    self.store.record_llm_call(
                        session_id,
                        round_no=event.data.get("round_no", 0),
                        model=self.cfg.model,
                        prompt_tokens=event.data.get("prompt_tokens", 0),
                        cache_hit_tokens=event.data.get("cache_hit_tokens", 0),
                        cache_miss_tokens=event.data.get("cache_miss_tokens", 0),
                        completion_tokens=event.data.get("completion_tokens", 0),
                        total_tokens=event.data.get("total_tokens", 0),
                        duration_ms=event.data.get("duration_ms", 0),
                        first_token_ms=event.data.get("first_token_ms", 0),
                        context=event.data.get("context"),
                        finish_reason=event.data.get("finish_reason", ""),
                        reasoning_tokens=event.data.get("reasoning_tokens", 0),
                        req_messages=event.data.get("req_messages", 0),
                        req_tools=event.data.get("req_tools", 0),
                        req_snapshot=event.data.get("req_snapshot"),
                    )
                    # 请求体快照只落库，不进 SSE：对话内容不该广播到界面事件流，
                    # 控制台想看时按 call id 单独拉（GET /console/llm-calls/{id}）
                    event.data.pop("req_snapshot", None)
                elif event.type == "done":
                    # agent 不知道自己在哪个会话里，done 统一由 Runtime 发
                    continue
                yield event

        text = "".join(answer)

        # 双写：内存上下文（进 prompt）+ SQLite（进硬盘）。
        # 助手回复为空（比如模型直接报错）就不写，避免库里出现空 assistant 消息，
        # 那种消息回放时会被模型 API 当成非法输入。
        user_msg = {"role": "user", "content": question}
        history.append(user_msg)
        self.store.append(session_id, "user", question)

        if text:
            history.append({"role": "assistant", "content": text})
            self.store.append(session_id, "assistant", text)

        yield ev.done(session_id)
