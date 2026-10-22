"""
cli 和 http 服务端只消费这一种格式，所以界面怎么换都不用改内核。
"""

from __future__ import annotations

from dataclasses import field, dataclass, asdict
from typing import Literal, Any

EventType = Literal[
    "text",              # 模型输出的文本增量
    "tool_call",         # 模型决定调用工具
    "tool_result",       # 工具执行完毕
    "approval_request",  # 需要人工确认（危险操作）
    "usage",             # token 用量
    "notice",            # 给界面看的提示信息（不是模型的输出）
    "error",             # 出错
    "done",              # 一轮回答结束
]

@dataclass
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.data}

def text(delta: str) -> Event:
    return Event("text", {"delta": delta})

def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> Event:
    return Event("tool_call", {"id": call_id, "name": name, "arguments": arguments})

def tool_result(call_id: str, name: str, content: str, is_error: bool = False) -> Event:
    return Event(
        "tool_result",
        {"id": call_id, "name": name, "content": content, "is_error": is_error},
    )

def approval_request(call_id: str, name: str, arguments: dict[str, Any], reason: str) -> Event:
    return Event(
        "approval_request",
        {"id": call_id, "name": name, "arguments": arguments, "reason": reason},
    )

def usage(prompt_tokens: int, completion_tokens: int, total: int) -> Event:
    return Event(
        "usage",
        {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total,
        }
    )

def error(message: str) -> Event:
    return Event("error", {"message": message})

def notice(message: str) -> Event:
    """给界面看的提示（压缩了摘要、切换了会话……），不是模型说的话。

    单独一类而不是塞进 text，是因为 text 会被当成模型输出累积进会话历史，
    提示信息不该落库、也不该影响下一轮 prompt。
    """
    return Event("notice", {"message": message})

def done(session_id: str) -> Event:
    return Event("done", {"session_id": session_id})

def event_to_dict(event: Event) -> dict[str, Any]:
    return asdict(event)
