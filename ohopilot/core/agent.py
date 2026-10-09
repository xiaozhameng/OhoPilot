"""Agent 内核：一个循环，四个动作。

while True:
    1. 把当前上下文发给模型（流式，边收边吐）
    2. 模型要么回文本（本轮结束），要么要调工具
    3. 危险工具先过人工确认
    4. 执行工具、结果回填上下文，回到第 1 步

界面怎么换都不影响这里——所有输出都是 events.Event。

持久化策略：只存 user 和最终 assistant 文本。中间的 tool_calls / tool 结果
只在本次运行的上下文里存在，避免下次会话出现"孤儿 tool_call"导致接口报错。
"""

from __future__ import annotations

import platform as _platform
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator

from ..config import Config
from ..memory import MemoryStore
from ..skills import SkillLoader
from ..tools import ToolRegistry
from ..tools.shell import ShellTool
from . import events as ev
from .llm import AssistantTurn, LLMError, stream_chat

#: 单轮最多让模型调多少次工具，防止死循环烧钱
MAX_TOOL_ROUNDS = 12

SYSTEM_PROMPT = """你是 OhoPilot（中文名「哦豁」），一个跑在用户自己电脑上的中文办公 agent。

你的工作方式：
- 先想清楚要做什么，再动手。需要了解现状就先看，别凭空猜。
- 需要读文件、跑命令、查网页时，直接调用工具，不要只说"你可以这样做"。
- 工具报错时读懂错误再调整，别用同样的参数反复重试。
- 遇到危险或不可逆的操作，先向用户说明后果。

你的表达方式：
- 默认用简体中文回答，语气自然、直接，不说客套话。
- 汇报结果时给要点，别把工具的原始输出原封不动贴给用户。
- 不确定的事就说不知道，不要编。

当前工作目录：{workspace}
运行平台：{platform}
"""

#: 审批回调签名：(工具名, 参数, 原因) -> 是否放行
Approver = Callable[[str, dict[str, Any], str], Awaitable[bool]]


async def auto_approve(name: str, arguments: dict[str, Any], reason: str) -> bool:
    """默认放行；CLI / 服务端会传入真正的交互式审批。"""
    return True


