"""文件工具的边界。

三层防线（越界 / 硬禁止 / 审批）里，前两层是代码硬挡的，必须逐条验证；
第三层只是标记，交给 Agent 去问人，这里只验证标记是否正确。
"""

from __future__ import annotations

import pytest

from ohopilot.tools.base import MAX_RESULT_CHARS, ToolResult
from ohopilot.tools.fs import (
    ListDirTool,
    ReadFileTool,
    SearchTool,
    WriteFileTool,
    build_fs_tools,
)


# ---------- 读 ----------

async def test_读文件带行号和总行数(cfg, writer):
    writer("note.txt", "第一行\n第二行\n第三行\n")
    result = await ReadFileTool(cfg).run(path="note.txt")

    assert not result.is_error
    assert "共 3 行" in result.content
    assert "1\t第一行" in result.content
    assert "3\t第三行" in result.content


async def test_读文件支持_offset_和_limit(cfg, writer):
    writer("note.txt", "\n".join(f"l{i}" for i in range(1, 11)))
    result = await ReadFileTool(cfg).run(path="note.txt", offset=3, limit=2)

    assert "3\tl3" in result.content
    assert "4\tl4" in result.content
    assert "l5" not in result.content


async def test_读不存在的文件要给出补救方式(cfg):
    result = await ReadFileTool(cfg).run(path="nope.txt")
    assert result.is_error
    # 只说"不存在"，模型会换个猜的路径再试；给出下一步才不会瞎撞
    assert "fs_search" in result.content


async def test_读目录报错并指向_list_dir(cfg):
    (cfg.workspace / "sub").mkdir()
    result = await ReadFileTool(cfg).run(path="sub")
    assert result.is_error
    assert "fs_list_dir" in result.content


async def test_禁止读_env(cfg, writer):
    """10b 那次 key 泄露就是从这里进的：模型读了 .env，内容进了对话。"""
    writer(".env", "OHOPILOT_API_KEY=sk-leak")
    result = await ReadFileTool(cfg).run(path=".env")

    assert result.is_error
    assert "受保护" in result.content
    assert "sk-leak" not in result.content


async def test_子目录里的_env_也禁止(cfg, writer):
    writer("config/.env", "SECRET=1")
    assert (await ReadFileTool(cfg).run(path="config/.env")).is_error


async def test_env_example_可以读(cfg, writer):
    writer(".env.example", "OHOPILOT_API_KEY=")
    assert not (await ReadFileTool(cfg).run(path=".env.example")).is_error


async def test_读大文件要求分段(cfg):
    big = "x" * 300_000
    (cfg.workspace / "big.txt").write_text(big, encoding="utf-8")
    result = await ReadFileTool(cfg).run(path="big.txt")

    assert result.is_error
    assert "offset" in result.content


# ---------- 写 ----------

async def test_写文件并自动建父目录(cfg):
    result = await WriteFileTool(cfg).run(path="a/b/c.txt", content="内容")
    target = cfg.workspace / "a/b/c.txt"

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "内容"


async def test_写操作标记为需要审批(cfg):
    tool = WriteFileTool(cfg)
    assert tool.requires_approval is True
    assert tool.needs_approval({"path": "x", "content": ""}) == (True, "该操作可能修改你的电脑")


async def test_写出工作目录直接拒绝(cfg, tmp_path):
    outside = tmp_path / "outside.txt"
    result = await WriteFileTool(cfg).run(path=str(outside), content="x")

    assert result.is_error
    assert "超出可写范围" in result.content
    assert not outside.exists()


async def test_写相对路径跳出工作目录也拒绝(cfg):
    result = await WriteFileTool(cfg).run(path="../../evil.txt", content="x")
    assert result.is_error
    assert "超出可写范围" in result.content


async def test_禁止覆盖_env(cfg, writer):
    writer(".env", "原有配置")
    result = await WriteFileTool(cfg).run(path=".env", content="被覆盖了")

    assert result.is_error
    assert (cfg.workspace / ".env").read_text(encoding="utf-8") == "原有配置"


async def test_总开关关闭时一次都不落盘(cfg):
    """只想让它读、不许它写的时候，连工作目录内的文件也不该动。"""
    cfg.allow_write = False
    result = await WriteFileTool(cfg).run(path="a.txt", content="x")

    assert result.is_error
    assert "写操作已被配置关闭" in result.content
    assert not (cfg.workspace / "a.txt").exists()


# ---------- 列目录 ----------

async def test_列目录_目录带斜杠文件带字节数(cfg, writer):
    (cfg.workspace / "sub").mkdir()
    writer("a.txt", "12345")

    result = await ListDirTool(cfg).run(path=".")
    assert not result.is_error
    assert "a.txt  (5 B)" in result.content
    assert "sub/" in result.content


async def test_列目录隐藏受保护项(cfg, writer):
    """只给名字也会诱导模型去读，然后被拒——干脆不列。"""
    writer(".env", "x")
    writer("ok.txt", "1")

    result = await ListDirTool(cfg).run(path=".")
    assert "ok.txt" in result.content
    assert ".env" not in result.content
    assert "已隐藏 1 个受保护项" in result.content


async def test_列目录越界拒绝(cfg):
    result = await ListDirTool(cfg).run(path="/")
    assert result.is_error
    assert "超出工作目录" in result.content


