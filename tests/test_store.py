"""会话存储的行为约定。

这一层最容易被当成「一个 INSERT 而已」，所以重点测那些一旦写错
就会静默出问题的边界：回放顺序、条数上限、摘要覆盖点。
"""

from __future__ import annotations

import sqlite3


def _fill(store, session_id: str, count: int, role: str = "user") -> None:
    for i in range(count):
        store.append(session_id, role, f"消息{i}")


def _ids(store, session_id: str) -> list[int]:
    return [row[0] for row in store.fetch_after(session_id, 0, 1000)]


def test_每次追加都立刻落库(cfg):
    """进程崩掉最多丢当前半句——所以 append 必须自带 commit。"""
    from ohopilot.memory.store import SessionStore

    store = SessionStore(cfg.db_path)
    store.append("s1", "user", "你好")

    # 另开一个连接直接读文件：能读到，说明 commit 过，不是躺在事务里
    other = sqlite3.connect(cfg.db_path)
    rows = other.execute("SELECT role, content FROM messages").fetchall()
    other.close()
    store.close()

    assert rows == [("user", "你好")]


def test_回放保持正序(store):
    _fill(store, "s1", 3)
    history = store.load_history("s1")
    assert [m["content"] for m in history] == ["消息0", "消息1", "消息2"]
    assert all(m["role"] == "user" for m in history)


def test_回放只给最近_limit_条且仍是正序(store):
    """取最近 N 条再反转：上下文既不会无限长，顺序也不会被弄反。"""
    _fill(store, "s1", 10)
    history = store.load_history("s1", limit=4)
    assert [m["content"] for m in history] == ["消息6", "消息7", "消息8", "消息9"]


def test_不同会话互不串味(store):
    store.append("s1", "user", "属于一")
    store.append("s2", "user", "属于二")
    assert [m["content"] for m in store.load_history("s1")] == ["属于一"]
    assert [m["content"] for m in store.load_history("s2")] == ["属于二"]


def test_空会话回放是空列表(store):
    assert store.load_history("不存在") == []


def test_latest_session_取最近活跃的(store):
    assert store.latest_session() is None
    store.append("old", "user", "早")
    store.append("new", "user", "晚")
    assert store.latest_session() == "new"


def test_create_session_是十二位且不重复(store):
    ids = {store.create_session() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) == 12 for i in ids)


def test_没有摘要时_get_summary_返回零和空串(store):
    assert store.get_summary("s1") == (0, "")


def test_摘要覆盖掉的部分不再回放原_文而是打头一条摘要(store):
    _fill(store, "s1", 5)
    ids = _ids(store, "s1")
    store.save_summary("s1", ids[2], "用户想知道存储怎么实现")  # 覆盖前 3 条

    history = store.load_history("s1")

    assert history[0]["content"].startswith("【本次会话早前内容的摘要】")
    assert "存储怎么实现" in history[0]["content"]
    # 只留下 id > upto 的两条原文
    assert [m["content"] for m in history[1:]] == ["消息3", "消息4"]


def test_摘要是_upsert_不会越写越多行(store):
    _fill(store, "s1", 3)
    ids = _ids(store, "s1")
    store.save_summary("s1", ids[0], "第一版")
    store.save_summary("s1", ids[1], "第二版")

    upto, content = store.get_summary("s1")
    assert (upto, content) == (ids[1], "第二版")
    # 摘要覆盖到第 2 条，只剩第 3 条未压缩
    assert store.count_after("s1", upto) == 1
    # 表里只该有一行
    rows = store._conn.execute("SELECT COUNT(*) FROM summaries").fetchone()
    assert rows[0] == 1


def test_count_after_和_fetch_after_只数指定位置之后(store):
    _fill(store, "s1", 4)
    ids = _ids(store, "s1")

    assert store.count_after("s1", 0) == 4
    assert store.count_after("s1", ids[1]) == 2
    assert [r[0] for r in store.fetch_after("s1", ids[1])] == [ids[2], ids[3]]
    # fetch_after 的 limit 限制的是「最早的一批」，用于分批压缩
    assert [r[0] for r in store.fetch_after("s1", 0, limit=2)] == [ids[0], ids[1]]


def test_最近会话列表带标题与条数(store):
    store.append("s1", "user", "第一个问题的标题")
    store.append("s1", "assistant", "回答")
    store.append("s2", "user", "第二个问题")

    sessions = store.recent_sessions()
    by_id = {s["session_id"]: s for s in sessions}

    assert by_id["s1"]["title"] == "第一个问题的标题"  # 取最早那条 user 消息
    assert by_id["s1"]["messages"] == 2
    assert by_id["s2"]["messages"] == 1
    # 按最后活跃时间倒序，最新的 s2 在前
    assert sessions[0]["session_id"] == "s2"


def test_标题会截断并压掉换行(store):
    store.append("s1", "user", "很长的第一句\n带换行" + "啊" * 100)
    title = store.recent_sessions()[0]["title"]
    assert "\n" not in title
    assert len(title) == 60


def test_重复打开同一个库不会清空数据(cfg):
    """CREATE TABLE IF NOT EXISTS 是幂等的：重启进程不该丢历史。"""
    from ohopilot.memory.store import SessionStore

    first = SessionStore(cfg.db_path)
    first.append("s1", "user", "留下的")
    first.close()

    second = SessionStore(cfg.db_path)
    assert [m["content"] for m in second.load_history("s1")] == ["留下的"]
    second.close()
