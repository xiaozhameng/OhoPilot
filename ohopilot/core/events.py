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
    "todo",              # 任务清单快照（全量）
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

def tool_result(
    call_id: str,
    name: str,
    content: str,
    is_error: bool = False,
    duration_ms: int | None = None,
) -> Event:
    # duration_ms 给控制台记耗时用；未知工具/用户拒绝这类"没真正执行"的
    # 分支保持 None，统计平均耗时时不掺噪声。
    return Event(
        "tool_result",
        {
            "id": call_id,
            "name": name,
            "content": content,
            "is_error": is_error,
            "duration_ms": duration_ms,
        },
    )

def approval_request(call_id: str, name: str, arguments: dict[str, Any], reason: str) -> Event:
    return Event(
        "approval_request",
        {"id": call_id, "name": name, "arguments": arguments, "reason": reason},
    )

def usage(
    prompt_tokens: int,
    completion_tokens: int,
    total: int,
    round_no: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    duration_ms: int = 0,
    first_token_ms: int = 0,
    context: dict[str, int] | None = None,
) -> Event:
    """一轮模型调用的用量。

    round_no 从 1 起：一次问答里第几次调模型。带轮次号，界面才能画出
    「每轮各花多少」的柱状图，而不是只有一个总数。
    context 是发出这次请求时各类内容占的字符数（system / history /
    question / tool_results）——token 数模型只给总数，"花在哪"只能靠
    字符数占比近似归因，但足够定位"是大模型话多还是工具结果太肥"。
    """
    data: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total,
        "round_no": round_no,
        "cache_hit_tokens": cache_hit_tokens,
        "cache_miss_tokens": cache_miss_tokens,
        "duration_ms": duration_ms,
        "first_token_ms": first_token_ms,
    }
    if context is not None:
        data["context"] = context
    return Event("usage", data)

def todo(items: list[dict[str, Any]]) -> Event:
    """任务清单快照，全量推送。

    带全量而不是增量（"3 号变 done 了"），是为了让界面端只剩一个动作：
    拿到就整面替换，不用处理丢包/乱序。清单最多十几条，全量不贵。
    """
    return Event("todo", {"items": items})

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
