"""模型层：用 LiteLLM 统一各家接口，对外只暴露一个流式函数。

换模型 = 改一个环境变量，不动内核代码。
"""
import json
import os
import time
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator


from ohopilot.config import Config

# 让 LiteLLM 直接用内置的模型价格表，不要每次启动都联网拉取
# （国内网络下这一步会卡几秒并打印告警，纯属噪音）
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


import litellm


# 各家 provider 参数不一致时，自动丢掉不支持的参数而不是直接报错
litellm.drop_params = True
litellm.suppress_debug_info = True

# 一次工具调用的完整信息（id / name / arguments），to_message_part() 转成回传给 API 的格式
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
    # ---- 可观测性字段（第 18 步）----
    # DeepSeek 对「命中前缀缓存」的输入按约 1/10 计价，所以命中率是最值钱的指标：
    # 命中率低 → 说明每次请求的前缀都在变（比如把时间戳放进了系统提示），先修它。
    # 这两个字段各家返回位置不一：DeepSeek 顶层给 prompt_cache_hit_tokens，
    # OpenAI 风格塞在 usage.prompt_tokens_details.cached_tokens 里，这里两处都试。
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    duration_ms: int = 0       # 本次调用总耗时
    first_token_ms: int = 0    # 从发请求到收到第一个增量的延迟（用户感知的"等多久开始出字"）

class LLMError(RuntimeError):
    """把底层异常包一层，方便界面统一提示。"""

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


def has_credentials(cfg: Config) -> bool:
    if cfg.api_key:
        return True
    # 有 api_base 指向本地的模型（Ollama / vLLM）不需要 key
    base = (cfg.api_base or "").lower()
    return any(token in base for token in ("localhost", "127.0.0.1", "0.0.0.0"))

def _cache_fields(usage: Any) -> tuple[int, int]:
    """从 usage 对象里尽量挖出 (缓存命中, 缓存未命中) 的 token 数。

    挖不到就退回 (0, prompt_tokens)：宁可把全部 prompt 算成"未命中"，
    也不要谎报 100% 命中——命中率是被拿来做优化决策的，虚高会误导。
    """
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    miss = getattr(usage, "prompt_cache_miss_tokens", None)
    if hit is None:
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details is not None else None
        if cached is not None:
            hit = cached
    if hit is None:
        return 0, int(prompt)
    if miss is None:
        miss = int(prompt) - int(hit)
    return int(hit or 0), max(int(miss or 0), 0)


async def stream_chat(
        cfg: Config,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,) -> AsyncIterator[tuple[str, Any]]:
    """流式调用模型。
    yield 两种东西：
      ("text", str)           —— 文本增量，边收边渲染
      ("turn", AssistantTurn) —— 收尾时的完整结果（文本 + 工具调用 + 用量）
    """
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    if cfg.api_key:
        kwargs["api_key"] = cfg.api_key
    if cfg.api_base:
        kwargs["api_base"] = cfg.api_base
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    text_parts: list[str] = []
    slots: dict[int, dict[str, str]] = {}
    prompt_tokens = completion_tokens = total_tokens = 0
    cache_hit_tokens = cache_miss_tokens = 0
    started = time.monotonic()
    first_token_ms = 0

    try:
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                prompt_tokens = getattr(chunk_usage, "prompt_tokens", 0) or prompt_tokens
                completion_tokens = getattr(chunk_usage, "completion_tokens", 0) or completion_tokens
                total_tokens = getattr(chunk_usage, "total_tokens", 0) or total_tokens
                cache_hit_tokens, cache_miss_tokens = _cache_fields(chunk_usage)

            choice = getattr(chunk, "choices", None)
            if not choice:
                continue
            delta = getattr(choice[0], "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if content:
                if not first_token_ms:
                    first_token_ms = int((time.monotonic() - started) * 1000)
                text_parts.append(content)
                yield "text", content

            tool_delta = getattr(delta, "tool_calls", None)
            if tool_delta and not first_token_ms:
                # 模型先吐工具调用也算"开始响应了"：用户感知的延迟一样成立
                first_token_ms = int((time.monotonic() - started) * 1000)
            _accumulate_tool_calls(slots, tool_delta)
    except Exception as exc:
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
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            duration_ms=int((time.monotonic() - started) * 1000),
            first_token_ms=first_token_ms,
        ),
    )


async def complete(cfg: Config, messages: list[dict[str, Any]], *, fast: bool = False) -> str:
    """非流式：只把文本收完返回。

    给摘要这类后台任务用——不需要打字机效果，也不需要工具调用。
    复用 stream_chat 而不是另写一套，是为了少一处「换模型要改两个地方」。

    fast=True 且配置了 fast_model 时走便宜模型：这是模型路由（第 25 步）。
    摘要这种「压缩旧对话」的活不需要主模型的推理能力，小模型干同样好、
    成本低一个量级。没配 fast_model 则原样走主模型，行为不变。
    """
    if fast and cfg.fast_model:
        cfg = replace(cfg, model=cfg.fast_model)

    parts: list[str] = []
    async for kind, payload in stream_chat(cfg, messages, None):
        if kind == "text":
            parts.append(payload)
    return "".join(parts)