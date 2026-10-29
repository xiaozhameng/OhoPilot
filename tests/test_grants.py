"""目录白名单（第 32 步）：一次授权，整个目录不再逐次弹审批。

覆盖四层：
  1. GrantStore 的存取与路径匹配（含符号链接、撤销、坏文件）；
  2. fs_write_file：给候选范围、落规则、之后静默写、但 .env 仍拒；
  3. shell_run：目录内的写命令放行，删除/搬运类永远弹审批；
  4. 主循环：approver 返回的 range 被转成规则，第二次调用不再问。
"""

from __future__ import annotations

import json
from pathlib import Path

from ohopilot.core import agent as agent_mod
from ohopilot.core.agent import Agent, ApprovalDecision
from ohopilot.core.llm import AssistantTurn, ToolCall
from ohopilot.tools.base import ApprovalScope, Tool, ToolRegistry, ToolResult
from ohopilot.tools.fs import WriteFileTool
from ohopilot.tools.grants import GrantStore
from ohopilot.tools.shell import ShellTool


# ---------- GrantStore ----------

def test_授权目录后其下所有文件都被覆盖(cfg, tmp_path):
    project = tmp_path / "desktop" / "proj"
    project.mkdir(parents=True)
    grants = GrantStore(cfg.home)

    grant = grants.add("fs_write_file", project, "dir", label="proj")

    assert grant.path == str(project)
    assert grants.covers("fs_write_file", project / "a.md")
    assert grants.covers("fs_write_file", project / "sub" / "deep" / "b.md")
    # 目录本身也算覆盖（写目录本身不会发生，但判断要一致）
    assert grants.covers("fs_write_file", project)
    # 别的工具不共享这条规则
    assert not grants.covers("shell_run", project / "a.md")
    # 相邻目录不在范围内
    assert not grants.covers("fs_write_file", tmp_path / "desktop" / "other" / "a.md")


def test_file_范围只放行那一个文件(cfg, tmp_path):
    target = tmp_path / "one.txt"
    grants = GrantStore(cfg.home)
    grants.add("fs_write_file", target, "file")

    assert grants.covers("fs_write_file", target)
    assert not grants.covers("fs_write_file", tmp_path / "two.txt")


def test_规则落盘可撤销(cfg, tmp_path):
    grants = GrantStore(cfg.home)
    grant = grants.add("fs_write_file", tmp_path, "dir")

    raw = json.loads(grants.file.read_text(encoding="utf-8"))
    assert raw["grants"][0]["id"] == grant.id

    assert grants.remove(grant.id) is True
    assert grants.remove(grant.id) is False  # 再删一次 = 没有这条
    assert grants.list() == []


def test_重复授权同一路径不堆重复规则(cfg, tmp_path):
    grants = GrantStore(cfg.home)
    first = grants.add("fs_write_file", tmp_path, "dir")
    second = grants.add("fs_write_file", tmp_path, "dir")

    assert first.id == second.id
    assert len(grants.list()) == 1


def test_授权文件被手改坏时按没有规则处理(cfg, tmp_path):
    """坏 JSON 不该让所有工具崩，也不该被当成"全部放行"。"""
    grants = GrantStore(cfg.home)
    grants.file.write_text("{ 这不是 json", encoding="utf-8")

    assert grants.list() == []
    assert grants.covers("fs_write_file", tmp_path / "a.txt") is False


def test_covers_all_空列表不算覆盖(cfg):
    """没有目标 = 无从判断，不能默认放行。"""
    assert GrantStore(cfg.home).covers_all("shell_run", []) is False


# ---------- fs_write_file ----------

async def test_写文件给出多级目录候选(cfg, tmp_path):
    project = tmp_path / "desktop" / "proj"
    project.mkdir(parents=True)
    tool = WriteFileTool(cfg)

    options = tool.approval_options({"path": str(project / "a.md"), "content": ""})

    assert options[0].kind == "once"
    dirs = [Path(o.path) for o in options[1:]]
    # 第一级是文件所在目录，第二级是它的上一层
    assert dirs[0] == project
    assert dirs[1] == project.parent
    assert all(o.kind == "dir" for o in options[1:])


async def test_授权目录后不再审批且真的写进去(cfg, tmp_path):
    project = tmp_path / "desktop" / "proj"
    project.mkdir(parents=True)
    tool = WriteFileTool(cfg)
    target = project / "notes.md"

    assert tool.needs_approval({"path": str(target), "content": ""})[0] is True

    note = tool.apply_grant("dir", str(project))
    assert "白名单" in note

    assert tool.needs_approval({"path": str(target), "content": ""})[0] is False
    assert tool.needs_approval({"path": str(project / "b.txt"), "content": ""})[0] is False

    result = await tool.run(path=str(target), content="内容")
    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "内容"


