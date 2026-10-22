"""会话存储：SQLite 落库，进程重启后对话能接上。

设计约定：
1. 只存 user 与最终 assistant 文本。工具往返不存——
   回放一半工具轨迹会产生孤儿 tool 消息（没有配对的 tool_call），API 会直接报错。
2. 存储只属于这一层。agent 不 import 本模块，入口（cli / 将来的 server）负责调用。
3. 建表语句放在 __init__ 里，幂等（IF NOT EXISTS）：打开即用，不需要独立初始化脚本。
"""
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class SessionStore:
    def __init__(self, db_path: Path):
        # db 在 ~/.ohopilot/ 下，父目录理论上已由 ensure_dirs() 建好；
        # 这里再兜一层，让本类可以独立于 cli 使用（比如测试里直接实例化）
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # 摘要单独一张表，而不是往 messages 里塞一条"假消息"：
        # 摘要会被反复重写（每次压一批就更新一次），而 messages 是只追加的事实日志。
        # upto_id 记「已压缩到哪条消息」，回放时从它之后开始。
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                session_id TEXT PRIMARY KEY,
                upto_id    INTEGER NOT NULL,
                content    TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def create_session(self) -> str:
        """发一个新会话号。12 位 hex 足够区分会话，又不会在终端里太长。"""
        return uuid.uuid4().hex[:12]

    def latest_session(self) -> str | None:
        """最近活跃的会话号；库里还是空的就返回 None。"""
        row = self._conn.execute(
            "SELECT session_id FROM messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def append(self, session_id: str, role: str, content: str) -> None:
        """追加一条消息并立即提交。每问一句落一条，崩溃最多丢当前半句。"""
        self._conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.now().isoformat(timespec="seconds")),
        )
        self._conn.commit()

    def load_history(self, session_id: str, limit: int = 200) -> list[dict[str, str]]:
        """回放成一个对话可用的 messages 列表。

        已被摘要覆盖的部分不再回放原文，改用一条「摘要」消息打头；
        剩下的按 id 倒序取最近 limit 条、再反转回正序——
        这样上下文永远不会超过 limit 条，聊得再久也不会撑爆 token。
        """
        upto, summary = self.get_summary(session_id)
        rows = self._conn.execute(
            "SELECT role, content FROM messages "
            "WHERE session_id = ? AND id > ? ORDER BY id DESC LIMIT ?",
            (session_id, upto, limit),
        ).fetchall()
        history = [{"role": role, "content": content} for role, content in reversed(rows)]
        if summary:
            history.insert(0, {"role": "user", "content": f"【本次会话早前内容的摘要】\n{summary}"})
        return history

    # ---- 摘要：给上下文压缩用（summarize.py 调用） ----

    def get_summary(self, session_id: str) -> tuple[int, str]:
        """返回 (已压缩到哪条消息的 id, 摘要正文)；没有摘要就是 (0, "")。"""
        row = self._conn.execute(
            "SELECT upto_id, content FROM summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
        return (row[0], row[1]) if row else (0, "")

    def save_summary(self, session_id: str, upto_id: int, content: str) -> None:
        """写入/覆盖本会话的摘要。用 upsert：会话第一次摘要是 INSERT，之后都是 UPDATE。"""
        self._conn.execute(
            "INSERT INTO summaries (session_id, upto_id, content, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "upto_id = excluded.upto_id, content = excluded.content, updated_at = excluded.updated_at",
            (session_id, upto_id, content, datetime.now().isoformat(timespec="seconds")),
        )
        self._conn.commit()

    def count_after(self, session_id: str, after_id: int = 0) -> int:
        """统计某条消息之后还有多少条未压缩的消息。"""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND id > ?",
            (session_id, after_id),
        ).fetchone()
        return int(row[0])

    def fetch_after(
        self, session_id: str, after_id: int = 0, limit: int = 100
    ) -> list[tuple[int, str, str]]:
        """取未压缩的最早 limit 条，返回 [(id, role, content)]，按时间正序。"""
        rows = self._conn.execute(
            "SELECT id, role, content FROM messages "
            "WHERE session_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (session_id, after_id, limit),
        ).fetchall()
        return [(int(r[0]), r[1], r[2]) for r in rows]

    def recent_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """最近的会话列表：会话号 / 最后一条消息时间 / 消息条数 / 首句。

        给服务端的会话列表用（将来桌面端、手机端的首页就是它）。
        首句取该会话最早那条 user 消息，当标题最直观。
        """
        rows = self._conn.execute(
            "SELECT session_id, MAX(created_at) AS last_at, COUNT(*) AS n "
            "FROM messages GROUP BY session_id ORDER BY last_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

        sessions: list[dict[str, Any]] = []
        for session_id, last_at, count in rows:
            first = self._conn.execute(
                "SELECT content FROM messages WHERE session_id = ? AND role = 'user' "
                "ORDER BY id ASC LIMIT 1",
                (session_id,),
            ).fetchone()
            title = (first[0] if first else "")[:60].replace("\n", " ")
            sessions.append(
                {
                    "session_id": session_id,
                    "title": title,
                    "updated_at": last_at,
                    "messages": int(count),
                }
            )
        return sessions

    def close(self) -> None:
        self._conn.close()
