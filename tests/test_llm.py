"""模型层的用量归因：缓存命中字段在各家 API 里位置不一，挖取逻辑必须有测试钉住。

命中率是做优化决策的依据，挖错了会静默变成 0，面板上就是一片"全未命中"，
看起来像出问题，其实只是解析问题——所以这里宁可测得啰嗦。
"""

from __future__ import annotations

from types import SimpleNamespace

from ohopilot.core.llm import _cache_fields


def test_deepseek_风格的顶层缓存字段():
    """DeepSeek 原生返回 prompt_cache_hit_tokens / prompt_cache_miss_tokens。"""
    usage = SimpleNamespace(
        prompt_tokens=2300,
        prompt_cache_hit_tokens=2300,
        prompt_cache_miss_tokens=0,
    )
    assert _cache_fields(usage) == (2300, 0)


def test_openai_风格的_details_缓存字段():
    """OpenAI 系把命中数塞在 usage.prompt_tokens_details.cached_tokens 里。"""
    usage = SimpleNamespace(
        prompt_tokens=1000,
        prompt_tokens_details=SimpleNamespace(cached_tokens=800),
    )
    assert _cache_fields(usage) == (800, 200)


def test_挖不到时全部算未命中_而不是谎报命中():
    """宁可虚报 miss 也不能虚报 hit：命中率是优化决策的依据。"""
    assert _cache_fields(SimpleNamespace(prompt_tokens=500)) == (0, 500)


def test_没有_usage_对象也不崩():
    """极端情况：上游什么都没给，返回全零而不是抛异常打断对话。"""
    assert _cache_fields(SimpleNamespace()) == (0, 0)


def test_命中数不会超过_prompt_总数():
    """miss 被算成负数时压回 0，避免面板上出现负的柱子。"""
    usage = SimpleNamespace(
        prompt_tokens=100,
        prompt_cache_hit_tokens=800,
        prompt_cache_miss_tokens=None,
    )
    assert _cache_fields(usage) == (800, 0)
