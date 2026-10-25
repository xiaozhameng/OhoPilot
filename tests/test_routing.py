"""模型路由测试：fast_model 配置解析、complete(fast=True) 换模型、摘要走快模型。

不发真网：monkeypatch litellm.acompletion 换成记录器，返回假流式响应。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ohopilot.config import Config
from ohopilot.core import llm as llm_mod
from ohopilot.core.llm import complete
from ohopilot.memory.summarize import maybe_summarize


def _fake_stream(text: str, prompt: int = 100, completion: int = 10):
    """造一个最简单的假流式响应：一个带内容的 chunk + 一个带 usage 的收尾 chunk。"""

    async def gen():
        delta = SimpleNamespace(content=text, tool_calls=None)
        chunk = SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta)])
        yield chunk
        usage = SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=prompt,
        )
        yield SimpleNamespace(usage=usage, choices=[])

    return gen()


@pytest.fixture
def recorder(monkeypatch):
    """替换 litellm.acompletion，记录每次调用用的 model，并回一段固定文本。"""
    calls: list[dict] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return _fake_stream("摘要内容")

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)
    return calls


async def test_complete_默认走主模型(cfg, recorder):
    await complete(cfg, [{"role": "user", "content": "hi"}])
    assert recorder[0]["model"] == cfg.model


async def test_complete_fast且配置了快模型_走快模型(cfg, recorder):
    cfg.fast_model = "openai/deepseek-chat"
    await complete(cfg, [{"role": "user", "content": "hi"}], fast=True)
    assert recorder[0]["model"] == "openai/deepseek-chat"


async def test_complete_fast但没配快模型_回落主模型(cfg, recorder):
    assert cfg.fast_model == ""
    await complete(cfg, [{"role": "user", "content": "hi"}], fast=True)
    assert recorder[0]["model"] == cfg.model


async def test_摘要走快模型(cfg, store, recorder):
    """滚动摘要是路由的第一批受益者：超过阈值触发摘要时，model 应是 fast_model。"""
    cfg.fast_model = "openai/deepseek-chat"
    cfg.summary_after = 4
    cfg.summary_keep = 1
    sid = store.create_session()
    for i in range(6):
        store.append(sid, "user" if i % 2 == 0 else "assistant", f"消息 {i}")

    assert await maybe_summarize(cfg, store, sid) is True
    assert recorder[0]["model"] == "openai/deepseek-chat"


def test_config_从环境读快模型(monkeypatch):
    monkeypatch.setenv("OHOPILOT_FAST_MODEL", "openai/deepseek-chat")
    cfg = Config.load(workspace=None)
    assert cfg.fast_model == "openai/deepseek-chat"
