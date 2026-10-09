"""记忆层。

两级就够，别一上来就上向量库：
  1. 会话历史 —— SQLite，支持超长自动压缩
  2. 长期记忆 —— ~/.ohopilot/MEMORY.md，每次启动整份读进系统提示词
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
"""

#: 超过这个条数就触发压缩，避免上下文无限膨胀
COMPACT_THRESHOLD = 60
KEEP_RECENT = 20


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        cfg.home.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(cfg.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---------- 会话 ----------

    def create_session(self, title: str = "") -> str:
        session_id = uuid.uuid4().hex[:12]
        stamp = _now()
        self._conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, stamp, stamp),
        )
        self._conn.commit()
        return session_id

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, title, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_session(self) -> str | None:
        row = self._conn.execute(
            "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None

    def touch_session(self, session_id: str, title: str | None = None) -> None:
        if title:
            self._conn.execute(
                "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                (_now(), title[:80], session_id),
            )
        else:
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id)
            )
        self._conn.commit()

    # ---------- 消息 ----------

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO messages (session_id, role, payload, created_at) VALUES (?, ?, ?, ?)",
            (session_id, message.get("role", "?"), json.dumps(message, ensure_ascii=False), _now()),
        )
        self._conn.commit()

    def history(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def needs_compaction(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return bool(row and row["n"] > COMPACT_THRESHOLD)

    def compact(self, session_id: str, summary: str) -> None:
        """把较早的对话折叠成一条摘要，只保留最近若干轮。

        摘要由模型生成（见 Agent._maybe_compact），这里只负责落库。
        """
        rows = self._conn.execute(
            "SELECT id, payload FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        if len(rows) <= KEEP_RECENT:
            return
        cutoff = rows[-KEEP_RECENT]["id"]
        keep = [json.loads(r["payload"]) for r in rows if r["id"] >= cutoff]

        self._conn.execute(
            "DELETE FROM messages WHERE session_id = ? AND id < ?", (session_id, cutoff)
        )
        summary_message = {
            "role": "user",
            "content": f"[此前对话摘要]\n{summary}\n[摘要结束，以下是最近的对话]",
        }
        self._conn.execute(
            "INSERT INTO messages (session_id, role, payload, created_at) VALUES (?, ?, ?, ?)",
            (session_id, "user", json.dumps(summary_message, ensure_ascii=False), _now()),
        )
        for message in keep:
            self._conn.execute(
                "INSERT INTO messages (session_id, role, payload, created_at) VALUES (?, ?, ?, ?)",
                (session_id, message.get("role", "?"), json.dumps(message, ensure_ascii=False), _now()),
            )
        self._conn.commit()

    # ---------- 长期记忆 ----------

    def read_long_term(self) -> str:
        if not self.cfg.memory_file.exists():
            return ""
        return self.cfg.memory_file.read_text(encoding="utf-8").strip()

    def write_long_term(self, content: str) -> Path:
        self.cfg.memory_file.write_text(content, encoding="utf-8")
        return self.cfg.memory_file

    def close(self) -> None:
        self._conn.close()
