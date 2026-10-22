"""配置读取。

这一层的价值全在「容错」：环境变量写错了不该让程序起不来，
但也不能悄悄用错值，所以「写错退回默认」和「优先级」都要说清楚。
"""

from __future__ import annotations

from pathlib import Path

from ohopilot.config import Config


def test_load_读取环境变量(monkeypatch, tmp_path):
    monkeypatch.setenv("OHOPILOT_MODEL", "gpt-test")
    monkeypatch.setenv("OHOPILOT_API_BASE", "http://127.0.0.1:11434")
    monkeypatch.setenv("OHOPILOT_API_KEY", "sk-abc")
    monkeypatch.setenv("OHOPILOT_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("OHOPILOT_MAX_TOOL_ROUNDS", "3")

    cfg = Config.load()

    assert cfg.model == "gpt-test"
    assert cfg.api_base == "http://127.0.0.1:11434"
    assert cfg.api_key == "sk-abc"
    assert cfg.home == tmp_path / "h"
    assert cfg.max_tool_rounds == 3


def test_工作目录优先级_参数高于环境变量(monkeypatch, tmp_path):
    env_ws = tmp_path / "from-env"
    arg_ws = tmp_path / "from-arg"

    monkeypatch.setenv("OHOPILOT_WORKSPACE", str(env_ws))
    assert Config.load().workspace == env_ws.resolve()

    assert Config.load(workspace=arg_ws).workspace == arg_ws.resolve()


def test_没配工作目录时用当前目录(monkeypatch, tmp_path):
    monkeypatch.delenv("OHOPILOT_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert Config.load().workspace == Path(tmp_path).resolve()


def test_布尔值认得几种写法(monkeypatch):
    for raw, expected in [
        ("1", True), ("true", True), ("YES", True), ("on", True),
        ("0", False), ("no", False), ("off", False), ("False", False),
    ]:
        monkeypatch.setenv("OHOPILOT_ALLOW_WRITE", raw)
        assert Config.load().allow_write is expected, raw


def test_布尔值写错时退回默认(monkeypatch):
    """认不出的值退回默认，而不是静默当成 False。"""
    monkeypatch.setenv("OHOPILOT_ALLOW_WRITE", "大概吧")
    assert Config.load().allow_write is True


def test_整数写错时退回默认(monkeypatch):
    monkeypatch.setenv("OHOPILOT_SHELL_TIMEOUT", "一分钟")
    assert Config.load().shell_timeout == 60


def test_派生路径都挂在_home_下(cfg):
    assert cfg.skills_dir == cfg.home / "skills"
    assert cfg.memory_file == cfg.home / "MEMORY.md"
    assert cfg.db_path == cfg.home / "ohopilot.db"


def test_ensure_dirs_建目录和初始记忆文件(cfg):
    cfg.ensure_dirs()

    assert cfg.skills_dir.is_dir()
    assert cfg.memory_file.exists()
    # 初始文件只有标题，不该被当成记忆注入系统提示
    from ohopilot.memory.longterm import load_longterm

    assert load_longterm(cfg) == ""


def test_ensure_dirs_幂等且不覆盖已有记忆(cfg):
    cfg.ensure_dirs()
    cfg.memory_file.write_text("# 记忆\n- 一条重要事实\n", encoding="utf-8")

    cfg.ensure_dirs()

    assert "一条重要事实" in cfg.memory_file.read_text(encoding="utf-8")
