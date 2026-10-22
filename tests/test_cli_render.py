"""终端渲染层。

只有两件事值得测：参数截断（防止长内容刷屏和泄露）与事件分派。
渲染本身是 print，不值得断言，但「截断」是安全相关的行为。
"""

from __future__ import annotations

import pytest

from ohopilot.cli import BRIEF_LIMIT, _brief


def test_短参数原样显示():
    assert _brief({"path": "a.txt"}) == "{'path': 'a.txt'}"


def test_长参数被截断并标注():
    text = _brief({"path": "x" * 500})
    assert len(text) == BRIEF_LIMIT + len("…[已截断]")
    assert text.endswith("…[已截断]")


def test_边界长度不截断():
    base = str({"a": ""})
    args = {"a": "b" * (BRIEF_LIMIT - len(base))}
    assert len(str(args)) == BRIEF_LIMIT
    assert _brief(args) == str(args)


@pytest.mark.parametrize("limit", [10, 40, 160])
def test_自定义上限生效(limit):
    assert _brief({"content": "y" * 300}, limit=limit).endswith("…[已截断]")


def test_长内容不会整段上屏():
    """10b 的真实事故：把 .env 整段内容打到终端，等于当场泄露密钥。

    截断本身不是保密手段（真正的拦截在 fs 工具的 PROTECTED 清单里），
    但它保证了「一次误操作最多漏出 160 个字符」，而不是整份配置。
    """
    secret = "sk-86e5" + "x" * 400
    brief = _brief({"path": ".env", "content": secret})

    assert len(brief) < 200
    assert secret not in brief


async def test_一轮问答会把文本与错误分别渲染(cfg, capsys):
    """走一遍真实的 Runtime.ask 事件流，确认渲染层不会因为缺某类事件而崩。"""
    from ohopilot.core import events as ev
    from ohopilot.runtime import Runtime

    rt = Runtime.create(cfg=cfg)

    class FakeAgent:
        async def run(self, question, history=None):
            yield ev.text("你好")
            yield ev.notice("提示一条")
            yield ev.usage(1, 2, 3)
            yield ev.error("示例错误")
            yield ev.done("")

    rt.agent = FakeAgent()

    from ohopilot.cli import ask

    await ask(rt, "s1", "在吗", [])
    out = capsys.readouterr().out

    assert "你好" in out
    assert "提示一条" in out
    assert "示例错误" in out
    assert "3 tokens" in out
    rt.close()
