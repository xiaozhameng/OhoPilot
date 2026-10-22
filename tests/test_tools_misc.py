"""两个「薄工具」：memory_append 与 skill_load。

它们本身没多少逻辑，但一个改的是模型以后每一轮的行为（必须审批），
一个决定了渐进披露能不能真的取到正文（名字对不上就等于技能失效）。
"""

from __future__ import annotations

from ohopilot.memory.longterm import load_longterm
from ohopilot.tools.memory import MemoryAppendTool
from ohopilot.tools.skills import SkillLoadTool


# ---------- memory_append ----------

def test_写记忆必须审批(cfg):
    """它影响的是之后每一轮对话，用户得有机会看一眼再放进去。"""
    tool = MemoryAppendTool(cfg)
    need, reason = tool.needs_approval({"text": "x"})
    assert need is True
    assert "长期记忆" in reason


async def test_写进长期记忆并返回位置(cfg):
    result = await MemoryAppendTool(cfg).run(text="用户是后端工程师")

    assert not result.is_error
    assert "已记住" in result.content
    assert "用户是后端工程师" in load_longterm(cfg)


async def test_空内容写不进去(cfg):
    result = await MemoryAppendTool(cfg).run(text="   ")
    assert result.is_error
    assert "不能为空" in result.content


# ---------- skill_load ----------

async def test_按名字取到技能正文(cfg):
    cfg.skills_dir.mkdir(parents=True, exist_ok=True)
    (cfg.skills_dir / "weekly.md").write_text(
        "---\nname: weekly\ndescription: 写周报\n---\n第一步：找 git log\n",
        encoding="utf-8",
    )

    result = await SkillLoadTool(cfg).run(name="weekly")

    assert not result.is_error
    assert "第一步：找 git log" in result.content


async def test_名字写错时列出可用技能(cfg):
    """只说"没找到"，模型会换个名字瞎猜；给出清单它才能自我纠正。"""
    cfg.skills_dir.mkdir(parents=True, exist_ok=True)
    (cfg.skills_dir / "weekly.md").write_text(
        "---\nname: weekly\ndescription: 写周报\n---\n步骤\n", encoding="utf-8"
    )

    result = await SkillLoadTool(cfg).run(name="week-report")

    assert result.is_error
    assert "weekly" in result.content


async def test_目录为空时明确报错(cfg):
    result = await SkillLoadTool(cfg).run(name="weekly")
    assert result.is_error
    assert "没有任何可用技能" in result.content


def test_取技能正文不需要审批(cfg):
    assert SkillLoadTool(cfg).needs_approval({"name": "x"}) == (False, "")
