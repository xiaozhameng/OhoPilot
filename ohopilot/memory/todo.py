"""任务清单存储：SQLite 落库，按会话隔离。

为什么单独一个 store 而不是塞进 SessionStore：
SessionStore 的职责是「对话历史怎么存」，todo 是另一类数据——
它会被整表重写（模型改计划）、被高频单条更新（勾选完成），
读写模式和历史消息完全不同，混在一起两边都会变别扭。

两条身份线索，故意分开：
- id（全局自增，永不复用）：模型引用条目用「3 号」，
  重排/重写都不能让 id 变，否则模型手里的引用会指错条目；
- position（会话内顺序号）：只管展示顺序，重写整表时重排。

状态机：pending -> in_progress -> done，允许跳步（直接 pending -> done），
不允许逆序（done -> pending）由工具层校验——存储层只认合法字符串。
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

VALID_STATUSES = ("pending", "in_progress", "done")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TodoStore:
    def __init__(self, db_path: Path):
        # 与 SessionStore 同一个库文件；SQLite 多连接读写同一文件没问题，
        # 这里写入频率低（写清单/勾选），不需要 WAL。
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                position   INTEGER NOT NULL,
                content    TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        self._session_id: str = ""

    # ---------- 会话绑定 ----------

    def set_session(self, session_id: str) -> None:
        """工具和 store 都是全局单例，但清单按会话隔离——
        所以每轮问答开始时由 Runtime 告诉 store「当前在哪个会话」。
        不设置就调用读写方法会直接报错，宁可炸也不悄悄写串会话。
        """
        self._session_id = session_id

    def _require_session(self) -> str:
        if not self._session_id:
            raise RuntimeError("TodoStore 未绑定会话，先调用 set_session()")
        return self._session_id

    # ---------- 读写 ----------

    def replace_all(self, contents: list[str]) -> list[dict[str, Any]]:
        """整表重写：删掉本会话全部条目，按传入顺序重建（状态全是 pending）。

        「增删改排序」全靠这一个原子操作覆盖——模型改主意时不需要
        逐条删除的接口，重发一遍完整列表最不容易出错。
        """
        session_id = self._require_session()
        if not contents:
            raise ValueError("清单不能为空；要清空清单请逐条 update 成 done，而不是发空列表")
        ts = _now()
        cur = self._conn.cursor()
        cur.execute("DELETE FROM todos WHERE session_id = ?", (session_id,))
        for position, content in enumerate(contents):
            content = content.strip()
            if not content:
                raise ValueError(f"第 {position + 1} 条内容是空的，请补上具体要做什么")
            cur.execute(
                "INSERT INTO todos (session_id, position, content, status, created_at, updated_at)"
                " VALUES (?, ?, ?, 'pending', ?, ?)",
                (session_id, position, content, ts, ts),
            )
        self._conn.commit()
        return self.list_items()

    def update(
        self,
        item_id: int,
        status: str | None = None,
        content: str | None = None,
    ) -> list[dict[str, Any]]:
        """按 id 改一条的状态/内容。id 不存在会抛 KeyError（工具层转成带补救建议的错误）。"""
        session_id = self._require_session()
        if status is None and content is None:
            raise ValueError("status 和 content 至少给一个，不然这次调用什么都没改")
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(
                f"status 只能是 {' / '.join(VALID_STATUSES)}，收到的是 {status!r}"
            )
        row = self._conn.execute(
            "SELECT id FROM todos WHERE id = ? AND session_id = ?",
            (item_id, session_id),
        ).fetchone()
        if row is None:
            raise KeyError(item_id)
        sets, params = [], []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if content is not None:
            content = content.strip()
            if not content:
                raise ValueError("content 不能是空字符串")
            sets.append("content = ?")
            params.append(content)
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(item_id)
        self._conn.execute(f"UPDATE todos SET {', '.join(sets)} WHERE id = ?", params)
        self._conn.commit()
        return self.list_items()

    def list_items(self) -> list[dict[str, Any]]:
        """当前会话的完整清单快照，按 position 排序。事件推送和工具返回都用它。"""
        session_id = self._require_session()
        rows = self._conn.execute(
            "SELECT id, position, content, status FROM todos"
            " WHERE session_id = ? ORDER BY position",
            (session_id,),
        ).fetchall()
        return [
            {"id": r[0], "position": r[1], "content": r[2], "status": r[3]}
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
