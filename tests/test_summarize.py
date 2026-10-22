"""滚动摘要：阈值、保留窗口、累积、失败即放弃。

摘要会花真实 token，所以这里全部用假的 complete()，
测的是「什么时候压、压哪一段、压完账对不对」，不测模型写得好不好。
"""

from __future__ import annotations

import pytest

from ohopilot.memory import summarize
from ohopilot.memory.summarize import maybe_summarize


class FakeLLM:
    """记下每次调用收到的 prompt，返回固定摘要。"""

    def __init__(self, reply: str = "这是一段摘要", raises: Exception | None = None):
        self.reply = reply
        self.raises = raises
        self.prompts: list[str] = []

    async def __call__(self, cfg, messages):
        self.prompts.append(messages[-1]["content"])
        if self.raises:
            raise self.raises
        return self.reply


@pytest.fixture
def fake(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(summarize, "complete", llm)
    return llm


def _fill(store, session_id: str, count: int) -> None:
    for i in range(count):
        store.append(session_id, "user" if i % 2 == 0 else "assistant", f"内容{i}")


async def test_没超过阈值不压(cfg, store, fake):
    cfg.summary_after = 4
    _fill(store, "s1", 4)  # 正好等于阈值，不该触发

    assert await maybe_summarize(cfg, store, "s1") is False
    assert fake.prompts == []
    assert store.get_summary("s1") == (0, "")


async def test_超过阈值时只压到留下_summary_keep_条原文(cfg, store, fake):
    cfg.summary_after = 4
    cfg.summary_keep = 2
    _fill(store, "s1", 7)  # pending=7 > 4，batch=7-2=5

    assert await maybe_summarize(cfg, store, "s1") is True

    upto, content = store.get_summary("s1")
    assert content == "这是一段摘要"
    # 压掉了前 5 条，还剩最近 2 条没进摘要
    assert store.count_after("s1", upto) == 2
    # 送进模型的只有前 5 条
    assert "内容4" in fake.prompts[0]
    assert "内容5" not in fake.prompts[0]


async def test_第二次压缩要把旧摘要一起带上(cfg, store, fake):
    """不累积就会丢信息：上一轮压过的内容不会自己回到新 prompt 里。"""
    cfg.summary_after = 2
    cfg.summary_keep = 1
    _fill(store, "s1", 4)
    await maybe_summarize(cfg, store, "s1")

    _fill(store, "s1", 4)
    await maybe_summarize(cfg, store, "s1")

    assert len(fake.prompts) == 2
    assert "已有摘要" in fake.prompts[1]
    assert "这是一段摘要" in fake.prompts[1]


async def test_模型报错时放弃但不抛出去(cfg, store, monkeypatch):
    """摘要只是省 token 的优化项，不能因为它把整轮对话挂掉。"""
    monkeypatch.setattr(
        summarize,
        "complete",
        FakeLLM(raises=summarize.LLMError("模型挂了")),
    )
    cfg.summary_after = 1
    cfg.summary_keep = 0
    _fill(store, "s1", 3)

    assert await maybe_summarize(cfg, store, "s1") is False
    assert store.get_summary("s1") == (0, "")


async def test_模型返回空串时不写摘要(cfg, store, monkeypatch):
    monkeypatch.setattr(summarize, "complete", FakeLLM(reply="   "))
    cfg.summary_after = 1
    cfg.summary_keep = 0
    _fill(store, "s1", 3)

    assert await maybe_summarize(cfg, store, "s1") is False
    assert store.get_summary("s1") == (0, "")


async def test_summary_after_设为_0_等于关闭(cfg, store, fake):
    cfg.summary_after = 0
    _fill(store, "s1", 50)

    assert await maybe_summarize(cfg, store, "s1") is False
    assert fake.prompts == []


async def test_保留数比待压数还大时什么也不做(cfg, store, fake):
    """边界：summary_after 很小但 summary_keep 很大，batch 会变成 0 或负数。"""
    cfg.summary_after = 1
    cfg.summary_keep = 100
    _fill(store, "s1", 5)

    assert await maybe_summarize(cfg, store, "s1") is False
    assert fake.prompts == []


async def test_压完之后再回放_历史只剩摘要加最近几条(cfg, store, fake):
    cfg.summary_after = 3
    cfg.summary_keep = 2
    _fill(store, "s1", 6)
    await maybe_summarize(cfg, store, "s1")

    history = store.load_history("s1")
    assert history[0]["content"].startswith("【本次会话早前内容的摘要】")
    assert [m["content"] for m in history[1:]] == ["内容4", "内容5"]
