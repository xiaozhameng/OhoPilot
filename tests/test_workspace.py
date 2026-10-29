"""第 39 步：工作目录按会话切换。

这一批测试钉住三件事，任何一件坏掉都会表现成"文件莫名写到别处"：

1. **默认行为不变**：没人指定目录时，仍然用 cfg.workspace。
2. **切了之后全局一致**：文件工具的路径边界、shell 的判断根、系统提示里
   告诉模型的目录，用的是同一个值（否则模型会按 A 目录拼路径、被 B 目录拒掉）。
3. **并发不串**：两个会话同时跑，各自用自己的目录——这是选 ContextVar
   而不是改 cfg.workspace 的唯一理由，必须有用例守着。
"""
from __future__ import annotations

import asyncio

from ohopilot.core.agent import _system_prompt
from ohopilot.runtime import Runtime
from ohopilot.tools.fs import ListDirTool, ReadFileTool
from ohopilot.workspace import active_workspace, use_workspace


def test_没指定时用配置里的默认目录(cfg, workspace):
    assert active_workspace(cfg) == workspace


def test_with块内切目录_出来还原(cfg, tmp_path):
    other = tmp_path / "other"
    other.mkdir()

    with use_workspace(other):
        assert active_workspace(cfg) == other

    assert active_workspace(cfg) == cfg.workspace


def test_传None等于不切(cfg):
    with use_workspace(None):
        assert active_workspace(cfg) == cfg.workspace


def test_块内抛异常也要还原(cfg, tmp_path):
    """不还原的后果：同一个任务里后续操作继续用上一个目录，且没有任何报错。"""
    other = tmp_path / "other"
    other.mkdir()

    try:
        with use_workspace(other):
            raise RuntimeError("炸了")
    except RuntimeError:
        pass

    assert active_workspace(cfg) == cfg.workspace


async def test_并发两个会话各用各的目录(cfg, tmp_path):
    """两条并发任务同时切目录，各自 sleep 一下制造交错。

    这个用例就是"为什么不能直接改 cfg.workspace"的证据：
    改共享值的话后跑的会覆盖先跑的，断言立刻红。
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    async def job(path):
        with use_workspace(path):
            await asyncio.sleep(0.01)  # 故意让出控制权，制造交错
            return active_workspace(cfg)

    got = await asyncio.gather(job(a), job(b))
    assert got == [a, b]


async def test_文件工具按切换后的目录解析相对路径(cfg, tmp_path):
    project = tmp_path / "pay"
    project.mkdir()
    (project / "note.md").write_text("支付项目的说明", encoding="utf-8")

    tool = ReadFileTool(cfg)

    # 默认目录里没有这个文件 → 读不到
    miss = await tool.run(path="note.md")
    assert miss.is_error

    with use_workspace(project):
        hit = await tool.run(path="note.md")
        assert not hit.is_error
        assert "支付项目的说明" in hit.content


async def test_切目录后旧目录变成越界(cfg, workspace, tmp_path):
    """边界跟着目录走：切到 /pay 之后，原来那个工作目录不再是"内部范围"。

    用 fs_list_dir 断言：读文件（fs_read_file）本来就允许读任意位置，
    真正按工作目录卡边界的是列目录/搜索这类"按范围操作"的工具。
    """
    project = tmp_path / "pay"
    project.mkdir()

    tool = ListDirTool(cfg)
    with use_workspace(project):
        result = await tool.run(path=str(workspace))
    assert result.is_error
    assert "工作目录" in result.content


def test_系统提示里的目录跟着会话走(cfg, tmp_path):
    """模型看到的目录必须和工具校验的一致，否则它会拼出"合法但被拒"的路径。"""
    project = tmp_path / "pay"
    project.mkdir()

    assert str(cfg.workspace) in _system_prompt(cfg, [])
    with use_workspace(project):
        prompt = _system_prompt(cfg, [])
    assert str(project) in prompt
    assert str(cfg.workspace) not in prompt


def test_会话工作目录来自它所在的空间(cfg, store, tmp_path):
    project = tmp_path / "pay"
    project.mkdir()
    space = store.create_space("支付项目", str(project))

    session_id = store.create_session()
    store.append(session_id, "user", "你好")

    # 没搬进空间时：不切（用默认值）
    assert store.session_folder(session_id) == ""

    store.set_session_space(session_id, space["id"])
    assert store.session_folder(session_id) == str(project)


def test_绑定目录不存在时降级到默认目录并给提示(cfg, store, tmp_path):
    """目录被删/外接盘没挂上都是日常情况：这一轮降级继续，但要明说。"""
    gone = tmp_path / "gone"
    gone.mkdir()
    space = store.create_space("临时", str(gone))
    session_id = store.create_session()
    store.set_session_space(session_id, space["id"])

    rt = Runtime.create(cfg=cfg)
    try:
        path, note = rt.session_workspace(session_id)
        assert path == gone.resolve()
        assert note == ""

        gone.rmdir()  # 目录没了
        path, note = rt.session_workspace(session_id)
        assert path is None
        assert "不存在" in note
    finally:
        rt.close()
