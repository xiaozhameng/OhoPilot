"""主循环：模型 ⇄ 工具，直到模型不再要工具。

这是整个软件的心脏，但它依赖模型返回什么，所以测法是把 stream_chat 换掉，
按剧本喂「一轮文本」「一轮工具调用」……，再看吐出来的事件对不对。
测的是编排逻辑，不是模型质量。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ohopilot.core import agent as agent_mod
from ohopilot.core.agent import Agent, _system_prompt
from ohopilot.core.llm import AssistantTurn, LLMError, ToolCall
from ohopilot.tools.base import Tool, ToolRegistry, ToolResult


class EchoTool(Tool):
    name = "echo"
    description = "把参数原样返回"

    def __init__(self) -> None:
        self.called: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> ToolResult:
        self.called.append(kwargs)
        return ToolResult.ok(f"echo:{kwargs.get('text')}")


class DenyAllTool(Tool):
    name = "danger"
    description = "每次都要审批"
    requires_approval = True

    def __init__(self) -> None:
        self.called = 0

    async def run(self, **kwargs: Any) -> ToolResult:
        self.called += 1
        return ToolResult.ok("执行了")


def scripted(monkeypatch, turns: list[tuple[str, list[ToolCall]]]):
    """把模型换成按剧本走的假流。剧本每项是 (文本, 工具调用列表)。"""
    seen: list[list[dict[str, Any]]] = []
    queue = list(turns)

    async def fake_stream(cfg, messages, tools=None):
        seen.append([dict(m) for m in messages])
        text, calls = queue.pop(0)
        if text:
            yield "text", text
        yield "turn", AssistantTurn(
            text=text, tool_calls=calls, prompt_tokens=1, completion_tokens=2, total_tokens=3
        )

    monkeypatch.setattr(agent_mod, "stream_chat", fake_stream)
    return seen


async def _collect(agent: Agent, question: str = "问题", history=None) -> list:
    return [e async for e in agent.run(question, history)]


# ---------- 纯文本 ----------

async def test_一轮文本回答的事件序列(cfg, monkeypatch):
    scripted(monkeypatch, [("你好", [])])
    agent = Agent(cfg, ToolRegistry())

    events = await _collect(agent)
    kinds = [e.type for e in events]

    assert kinds == ["text", "usage", "done"]
    assert events[0].data["delta"] == "你好"
    assert events[1].data["total_tokens"] == 3


async def test_用户问题被拼到消息末尾(cfg, monkeypatch):
    seen = scripted(monkeypatch, [("答", [])])
    agent = Agent(cfg, ToolRegistry())

    await _collect(agent, "今天几号")

    messages = seen[0]
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "今天几号"}


async def test_历史消息被原样带上(cfg, monkeypatch):
    seen = scripted(monkeypatch, [("答", [])])
    agent = Agent(cfg, ToolRegistry())
    history = [
        {"role": "user", "content": "早先的问题"},
        {"role": "assistant", "content": "早先的回答"},
    ]

    await _collect(agent, "接着问", history)

    assert [m["content"] for m in seen[0][1:3]] == ["早先的问题", "早先的回答"]


# ---------- 工具往返 ----------

async def test_工具调用被真实执行并回填结果(cfg, monkeypatch):
    call = ToolCall(id="c1", name="echo", arguments={"text": "hi"}, raw_arguments='{"text":"hi"}')
    scripted(monkeypatch, [("", [call]), ("收到", [])])
    tool = EchoTool()
    agent = Agent(cfg, ToolRegistry([tool]))

    events = await _collect(agent)
    kinds = [e.type for e in events]

    assert kinds == [
        "tool_call", "tool_result",       # 第一轮：调用并执行
        "usage",                           # 第一轮用完的 token
        "text", "usage", "done",          # 第二轮：模型收尾
    ]
    assert tool.called == [{"text": "hi"}]
    result = next(e for e in events if e.type == "tool_result")
    assert result.data["content"] == "echo:hi"
    assert result.data["is_error"] is False


async def test_工具结果会以_tool_消息回填进上下文(cfg, monkeypatch):
    call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})
    seen = scripted(monkeypatch, [("", [call]), ("收到", [])])
    agent = Agent(cfg, ToolRegistry([EchoTool()]))

    await _collect(agent)

    second_round = seen[1]
    tool_msgs = [m for m in second_round if m["role"] == "tool"]
    assert tool_msgs == [{"role": "tool", "tool_call_id": "c1", "content": "echo:hi"}]
    # assistant 那条要带上 tool_calls，否则 API 会因为孤儿 tool 消息报错
    assistant = [m for m in second_round if m["role"] == "assistant"][-1]
    assert assistant["tool_calls"][0]["id"] == "c1"


async def test_未知工具返回失败而不是崩掉(cfg, monkeypatch):
    call = ToolCall(id="c1", name="不存在的工具", arguments={})
    scripted(monkeypatch, [("", [call]), ("好的", [])])
    agent = Agent(cfg, ToolRegistry())

    events = await _collect(agent)
    result = next(e for e in events if e.type == "tool_result")

    assert result.data["is_error"] is True
    assert "未知工具" in result.data["content"]


async def test_工具内部异常被兜住(cfg, monkeypatch):
    class BoomTool(Tool):
        name = "boom"

        async def run(self, **kwargs):
            raise RuntimeError("炸了")

    call = ToolCall(id="c1", name="boom", arguments={})
    scripted(monkeypatch, [("", [call]), ("好的", [])])
    agent = Agent(cfg, ToolRegistry([BoomTool()]))

    events = await _collect(agent)
    result = next(e for e in events if e.type == "tool_result")

    assert result.data["is_error"] is True
    assert "RuntimeError" in result.data["content"]


# ---------- 审批 ----------

async def test_没有审批员时危险操作默认拒绝(cfg, monkeypatch):
    """不传 approver = 没人能批准 = 一律拒绝。宁可不做，不能乱做。"""
    call = ToolCall(id="c1", name="danger", arguments={})
    scripted(monkeypatch, [("", [call]), ("好的", [])])
    tool = DenyAllTool()
    agent = Agent(cfg, ToolRegistry([tool]))  # approver=None

    events = await _collect(agent)

    assert tool.called == 0
    assert any(e.type == "approval_request" for e in events)
    result = next(e for e in events if e.type == "tool_result")
    assert result.data["is_error"] is True
    assert "拒绝" in result.data["content"]


async def test_审批通过才真的执行(cfg, monkeypatch):
    call = ToolCall(id="c1", name="danger", arguments={})
    scripted(monkeypatch, [("", [call]), ("好的", [])])
    tool = DenyAllTool()
    asked: list[tuple] = []

    async def approver(call_id, name, arguments, reason):
        asked.append((call_id, name, reason))
        return True

    agent = Agent(cfg, ToolRegistry([tool]), approver=approver)
    await _collect(agent)

    assert tool.called == 1
    # call_id 必须传下去：HTTP 客户端靠它对应「我在回答哪一次调用」
    assert asked == [("c1", "danger", "该操作可能修改你的电脑")]


# ---------- 各种收尾 ----------

async def test_模型报错转成_error_事件(cfg, monkeypatch):
    async def boom(cfg, messages, tools=None):
        raise LLMError("网络断了")
        yield  # pragma: no cover  —— 让它成为异步生成器

    monkeypatch.setattr(agent_mod, "stream_chat", boom)
    events = await _collect(Agent(cfg, ToolRegistry()))

    assert [e.type for e in events] == ["error"]
    assert "网络断了" in events[0].data["message"]


async def test_轮次用尽强制停止(cfg, monkeypatch):
    """模型一直要工具 = 死循环烧钱，必须有上限。"""
    cfg.max_tool_rounds = 2
    call = ToolCall(id="c", name="echo", arguments={"text": "x"})
    scripted(monkeypatch, [("", [call]), ("", [call]), ("", [call])])
    agent = Agent(cfg, ToolRegistry([EchoTool()]))

    events = await _collect(agent)

    assert events[-1].type == "error"
    assert "轮次上限" in events[-1].data["message"]


async def test_所有事件都能序列化成_dict(cfg, monkeypatch):
    """服务端要把事件 json 化推给前端，序列化失败等于接口 500。"""
    call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})
    scripted(monkeypatch, [("", [call]), ("好", [])])
    agent = Agent(cfg, ToolRegistry([EchoTool()]))

    for event in await _collect(agent):
        payload = event.to_dict()
        assert isinstance(payload["type"], str)
        json.dumps(payload, ensure_ascii=False)  # 不抛异常就说明能推给前端


# ---------- 系统提示 ----------

def test_系统提示包含工作目录与工具名(cfg):
    prompt = _system_prompt(cfg, ["fs_read_file", "shell_run"])
    assert str(cfg.workspace) in prompt
    assert "fs_read_file, shell_run" in prompt


def test_有长期记忆时才注入那一段(cfg):
    # 认「注入段的标题」而不是「长期记忆」四个字：临时目录名里可能正好含这几个字
    prompt = _system_prompt(cfg, [])
    assert "以下是用户的长期记忆" not in prompt

    cfg.memory_file.write_text("# 记忆\n- 用户偏好表格\n", encoding="utf-8")
    prompt = _system_prompt(cfg, [])
    assert "以下是用户的长期记忆" in prompt
    assert "用户偏好表格" in prompt


def test_技能只注入目录不注入正文(cfg):
    cfg.skills_dir.mkdir(parents=True, exist_ok=True)
    (cfg.skills_dir / "w.md").write_text(
        "---\nname: weekly\ndescription: 写周报时用\n---\n第一步：找 git log\n",
        encoding="utf-8",
    )
    prompt = _system_prompt(cfg, [])

    assert "weekly：写周报时用" in prompt
    assert "第一步：找 git log" not in prompt  # 渐进披露：正文按需取


@pytest.mark.parametrize("rule", ["memory_append", "skill_load"])
def test_系统提示里点明了记忆与技能的用法(cfg, rule):
    assert rule in _system_prompt(cfg, [])


# ---------- 用量事件（第 18 步：token 可观测） ----------

async def test_每轮_usage_带轮次号与上下文构成(cfg, monkeypatch):
    """面板要画"每轮花多少、花在哪"，所以轮次号和构成必须在事件里。"""
    call = ToolCall(id="c1", name="echo", arguments={"text": "hi"}, raw_arguments='{"text":"hi"}')
    scripted(monkeypatch, [("", [call]), ("收到", [])])
    agent = Agent(cfg, ToolRegistry([EchoTool()]))

    events = await _collect(agent, "帮我看看")
    usages = [e for e in events if e.type == "usage"]

    assert [u.data["round_no"] for u in usages] == [1, 2]

    # 第 1 轮发请求时：有系统提示和问题，工具结果还没出现
    r1 = usages[0].data["context"]
    assert r1["system"] > 0
    assert r1["question"] == len("帮我看看")
    assert r1["tool_results"] == 0

    # 第 2 轮发请求时：上一轮的工具结果进了上下文；
    # 问题不再是最后一条 user，按约定计入历史
    r2 = usages[1].data["context"]
    assert r2["tool_results"] == len("echo:hi")
    assert r2["question"] == 0
    assert r2["history"] > 0