class Agent:
    def __init__(
        self,
        cfg: Config,
        registry: ToolRegistry,
        store: MemoryStore,
        skills: SkillLoader,
        approver: Approver | None = None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry
        self.store = store
        self.skills = skills
        self.approver = approver or auto_approve

    # ---------- 系统提示词 ----------

    def _system_prompt(self) -> dict[str, str]:
        base = SYSTEM_PROMPT.format(
            workspace=self.cfg.workspace,
            platform=f"{_platform.system()} {_platform.machine()}",
        )
        long_term = self.store.read_long_term()
        if long_term:
            base += f"\n## 长期记忆\n{long_term}\n"
        skill_index = self.skills.index_prompt()
        if skill_index:
            base += f"\n{skill_index}\n"
        return {"role": "system", "content": base}

    def _build_messages(self, session_id: str) -> list[dict[str, Any]]:
        return [self._system_prompt(), *self.store.history(session_id)]

    # ---------- 上下文压缩 ----------

    async def _maybe_compact(self, session_id: str) -> str | None:
        """历史太长时，让模型把早期对话压成一段摘要（省 token，保住关键信息）。"""
        if not self.store.needs_compaction(session_id):
            return None

        history = self.store.history(session_id)
        transcript: list[str] = []
        for message in history[:-20]:
            role = message.get("role", "?")
            content = message.get("content") or ""
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
            if content:
                transcript.append(f"{role}: {str(content)[:600]}")
        if not transcript:
            return None

        prompt = (
            "把下面这段对话压缩成不超过 400 字的中文摘要，保留：任务目标、已完成的动作、"
            "关键结论、未解决的问题、用户偏好。直接输出摘要正文，不要加评论。\n\n"
            + "\n".join(transcript)
        )
        chunks: list[str] = []
        try:
            async for kind, payload in stream_chat(self.cfg, [{"role": "user", "content": prompt}]):
                if kind == "text":
                    chunks.append(payload)
        except LLMError:
            return None

        summary = "".join(chunks).strip()
        if not summary:
            return None
        self.store.compact(session_id, summary)
        return summary

    # ---------- 主循环 ----------

    async def run(self, session_id: str, user_text: str) -> AsyncIterator[ev.Event]:
        self.store.append(session_id, {"role": "user", "content": user_text})
        await self._maybe_compact(session_id)
        messages = self._build_messages(session_id)

        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for _ in range(MAX_TOOL_ROUNDS):
            holder: dict[str, AssistantTurn] = {}
            try:
                async for event in self._stream_round(messages, holder):
                    yield event
            except LLMError as exc:
                yield ev.error(str(exc))
                yield ev.done(session_id)
                return

            turn = holder["turn"]
            usage["prompt_tokens"] += turn.prompt_tokens
            usage["completion_tokens"] += turn.completion_tokens
            usage["total_tokens"] += turn.total_tokens

            if not turn.tool_calls:
                if turn.text:
                    self.store.append(session_id, {"role": "assistant", "content": turn.text})
                self.store.touch_session(session_id, title=user_text)
                if usage["total_tokens"]:
                    yield ev.usage(**usage)
                yield ev.done(session_id)
                return

            # 有工具调用：assistant 消息（含 tool_calls）先入本次上下文
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.text or None,
                    "tool_calls": [call.to_message_part() for call in turn.tool_calls],
                }
            )

            for call in turn.tool_calls:
                async for event in self._handle_tool_call(call, messages):
                    yield event

        # 工具轮次用尽
        warning = f"已达到单轮工具调用上限（{MAX_TOOL_ROUNDS} 次），先停在这里。你可以让我继续。"
        yield ev.text(warning)
        self.store.append(session_id, {"role": "assistant", "content": warning})
        self.store.touch_session(session_id, title=user_text)
        if usage["total_tokens"]:
            yield ev.usage(**usage)
        yield ev.done(session_id)

    async def _stream_round(
        self, messages: list[dict[str, Any]], holder: dict[str, AssistantTurn]
    ) -> AsyncIterator[ev.Event]:
        """跑一次模型调用：文本增量实时 yield，最终 turn 放进 holder。"""
        async for kind, payload in stream_chat(
            self.cfg, messages, tools=self.registry.schemas()
        ):
            if kind == "text":
                yield ev.text(payload)
            else:
                holder["turn"] = payload

    async def _handle_tool_call(
        self, call: Any, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ev.Event]:
        tool = self.registry.get(call.name)

        if tool is None:
            content = f"没有名为 {call.name} 的工具。可用工具：{self.registry.names()}"
            yield ev.tool_result(call.id, call.name, content, is_error=True)
            messages.append(self._tool_message(call, content))
            return

        yield ev.tool_call(call.id, call.name, call.arguments)

        blocked, reason = tool.needs_approval(call.arguments)

        if blocked and call.name == "shell.run" and ShellTool.is_denied(reason):
            label = reason.replace("__DENY__", "")
            content = f"已拒绝执行：{label}。该命令命中禁止清单，请改用安全的做法。"
            yield ev.tool_result(call.id, call.name, content, is_error=True)
            messages.append(self._tool_message(call, content))
            return

        if blocked:
            yield ev.approval_request(call.id, call.name, call.arguments, reason)
            if not await self.approver(call.name, call.arguments, reason):
                content = "用户拒绝了这个操作。请换一种方式，或先向用户说明并询问。"
                yield ev.tool_result(call.id, call.name, content, is_error=True)
                messages.append(self._tool_message(call, content))
                return

        result = await tool.safe_run(call.arguments)
        yield ev.tool_result(call.id, call.name, result.content, result.is_error)
        messages.append(self._tool_message(call, result.content))

    @staticmethod
    def _tool_message(call: Any, content: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": content,
        }


async def run_once(agent: Agent, session_id: str, user_text: str) -> tuple[str, list[ev.Event]]:
    """跑完一轮，返回完整文本 + 事件列表（给非流式接口用）。"""
    chunks: list[str] = []
    collected: list[ev.Event] = []
    async for event in agent.run(session_id, user_text):
        collected.append(event)
        if event.type == "text":
            chunks.append(event.data.get("delta", ""))
    return "".join(chunks), collected


__all__ = ["Agent", "Approver", "MAX_TOOL_ROUNDS", "auto_approve", "run_once"]