async def test_列目录_limit_生效(cfg, writer):
    for i in range(5):
        writer(f"f{i}.txt")
    result = await ListDirTool(cfg).run(path=".", limit=2)
    assert "显示 2" in result.content


# ---------- 搜索 ----------

async def test_搜索同时匹配文件名和内容(cfg, writer):
    writer("todo.md", "还有一件事")
    writer("main.py", "x = 1  # TODO: 待办\n")

    result = await SearchTool(cfg).run(pattern="todo")
    assert not result.is_error
    assert "todo.md" in result.content
    assert "main.py:1" in result.content


async def test_搜索零命中返回成功(cfg, writer):
    """没找到不是工具坏了。回 error 会让模型反复重试同一个搜索。"""
    writer("a.txt", "无关内容")
    result = await SearchTool(cfg).run(pattern="绝对不存在的词")

    assert result.is_error is False
    assert "没有找到" in result.content


async def test_搜索按后缀过滤(cfg, writer):
    writer("a.py", "needle")
    writer("b.md", "needle")

    result = await SearchTool(cfg).run(pattern="needle", suffix=".py")
    assert "a.py" in result.content
    assert "b.md" not in result.content


async def test_搜索跳过噪声目录(cfg, writer):
    """真实踩过：不加剪枝会扫进 .venv 的 5000+ 文件。"""
    writer(".venv/lib/needle.py", "needle")
    writer("src/needle.py", "needle")

    result = await SearchTool(cfg).run(pattern="needle")
    assert "src/needle.py" in result.content
    assert ".venv" not in result.content


async def test_搜索跳过二进制文件(cfg):
    (cfg.workspace / "bin.dat").write_bytes(b"\x00\x01needle")
    result = await SearchTool(cfg).run(pattern="needle")
    assert result.is_error is False
    assert "bin.dat" not in result.content


async def test_搜索_limit_生效(cfg, writer):
    writer("many.txt", "\n".join("needle" for _ in range(20)))
    result = await SearchTool(cfg).run(pattern="needle", limit=3)
    assert "命中 3 条" in result.content


async def test_空_pattern_报错(cfg):
    result = await SearchTool(cfg).run(pattern="  ")
    assert result.is_error
    assert "不能为空" in result.content


async def test_搜索越界拒绝(cfg):
    result = await SearchTool(cfg).run(pattern="x", path="/etc")
    assert result.is_error
    assert "超出工作目录" in result.content


# ---------- 结果截断与装配 ----------

async def test_工具结果过长会被截断(cfg, writer):
    """一次 ls 大目录就能把上下文撑爆，所以基类必须兜一层。"""
    writer("many.txt", "\n".join(f"行{i}" for i in range(20_000)))
    # 截断发生在 safe_run（基类）里，不是 run 里——走真实调用路径才测得到
    result = await ReadFileTool(cfg).safe_run({"path": "many.txt", "limit": 20_000})

    assert len(result.content) < MAX_RESULT_CHARS + 200
    assert "已省略" in result.content


async def test_装配出四个文件工具(cfg):
    names = [t.name for t in build_fs_tools(cfg)]
    assert names == ["fs_read_file", "fs_write_file", "fs_list_dir", "fs_search"]


@pytest.mark.parametrize("raw", ["a.txt", "./a.txt", "sub/../a.txt"])
async def test_相对路径都落回工作目录(cfg, raw):
    (cfg.workspace / "a.txt").write_text("x", encoding="utf-8")
    assert not (await ReadFileTool(cfg).run(path=raw)).is_error


def test_ToolResult_两个构造器语义清楚():
    assert ToolResult.ok("a").is_error is False
    assert ToolResult.fail("a").is_error is True


# ---------- 写边界：工作目录 ∪ 数据家目录 ----------

async def test_写进数据家目录的子目录允许(cfg):
    """家目录是 agent 自己的地盘（上传文件、整理技能都在这），放行。"""
    result = await WriteFileTool(cfg).run(path=str(cfg.home / "uploads" / "x.txt"), content="hi")
    assert not result.is_error
    assert (cfg.home / "uploads" / "x.txt").read_text() == "hi"


async def test_家目录内部文件拒绝裸写(cfg):
    """数据库和 MEMORY.md 各有专用通道：store 层 / memory_append。
    fs_write_file 拿文本去写二进制库文件必坏，必须拦。"""
    for name in ("ohopilot.db", "ohopilot.db-journal", "MEMORY.md"):
        result = await WriteFileTool(cfg).run(path=str(cfg.home / name), content="x")
        assert result.is_error, name
        assert "memory_append" in result.content or "专用" in result.content


async def test_家目录和工作目录之外仍拒绝(cfg, tmp_path):
    """放开了家目录，不等于全盘可写：桌面、其他仓库照样拒。"""
    outside = tmp_path / "elsewhere" / "a.txt"
    result = await WriteFileTool(cfg).run(path=str(outside), content="x")
    assert result.is_error
    assert "可写范围" in result.content


async def test_工作目录下的同名内部文件不受家目录保护清单影响(cfg):
    """保护清单只对家目录生效：用户工作目录里自己有个 MEMORY.md 是合法文件。"""
    result = await WriteFileTool(cfg).run(path="MEMORY.md", content="用户自己的文件")
    assert not result.is_error
    assert (cfg.workspace / "MEMORY.md").read_text() == "用户自己的文件"
