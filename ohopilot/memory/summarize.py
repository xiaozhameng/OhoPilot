"""滚动摘要：把会话早期的一批对话压成一段文字，替代"超过 200 条就丢掉"。

为什么放在 memory 层：它产出的是「会话级记忆」，存进 summaries 表和 messages 平级。
它借用 core.llm 的模型客户端（只借 complete，不借 agent），
而 core.llm 不认识 memory —— 依赖仍然是单向的。

两条设计原则：
1. 累积压缩：新摘要基于「旧摘要 + 这批新对话」，否则上一次压过的信息就丢了。
2. 失败即放弃：摘要只是省 token 的优化项，模型调用失败绝不能让对话挂掉。
"""

from __future__ import annotations

from ohopilot.config import Config
from ohopilot.core.llm import LLMError, complete
from ohopilot.memory.store import SessionStore

#: 摘要提示词。要求里点明"保留什么"比"压缩到多短"更有效——
#: 只说压短，模型会优先删掉数字和人名这类真正有用的东西。
SUMMARY_PROMPT = (
    "你是对话压缩器。把下面的对话压缩成不超过 300 字的中文摘要。\n"
    "必须保留：用户的身份与偏好、已经确认的事实与结论、尚未完成的任务、"
    "关键数字、文件名与命令。\n"
    "必须删掉：寒暄、鼓励与情绪化的闲聊、重复表述、工具调用的过程细节。\n"
    "用陈述句罗列，不要写成故事，不要评论。只输出摘要正文。"
)


def _transcript(rows: list[tuple[int, str, str]]) -> str:
    return "\n".join(f"{role}: {content}" for _id, role, content in rows)


async def maybe_summarize(cfg: Config, store: SessionStore, session_id: str) -> bool:
    """未压缩消息超过阈值时压一批，返回是否真的压了。

    返回 bool 而不是摘要文本：调用方只需要知道"要不要重新加载历史"。
    """
    if cfg.summary_after <= 0:
        return False

    upto, previous = store.get_summary(session_id)
    pending = store.count_after(session_id, upto)
    if pending <= cfg.summary_after:
        return False

    # 只压到"留下 summary_keep 条原文"为止，最近几轮必须原样保留：
    # 摘要会丢语气和细节，而用户接下来最可能追问的正是刚说过的事。
    batch = pending - max(cfg.summary_keep, 0)
    if batch <= 0:
        return False

    rows = store.fetch_after(session_id, upto, batch)
    if not rows:
        return False

    blocks: list[str] = []
    if previous:
        blocks.append(f"已有摘要（更早的内容）：\n{previous}")
    blocks.append(f"新增对话：\n{_transcript(rows)}")

    try:
        summary = await complete(
            cfg,
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": "\n\n".join(blocks)},
            ],
        )
    except LLMError:
        # 压缩失败就当没发生：这次不压，下次再试，对话照常进行
        return False

    summary = summary.strip()
    if not summary:
        return False

    # upto_id 取这批消息的最后一条：下次从这里往后数
    store.save_summary(session_id, rows[-1][0], summary)
    return True
