"""模型层：用 LiteLLM 统一各家接口，对外只暴露一个流式函数。

换模型 = 改一个环境变量，不动内核代码。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import litellm

from ..config import Config

# 各家 provider 参数不一致时，自动丢掉不支持的参数而不是直接报错
litellm.drop_params = True
litellm.suppress_debug_info = True


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""

    def to_message_part(self) -> dict[str, Any]:
        """转成 OpenAI 消息格式里 assistant.tool_calls 的一项。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.raw_arguments or json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMError(RuntimeError):
    """把底层异常包一层，方便界面统一提示。"""


def _resolve_credentials(cfg: Config) -> tuple[str | None, str | None]:
    api_key = cfg.api_key
    api_base = cfg.api_base
    # 走 openai/ 前缀时必须给 key，否则 litellm 会去翻 OPENAI_API_KEY
    return api_key, api_base


def _accumulate_tool_calls(slots: dict[int, dict[str, str]], delta_tool_calls: Any) -> None:
    """流式返回的 tool_call 是分片来的，要按 index 拼回去。"""
    for tc in delta_tool_calls or []:
        idx = getattr(tc, "index", 0) or 0
        slot = slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        tc_id = getattr(tc, "id", None)
        if tc_id:
            slot["id"] = tc_id
        fn = getattr(tc, "function", None)
        if fn is not None:
            name = getattr(fn, "name", None)
            if name:
                slot["name"] = name
            args = getattr(fn, "arguments", None)
            if args:
                slot["arguments"] += args


async def stream_chat(
    cfg: Config,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """流式调用模型。

    yield 两种东西：
      ("text", str)        —— 文本增量，边收边渲染
      ("turn", AssistantTurn) —— 收尾时的完整结果（文本 + 工具调用 + 用量）
    """
    api_key, api_base = _resolve_credentials(cfg)

    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if temperature is not None:
        kwargs["temperature"] = temperature
    if cfg.reasoning_effort:
        kwargs["reasoning_effort"] = cfg.reasoning_effort

    text_parts: list[str] = []
    slots: dict[int, dict[str, str]] = {}
    prompt_tokens = completion_tokens = total_tokens = 0

    try:
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                prompt_tokens = getattr(chunk_usage, "prompt_tokens", 0) or prompt_tokens
                completion_tokens = getattr(chunk_usage, "completion_tokens", 0) or completion_tokens
                total_tokens = getattr(chunk_usage, "total_tokens", 0) or total_tokens

            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                yield "text", content

            _accumulate_tool_calls(slots, getattr(delta, "tool_calls", None))
    except Exception as exc:  # noqa: BLE001 - 统一转成 LLMError 交给上层展示
        raise LLMError(f"模型调用失败：{type(exc).__name__}: {exc}") from exc

    tool_calls: list[ToolCall] = []
    for idx in sorted(slots):
        slot = slots[idx]
        raw = slot["arguments"]
        parsed: dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"_raw": raw}
        tool_calls.append(
            ToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=parsed,
                raw_arguments=raw,
            )
        )

    yield (
        "turn",
        AssistantTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


def has_credentials(cfg: Config) -> bool:
    if cfg.api_key:
        return True
    # 有 api_base 的本地模型（Ollama / vLLM）不需要 key
    base = (cfg.api_base or "").lower()
    return any(token in base for token in ("localhost", "127.0.0.1", "0.0.0.0"))


def describe(cfg: Config) -> str:
    key_state = "已配置" if os.getenv("OHOPILOT_API_KEY") else (
        "来自 DEEPSEEK_API_KEY" if os.getenv("DEEPSEEK_API_KEY") else "未配置"
    )
    return f"model={cfg.model}  base={cfg.api_base}  key={key_state}"
