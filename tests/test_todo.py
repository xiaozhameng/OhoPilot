"""任务清单：store 行为 + 工具行为。

纪律沿用 conftest：库文件用 tmp_path，不碰 ~/.ohopilot，不发真网。
"""
import json

import pytest

from ohopilot.memory.todo import TodoStore
from ohopilot.tools.todo import build_todo_tools


@pytest.fixture
def store(tmp_path):
    s = TodoStore(tmp_path / "test.db")
    s.set_session("sess-1")
    yield s
    s.close()


@pytest.fixture
def tools(store):
    write, update = build_todo_tools(store)
    return write, update


# ---------- TodoStore ----------

async def test_replace_all_creates_pending_items_in_order(store):
    items = store.replace_all(["读取 CSV", "汇总数据", "生成报告"])
    assert [i["position"] for i in items] == [0, 1, 2]
    assert all(i["status"] == "pending" for i in items)
    assert [i["content"] for i in items] == ["读取 CSV", "汇总数据", "生成报告"]


async def test_replace_all_rejects_empty(store):
    with pytest.raises(ValueError, match="不能为空"):
        store.replace_all([])


async def test_replace_all_rejects_blank_item(store):
    with pytest.raises(ValueError, match="第 2 条"):
        store.replace_all(["有内容", "   "])


async def test_update_status_and_content(store):
    store.replace_all(["a", "b"])
    items = store.update(2, status="done", content="b 改了")
    assert items[1]["status"] == "done"
    assert items[1]["content"] == "b 改了"


async def test_update_requires_something_to_change(store):
    store.replace_all(["a"])
    with pytest.raises(ValueError, match="至少给一个"):
        store.update(1)


async def test_update_rejects_bad_status(store):
    store.replace_all(["a"])
    with pytest.raises(ValueError, match="pending / in_progress / done"):
        store.update(1, status="finished")


async def test_update_missing_id_raises_keyerror(store):
    store.replace_all(["a"])
    with pytest.raises(KeyError):
        store.update(999, status="done")


async def test_id_stays_stable_across_rewrite(store):
    """重写整表后 id 从头编号——旧行删除、新行新建。
    钉这个行为是因为工具返回告诉模型 id，模型靠它引用；
    行为变了测试会先炸，提醒我们改工具描述。"""
    first = store.replace_all(["a", "b"])
    second = store.replace_all(["c"])
    assert [i["id"] for i in first] == [1, 2]
    assert [i["id"] for i in second] == [3]
    assert second[0]["position"] == 0


async def test_sessions_are_isolated(tmp_path):
    s = TodoStore(tmp_path / "test.db")
    s.set_session("sess-A")
    s.replace_all(["A 的任务"])
    s.set_session("sess-B")
    assert s.list_items() == []
    s.replace_all(["B 的任务"])
    s.set_session("sess-A")
    assert [i["content"] for i in s.list_items()] == ["A 的任务"]
    s.close()


async def test_unbound_session_raises(tmp_path):
    s = TodoStore(tmp_path / "test.db")
    with pytest.raises(RuntimeError, match="set_session"):
        s.list_items()
    s.close()


# ---------- 工具 ----------

async def test_write_tool_returns_full_snapshot(tools):
    write, _ = tools
    result = await write.safe_run({"items": ["第一步", "第二步"]})
    assert not result.is_error
    assert "共 2 条" in result.content
    snapshot = json.loads(result.content.split("：\n", 1)[1])
    assert [i["content"] for i in snapshot] == ["第一步", "第二步"]


async def test_write_tool_rejects_non_list(tools):
    write, _ = tools
    result = await write.safe_run({"items": "不是数组"})
    assert result.is_error
    assert "字符串数组" in result.content


async def test_write_tool_surfaces_store_errors_with_remedy(tools):
    write, _ = tools
    result = await write.safe_run({"items": ["有内容", ""]})
    assert result.is_error
    assert "重新调用 todo_write" in result.content


async def test_update_tool_happy_path(tools):
    write, update = tools
    await write.safe_run({"items": ["任务一", "任务二"]})
    result = await update.safe_run({"id": 1, "status": "in_progress"})
    assert not result.is_error
    assert '"status": "in_progress"' in result.content


async def test_update_tool_rejects_done_to_pending(tools):
    write, update = tools
    await write.safe_run({"items": ["任务一"]})
    await update.safe_run({"id": 1, "status": "done"})
    result = await update.safe_run({"id": 1, "status": "pending"})
    assert result.is_error
    assert "重写清单" in result.content


async def test_update_tool_missing_id_gives_remedy(tools):
    write, update = tools
    await write.safe_run({"items": ["任务一"]})
    result = await update.safe_run({"id": 42, "status": "done"})
    assert result.is_error
    assert "正确的 id" in result.content


async def test_update_tool_rejects_non_int_id(tools):
    write, update = tools
    await write.safe_run({"items": ["任务一"]})
    result = await update.safe_run({"id": "1", "status": "done"})
    assert result.is_error
    assert "整数" in result.content
