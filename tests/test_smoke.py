"""冒烟测试：不联网、不花钱，验证各层能装起来、安全闸有效。

    cd OhoPilot && python -m pytest -q
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ohopilot.config import Config
from ohopilot.core import events as ev
from ohopilot.runtime import build_runtime
from ohopilot.tools.base import ToolRegistry, ToolResult
from ohopilot.tools.web import html_to_text


@pytest.fixture()
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """给每个测试一套完全隔离的目录。

    关键点：build_runtime() 内部会自己调 Config.load()，而 Config.load() 读的是
    环境变量 OHOPILOT_HOME。所以必须先把 OHOPILOT_HOME 指到 tmp_path，
    否则测试会往用户真实的 ~/.ohopilot 里写数据库和记忆文件。
    """
    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    workspace.mkdir()

    monkeypatch.setenv("OHOPILOT_HOME", str(home))
    monkeypatch.delenv("OHOPILOT_API_KEY", raising=False)

    config = Config(
        model="openai/test-model",
        api_base="http://127.0.0.1:1/v1",
        api_key="sk-test",
        home=home,
        workspace=workspace,
        allow_write=True,
        shell_timeout=10,
        host="127.0.0.1",
        port=8737,
    )
    config.ensure_dirs()
    return config


def test_config_scaffolds_home(cfg: Config) -> None:
    assert cfg.skills_dir.is_dir()
    assert cfg.memory_file.exists()
    assert (cfg.skills_dir / "weekly-report.md").exists()


def test_runtime_never_touches_real_home(cfg: Config) -> None:
    """回归测试：build_runtime 必须待在隔离目录里，不许碰用户真实的 ~/.ohopilot。

    这条是防回归用的——之前测试确实往真实主目录里写了 3 条会话记录。
    """
    real_home = Path.home() / ".ohopilot"
    rt = build_runtime(cfg.workspace)
    try:
        assert rt.cfg.home != real_home, "build_runtime 用了真实主目录"
        assert str(rt.cfg.home).startswith(str(cfg.workspace.parent)), (
            f"配置目录跑到隔离区之外了：{rt.cfg.home}"
        )
        assert rt.cfg.db_path.exists()
        assert str(rt.cfg.db_path).startswith(str(cfg.workspace.parent))
    finally:
        rt.close()


def test_registry_has_expected_tools(cfg: Config) -> None:
    rt = build_runtime(cfg.workspace)
    try:
        names = set(rt.registry.names())
        assert {"fs.read_file", "fs.write_file", "fs.list_dir", "fs.search_files"} <= names
        assert {"shell.run", "web.fetch", "web.search", "skills.load"} <= names
        schemas = rt.registry.schemas()
        assert all(s["type"] == "function" for s in schemas)
    finally:
        rt.close()


def test_write_tool_flags_outside_workspace(cfg: Config) -> None:
    rt = build_runtime(cfg.workspace)
    try:
        tool = rt.registry.get("fs.write_file")
        assert tool is not None

        inside, _ = tool.needs_approval({"path": "note.md"})
        assert inside is False, "工作目录内写入不该弹确认"

        outside, reason = tool.needs_approval({"path": "/tmp/evil.md"})
        assert outside is True
        assert "工作目录之外" in reason
    finally:
        rt.close()


def test_shell_deny_list(cfg: Config) -> None:
    rt = build_runtime(cfg.workspace)
    try:
        tool = rt.registry.get("shell.run")
        assert tool is not None

        blocked, reason = tool.needs_approval({"command": "rm -rf /"})
        assert blocked is True
        assert reason.startswith("__DENY__")

        blocked, reason = tool.needs_approval({"command": "ls -la"})
        assert blocked is True
        assert not reason.startswith("__DENY__")
    finally:
        rt.close()


def test_fs_round_trip(cfg: Config) -> None:
    rt = build_runtime(cfg.workspace)
    try:
        write = rt.registry.get("fs.write_file")
        read = rt.registry.get("fs.read_file")
        assert write is not None and read is not None

        written = asyncio.run(write.safe_run({"path": "a/b.txt", "content": "你好\n世界"}))
        assert not written.is_error

        content = asyncio.run(read.safe_run({"path": "a/b.txt"}))
        assert "你好" in content.content and "世界" in content.content
    finally:
        rt.close()


def test_session_and_memory_store(cfg: Config) -> None:
    rt = build_runtime(cfg.workspace)
    try:
        sid = rt.store.create_session("测试")
        rt.store.append(sid, {"role": "user", "content": "hi"})
        rt.store.append(sid, {"role": "assistant", "content": "在"})
        history = rt.store.history(sid)
        assert [m["role"] for m in history] == ["user", "assistant"]
        assert rt.store.latest_session() == sid
    finally:
        rt.close()


def test_skill_loader(cfg: Config) -> None:
    rt = build_runtime(cfg.workspace)
    try:
        names = [skill.name for skill in rt.skills.scan()]
        assert "weekly-report" in names
        body = rt.skills.read("weekly-report")
        assert body and "周报生成" in body
        assert "weekly-report" in rt.skills.index_prompt()
    finally:
        rt.close()


def test_html_to_text() -> None:
    html = "<html><head><style>a{}</style></head><body><h1>标题</h1><p>正文</p><script>x()</script></body></html>"
    text = html_to_text(html)
    assert "标题" in text and "正文" in text
    assert "x()" not in text and "a{}" not in text


def test_events_shape() -> None:
    event = ev.tool_result("id1", "fs.read_file", "hello")
    payload = event.to_dict()
    assert payload["type"] == "tool_result"
    assert payload["id"] == "id1"
    assert payload["is_error"] is False


def test_registry_rejects_anonymous_tool() -> None:
    class Nameless:
        name = ""
        description = ""
        parameters = {}

    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register(Nameless())  # type: ignore[arg-type]


def test_tool_result_truncation() -> None:
    from ohopilot.tools.base import _truncate

    big = "x" * 50_000
    out = _truncate(big)
    assert len(out) < len(big)
    assert "已省略" in out


def test_empty_registry_describe() -> None:
    assert ToolRegistry().describe() == "[]"


def test_tool_result_helpers() -> None:
    assert ToolResult.ok("a").is_error is False
    assert ToolResult.fail("b").is_error is True