async def test_授权整个目录也不放行_env(cfg, tmp_path):
    """第 2 条防线永远优先于白名单：授权一个仓库不等于交出里面的密钥。"""
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".env").write_text("SECRET=1", encoding="utf-8")
    tool = WriteFileTool(cfg)
    tool.apply_grant("dir", str(project))

    need, _ = tool.needs_approval({"path": str(project / ".env"), "content": "x"})
    assert need is False  # 不问——因为 run 会直接拒
    result = await tool.run(path=str(project / ".env"), content="被覆盖")
    assert result.is_error
    assert (project / ".env").read_text(encoding="utf-8") == "SECRET=1"


# ---------- shell_run ----------

def test_shell_授权目录后同目录写命令不再问(cfg, tmp_path):
    project = tmp_path / "desktop" / "proj"
    project.mkdir(parents=True)
    tool = ShellTool(cfg)
    tool.apply_grant("dir", str(project))

    need, _ = tool.needs_approval({"command": f"cat > {project}/a.md"})
    assert need is False
    need, _ = tool.needs_approval({"command": f"mkdir -p {project}/sub && touch {project}/sub/b.txt"})
    assert need is False


def test_shell_删除和搬运永远要审批(cfg, tmp_path):
    """目录授权只覆盖「在这里新建/修改」，不覆盖「让已有文件消失」。"""
    project = tmp_path / "proj"
    project.mkdir()
    tool = ShellTool(cfg)
    tool.apply_grant("dir", str(project))

    for command in (
        f"rm -rf {project}/sub",
        f"mv {project}/a.md {project}/b.md",
        f"chmod 777 {project}/a.md",
        f"find {project} -name '*.tmp' -delete",
    ):
        need, reason = tool.needs_approval({"command": command})
        assert need is True, command
        assert reason


def test_shell_命令里有一个路径在授权范围外仍要问(cfg, tmp_path):
    granted = tmp_path / "granted"
    other = tmp_path / "other"
    granted.mkdir()
    other.mkdir()
    tool = ShellTool(cfg)
    tool.apply_grant("dir", str(granted))

    need, _ = tool.needs_approval({"command": f"cat {granted}/a.md > {other}/b.md"})
    assert need is True


def test_shell_没有路径参数的命令不走白名单(cfg):
    """pip install 这种命令没有路径可授权，老老实实问。"""
    tool = ShellTool(cfg)
    tool.apply_grant("dir", str(cfg.workspace))
    need, _ = tool.needs_approval({"command": "pip install requests"})
    assert need is True


def test_shell_候选范围取所有目标的公共目录(cfg, tmp_path):
    project = tmp_path / "proj"
    (project / "a").mkdir(parents=True)
    (project / "b").mkdir(parents=True)
    tool = ShellTool(cfg)

    options = tool.approval_options(
        {"command": f"cp {project}/a/x.md {project}/b/y.md"}
    )
    dirs = [o.path for o in options if o.kind == "dir"]
    assert dirs[0] == str(project)


# ---------- 主循环：范围真的落到工具上 ----------

class RangeTool(Tool):
    """最小号的"可授权"工具：授权后不再问。"""

    name = "range_tool"
    description = "测试用"

    def __init__(self) -> None:
        self.calls = 0
        self.rule: tuple[str, str] | None = None

    def needs_approval(self, arguments):
        if self.rule:
            return False, ""
        return True, "危险"

    def approval_options(self, arguments):
        return [
            ApprovalScope("once", "仅本次"),
            ApprovalScope("dir", "本目录：/tmp/proj", "/tmp/proj"),
        ]

    def apply_grant(self, kind: str, path: str) -> str:
        self.rule = (kind, path)
        return f"已记住 {path}"

    async def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult.ok("执行了")


async def test_选了目录范围后规则落在工具上且第二次不再问(cfg, monkeypatch):
    tool = RangeTool()
    call = ToolCall(id="c1", name="range_tool", arguments={})
    script = [
        AssistantTurn(text="", tool_calls=[call], prompt_tokens=1, completion_tokens=1, total_tokens=2),
        AssistantTurn(text="", tool_calls=[call], prompt_tokens=1, completion_tokens=1, total_tokens=2),
        AssistantTurn(text="好的", tool_calls=[], prompt_tokens=1, completion_tokens=1, total_tokens=2),
    ]

    async def fake_stream(cfg, messages, tools=None):
        turn = script.pop(0)
        if turn.text:
            yield "text", turn.text
        yield "turn", turn

    monkeypatch.setattr(agent_mod, "stream_chat", fake_stream)

    asked: list[str] = []

    async def approver(call_id, name, arguments, reason):
        asked.append(call_id)
        return ApprovalDecision(allow=True, kind="dir", path="/tmp/proj")

    agent = Agent(cfg, ToolRegistry([tool]), approver=approver)
    events = [e async for e in agent.run("试试")]

    assert tool.rule == ("dir", "/tmp/proj")
    assert tool.calls == 2
    assert asked == ["c1"]  # 只问了一次
    assert any(e.type == "notice" and "已记住" in e.data["message"] for e in events)
