"""技能解析与加载。

技能是「提示词能力」，最容易出的问题是**静默失效**：
文件放对了、名字对不上、或者缺 description 导致它根本不进系统提示。
所以这里的用例全部盯着「什么情况下会被丢掉」。
"""

from __future__ import annotations

from pathlib import Path

from ohopilot.skills.loader import (
    MAX_SKILL_CHARS,
    load_skills,
    parse_skill,
    render_skill_index,
)

GOOD = """---
name: weekly-report
description: 生成周报。用户提到周报、本周总结时使用。
---
1. 找本周的 git log
2. 按项目分组
"""


def _write_skill(cfg, filename: str, text: str) -> None:
    cfg.skills_dir.mkdir(parents=True, exist_ok=True)
    (cfg.skills_dir / filename).write_text(text, encoding="utf-8")


def test_正常解析出三要素():
    skill = parse_skill(GOOD, "fallback", Path("weekly-report.md"))
    assert skill is not None
    assert skill.name == "weekly-report"
    assert "周报" in skill.description
    assert skill.body.startswith("1. 找本周")


def test_缺_description_直接丢弃():
    """description 是模型判断「要不要用」的唯一依据，没有它就是噪声。"""
    text = "---\nname: x\n---\n步骤\n"
    assert parse_skill(text, "x", Path("x.md")) is None


def test_没有_frontmatter_丢弃():
    assert parse_skill("# 标题\n正文\n", "x", Path("x.md")) is None


def test_frontmatter_没有闭合也丢弃():
    assert parse_skill("---\nname: x\ndescription: y\n正文", "x", Path("x.md")) is None


def test_只有元数据没有正文也丢弃():
    """只有一个 description 的话，它和系统提示里那一行是重复的。"""
    text = "---\nname: x\ndescription: 什么时候用\n---\n\n"
    assert parse_skill(text, "x", Path("x.md")) is None


def test_没有_name_时用文件名兜底():
    text = "---\ndescription: 什么时候用\n---\n步骤\n"
    skill = parse_skill(text, "from-filename", Path("from-filename.md"))
    assert skill is not None
    assert skill.name == "from-filename"


def test_名字非法时丢弃():
    text = "---\nname: 有 空格\n---\n步骤\n"
    assert parse_skill(text, "x", Path("x.md")) is None


def test_正文过长会被截断():
    text = "---\nname: x\ndescription: y\n---\n" + "啊" * (MAX_SKILL_CHARS + 100)
    skill = parse_skill(text, "x", Path("x.md"))
    assert skill is not None
    assert len(skill.body) < MAX_SKILL_CHARS + 100
    assert "已截断" in skill.body


def test_元数据的注释行和空行被忽略():
    text = "---\n# 这是注释\nname: x\n\ndescription: y\n---\n步骤\n"
    skill = parse_skill(text, "fallback", Path("x.md"))
    assert skill is not None
    assert skill.name == "x"


def test_引号会被剥掉():
    text = '---\nname: "x"\ndescription: \'y\'\n---\n步骤\n'
    skill = parse_skill(text, "fallback", Path("x.md"))
    assert skill is not None
    assert (skill.name, skill.description) == ("x", "y")


def test_load_skills_扫描目录并跳过坏文件(cfg):
    _write_skill(cfg, "a.md", GOOD)
    _write_skill(cfg, "b.md", "没有 frontmatter，不是技能")
    _write_skill(cfg, "c.txt", "后缀不对，不该被读")

    skills = load_skills(cfg)
    assert [s.name for s in skills] == ["weekly-report"]


def test_目录不存在时返回空列表(cfg):
    assert cfg.skills_dir.exists() is False
    assert load_skills(cfg) == []


def test_同名技能只保留第一个(cfg):
    _write_skill(cfg, "a.md", GOOD)
    _write_skill(cfg, "b.md", GOOD)
    assert len(load_skills(cfg)) == 1


def test_技能索引只有名字和触发条件(cfg):
    """渐进披露的关键：系统提示里绝不放正文。"""
    _write_skill(cfg, "a.md", GOOD)
    index = render_skill_index(load_skills(cfg))

    assert index == "- weekly-report：生成周报。用户提到周报、本周总结时使用。"
    assert "按项目分组" not in index


def test_没有技能时索引是空串():
    """空串让调用方可以直接不拼这一段，而不是塞个空标题。"""
    assert render_skill_index([]) == ""
