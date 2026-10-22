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

        先按 id 倒序取最近 limit 条、再反转回正序——
        这样上下文永远不会超过 limit 条，聊得再久也不会撑爆 token。
        """
        rows = self._conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [{"role": role, "content": content} for role, content in reversed(rows)]

    def close(self) -> None:
        self._conn.close()
