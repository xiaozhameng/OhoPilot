"""长期记忆：注入什么、不注入什么、写的时候怎么加前缀。

最容易犯的错是把「写给人看的注释」也注进提示词——
空记忆时 ensure_dirs 建的那两行说明，就这样白占过 token。
"""

from __future__ import annotations

import pytest

from ohopilot.memory.longterm import MAX_MEMORY_CHARS, append_longterm, load_longterm


def test_文件不存在时返回空串(cfg):
    assert cfg.memory_file.exists() is False
    assert load_longterm(cfg) == ""


def test_只有标题和注释时返回空串(cfg):
    cfg.memory_file.write_text("# OhoPilot 长期记忆\n", encoding="utf-8")
    assert load_longterm(cfg) == ""


def test_注释行不进提示词但正文保留(cfg):
    cfg.memory_file.write_text(
        "# OhoPilot 长期记忆\n> 这个文件会自动注入系统提示\n- 用户是后端工程师\n",
        encoding="utf-8",
    )
    text = load_longterm(cfg)

    assert "用户是后端工程师" in text
    assert "这个文件会自动注入" not in text


def test_超过上限会截断(cfg):
    cfg.memory_file.write_text("# 记忆\n" + "- 一条很长的事实\n" * 1000, encoding="utf-8")
    text = load_longterm(cfg)

    assert len(text) < MAX_MEMORY_CHARS + 100
    assert "已截断" in text


def test_自定义上限生效(cfg):
    cfg.memory_file.write_text("# 记忆\n" + "啊" * 500, encoding="utf-8")
    assert "已截断" in load_longterm(cfg, limit=50)


def test_追加会统一成列表项(cfg):
    append_longterm(cfg, "用户偏好表格对比")
    assert "- 用户偏好表格对比" in cfg.memory_file.read_text(encoding="utf-8")


def test_已经是列表项就不重复加前缀(cfg):
    append_longterm(cfg, "- 已经有前缀了")
    content = cfg.memory_file.read_text(encoding="utf-8")
    assert content.count("- ") == 1
    assert "-- " not in content


def test_追加多条按顺序累积(cfg):
    append_longterm(cfg, "第一条")
    append_longterm(cfg, "第二条")
    text = load_longterm(cfg)
    assert text.index("第一条") < text.index("第二条")


def test_追加返回总字符数(cfg):
    assert append_longterm(cfg, "abc") == len("- abc\n")


def test_空内容拒绝写入(cfg):
    with pytest.raises(ValueError):
        append_longterm(cfg, "   ")


def test_追加之后立刻能被读到(cfg):
    """agent 每轮现读记忆：刚写的一条下一句就该生效。"""
    append_longterm(cfg, "用户在做一个叫 OhoPilot 的项目")
    assert "OhoPilot" in load_longterm(cfg)


def test_文件末尾没有换行也能正确追加(cfg):
    cfg.memory_file.write_text("# 记忆\n- 老的一条", encoding="utf-8")
    append_longterm(cfg, "新的一条")
    lines = cfg.memory_file.read_text(encoding="utf-8").splitlines()
    assert lines[-2:] == ["- 老的一条", "- 新的一条"]
