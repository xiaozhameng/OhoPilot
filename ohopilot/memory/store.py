"""会话存储：SQLite 落库，进程重启后对话能接上。

设计约定：
1. 只存 user 与最终 assistant 文本。工具往返不存——
   回放一半工具轨迹会产生孤儿 tool 消息（没有配对的 tool_call），API 会直接报错。
2. 存储只属于这一层。agent 不 import 本模块，入口（cli / 将来的 server）负责调用。
3. 建表语句放在 __init__ 里，幂等（IF NOT EXISTS）：打开即用，不需要独立初始化脚本。
"""
import json
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
        # 会话元信息：目前只有别名，将来要加"置顶/归档/标签"也放这张表。
        # 为什么不把 alias 塞进 messages：messages 是只追加的事实日志，
        # 别名会被反复改，混进去就再也分不清"当时说了什么"和"现在叫什么"。
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                alias      TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # 空间：会话的一级分组（第 34 步）。一个空间对应一件事/一个项目，
        # 可绑定一个本地文件夹（元数据；工作目录切换是后续独立改造）。
        # id=1 是默认空间：不选空间的会话都落这里，兜底、不可删。
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spaces (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                folder     TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO spaces (id, name, created_at) VALUES (1, '默认空间', '')"
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
        # 每次模型调用一行。和 messages 一样是只追加的事实日志：
        # 优化 prompt、换模型、调工具裁剪之后，回头对比这张表就知道有没有效。
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_calls (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id         TEXT NOT NULL,
                created_at         TEXT NOT NULL,
                round_no           INTEGER NOT NULL DEFAULT 0,
                model              TEXT NOT NULL DEFAULT '',
                prompt_tokens      INTEGER NOT NULL DEFAULT 0,
                cache_hit_tokens   INTEGER NOT NULL DEFAULT 0,
                cache_miss_tokens  INTEGER NOT NULL DEFAULT 0,
                completion_tokens  INTEGER NOT NULL DEFAULT 0,
                total_tokens       INTEGER NOT NULL DEFAULT 0,
                duration_ms        INTEGER NOT NULL DEFAULT 0,
                first_token_ms     INTEGER NOT NULL DEFAULT 0,
                ctx_system         INTEGER NOT NULL DEFAULT 0,
                ctx_history        INTEGER NOT NULL DEFAULT 0,
                ctx_question       INTEGER NOT NULL DEFAULT 0,
                ctx_tool_results   INTEGER NOT NULL DEFAULT 0,
                finish_reason      TEXT NOT NULL DEFAULT '',
                reasoning_tokens   INTEGER NOT NULL DEFAULT 0,
                req_messages       INTEGER NOT NULL DEFAULT 0,
                req_tools          INTEGER NOT NULL DEFAULT 0,
                req_snapshot       TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls (session_id)"
        )
        # 工具调用流水（第 26 步控制台用）。只记事实：谁、成功没——
        # 参数和结果不落库（messages 里已有对话上下文，这里只做统计）
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_calls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                name        TEXT NOT NULL,
                is_error    INTEGER NOT NULL DEFAULT 0,
                round_no    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._migrate_columns()
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls (session_id)"
        )

    def _migrate_columns(self) -> None:
        """给老库补列：探测到有就不动，幂等。

        为什么不直接写进 CREATE TABLE：老用户的 db 文件已经存在，
        IF NOT EXISTS 不会重建表，新列必须用 ALTER TABLE 补。
        """
        plans: dict[str, dict[str, str]] = {
            "tool_calls": {
                "duration_ms": "INTEGER",
                "error_excerpt": "TEXT",
                "round_no": "INTEGER NOT NULL DEFAULT 0",
            },
            "llm_calls": {
                "finish_reason": "TEXT NOT NULL DEFAULT ''",
                "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
                "req_messages": "INTEGER NOT NULL DEFAULT 0",
                "req_tools": "INTEGER NOT NULL DEFAULT 0",
                "req_snapshot": "TEXT",
            },
            "sessions": {
                "space_id": "INTEGER NOT NULL DEFAULT 1",
            },
        }
        for table, cols in plans.items():
            existing = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, decl in cols.items():
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
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

    # ---- 会话元信息：别名 ----

    def set_alias(self, session_id: str, alias: str) -> str:
        """改会话别名，返回落库后的值。

        空串 = 清空别名（界面回退到"首个问题当标题"）。
        折叠连续空白并限长 60：别名要出现在侧栏一行里，太长会被截断成乱码。
        """
        clean = " ".join(alias.split())[:60]
        self._conn.execute(
            "INSERT INTO sessions (session_id, alias, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET alias = excluded.alias",
            (session_id, clean, datetime.now().isoformat(timespec="seconds")),
        )
        self._conn.commit()
        return clean

    def get_alias(self, session_id: str) -> str:
        row = self._conn.execute(
            "SELECT alias FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else ""

    # ---- 空间（会话分组，第 34 步）----
    # 默认空间 id=1：不指定空间的会话都归它，删不掉、改不了名。
    # 空间只做「分类 + 记一个本地目录」；目录要不要变成会话的工作目录是
    # 另一件事（涉及工具按会话切 workspace），不在这里偷偷做。

    DEFAULT_SPACE_ID = 1

    def _space_row(self, row: tuple) -> dict[str, Any]:
        return {
            "id": int(row[0]),
            "name": row[1],
            "folder": row[2] or "",
            "created_at": row[3] or "",
            "sessions": int(row[4]) if len(row) > 4 else 0,
        }

    def list_spaces(self) -> list[dict[str, Any]]:
        """全部空间 + 各自会话数。默认空间排最前，其余按创建先后。"""
        rows = self._conn.execute(
            """
            SELECT s.id, s.name, s.folder, s.created_at,
                   (SELECT COUNT(DISTINCT m.session_id) FROM messages m
                     LEFT JOIN sessions x ON x.session_id = m.session_id
                    WHERE COALESCE(x.space_id, 1) = s.id)
            FROM spaces s
            ORDER BY s.id = 1 DESC, s.id ASC
            """
        ).fetchall()
        return [self._space_row(r) for r in rows]

    def create_space(self, name: str, folder: str = "") -> dict[str, Any]:
        """新建空间。重名直接拒绝——侧栏里两个同名空间没法选。"""
        clean = " ".join(name.split())[:40]
        if not clean:
            raise ValueError("空间名字不能为空")
        exists = self._conn.execute(
            "SELECT 1 FROM spaces WHERE name = ?", (clean,)
        ).fetchone()
        if exists:
            raise ValueError(f"已经有叫「{clean}」的空间了")
        cur = self._conn.execute(
            "INSERT INTO spaces (name, folder, created_at) VALUES (?, ?, ?)",
            (clean, folder.strip(), datetime.now().isoformat(timespec="seconds")),
        )
        self._conn.commit()
        return {"id": int(cur.lastrowid), "name": clean, "folder": folder.strip(), "sessions": 0}

    def update_space(
        self, space_id: int, name: str | None = None, folder: str | None = None
    ) -> dict[str, Any]:
        """改名 / 绑目录。默认空间允许绑目录，但名字固定。"""
        row = self._conn.execute(
            "SELECT id, name, folder FROM spaces WHERE id = ?", (space_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"空间不存在：{space_id}")
        if space_id == self.DEFAULT_SPACE_ID and name is not None:
            name = None  # 默认空间改名会让"兜底"失去意义，静默忽略
        if name is not None:
            clean = " ".join(name.split())[:40]
            if not clean:
                raise ValueError("空间名字不能为空")
            conflict = self._conn.execute(
                "SELECT 1 FROM spaces WHERE name = ? AND id != ?", (clean, space_id)
            ).fetchone()
            if conflict:
                raise ValueError(f"已经有叫「{clean}」的空间了")
            self._conn.execute("UPDATE spaces SET name = ? WHERE id = ?", (clean, space_id))
        if folder is not None:
            self._conn.execute(
                "UPDATE spaces SET folder = ? WHERE id = ?", (folder.strip(), space_id)
            )
        self._conn.commit()
        fresh = self._conn.execute(
            "SELECT id, name, folder, created_at FROM spaces WHERE id = ?", (space_id,)
        ).fetchone()
        return self._space_row(fresh)

    def delete_space(self, space_id: int) -> None:
        """删空间：里面的会话退回默认空间。默认空间不可删。"""
        if space_id == self.DEFAULT_SPACE_ID:
            raise ValueError("默认空间不能删除")
        exists = self._conn.execute("SELECT 1 FROM spaces WHERE id = ?", (space_id,)).fetchone()
        if not exists:
            raise KeyError(f"空间不存在：{space_id}")
        # 先搬会话再删空间：反过来的话中间一刻会话指向了不存在的空间
        self._conn.execute(
            "UPDATE sessions SET space_id = ? WHERE space_id = ?",
            (self.DEFAULT_SPACE_ID, space_id),
        )
        self._conn.execute("DELETE FROM spaces WHERE id = ?", (space_id,))
        self._conn.commit()

    def set_session_space(self, session_id: str, space_id: int) -> None:
        """把会话挪到某个空间（会话还没有元信息行时先建一行）。"""
        exists = self._conn.execute("SELECT 1 FROM spaces WHERE id = ?", (space_id,)).fetchone()
        if not exists:
            raise KeyError(f"空间不存在：{space_id}")
        self._conn.execute(
            "INSERT INTO sessions (session_id, alias, created_at, space_id) VALUES (?, '', ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET space_id = excluded.space_id",
            (session_id, datetime.now().isoformat(timespec="seconds"), space_id),
        )
        self._conn.commit()

    def session_meta(self, session_id: str) -> dict[str, Any]:
        """别名 / 首句标题 / 创建时间 / 最后更新时间 / 消息条数。

        创建时间取该会话最早一条消息的时间——不额外存"会话创建时刻"，
        因为消息时间才是事实；空会话（建了没说话）本来就没什么可展示的。
        """
        row = self._conn.execute(
            "SELECT MIN(created_at), MAX(created_at), COUNT(*) "
            "FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        first = self._conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND role = 'user' "
            "ORDER BY id ASC LIMIT 1",
            (session_id,),
        ).fetchone()
        return {
            "session_id": session_id,
            "alias": self.get_alias(session_id),
            "title": (first[0] if first else "")[:60].replace("\n", " "),
            "created_at": row[0] or "",
            "updated_at": row[1] or "",
            "messages": int(row[2]),
        }

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

    # ---- 用量：给开发者面板用（runtime.py 在每轮 usage 事件时调用） ----

    def record_llm_call(
        self,
        session_id: str,
        *,
        round_no: int,
        model: str,
        prompt_tokens: int,
        cache_hit_tokens: int,
        cache_miss_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        duration_ms: int,
        first_token_ms: int,
        context: dict[str, int] | None = None,
        finish_reason: str = "",
        reasoning_tokens: int = 0,
        req_messages: int = 0,
        req_tools: int = 0,
        req_snapshot: str | None = None,
    ) -> None:
        """记录一次模型调用。context 是各类内容的字符数，缺了就记 0。

        请求侧三件套（req_messages/req_tools/req_snapshot）回答「这次到底发了什么」：
        消息条数涨得快 = 历史没压缩；工具定义数 = 系统提示的固定开销；
        快照（截 4KB）留着排查「模型为什么乱调工具」时对原文。
        """
        ctx = context or {}
        self._conn.execute(
            """
            INSERT INTO llm_calls (
                session_id, created_at, round_no, model,
                prompt_tokens, cache_hit_tokens, cache_miss_tokens,
                completion_tokens, total_tokens, duration_ms, first_token_ms,
                ctx_system, ctx_history, ctx_question, ctx_tool_results,
                finish_reason, reasoning_tokens, req_messages, req_tools, req_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                datetime.now().isoformat(timespec="seconds"),
                round_no,
                model,
                prompt_tokens,
                cache_hit_tokens,
                cache_miss_tokens,
                completion_tokens,
                total_tokens,
                duration_ms,
                first_token_ms,
                ctx.get("system", 0),
                ctx.get("history", 0),
                ctx.get("question", 0),
                ctx.get("tool_results", 0),
                finish_reason,
                reasoning_tokens,
                req_messages,
                req_tools,
                req_snapshot,
            ),
        )
        self._conn.commit()

    def llm_calls(self, session_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """用量明细，最新在前。session_id 不传就看全部会话（全局视角）。"""
        if session_id:
            rows = self._conn.execute(
                "SELECT * FROM llm_calls WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = [c[0] for c in self._conn.execute("SELECT * FROM llm_calls LIMIT 0").description]
        return [dict(zip(cols, row)) for row in rows]

    def usage_summary(self, session_id: str | None = None) -> dict[str, Any]:
        """聚合一份总账：调用次数、各类 token 总量、缓存命中率、平均首字延迟。"""
        where = "WHERE session_id = ?" if session_id else ""
        params: tuple[Any, ...] = (session_id,) if session_id else ()
        row = self._conn.execute(
            """
            SELECT COUNT(*),
                   COALESCE(SUM(prompt_tokens), 0),
                   COALESCE(SUM(cache_hit_tokens), 0),
                   COALESCE(SUM(cache_miss_tokens), 0),
                   COALESCE(SUM(completion_tokens), 0),
                   COALESCE(SUM(total_tokens), 0),
                   COALESCE(AVG(NULLIF(first_token_ms, 0)), 0),
                   COALESCE(SUM(duration_ms), 0)
            FROM llm_calls
            """ + where,
            params,
        ).fetchone()
        calls, prompt, hit, miss, completion, total, avg_first, duration = row
        cacheable = int(hit) + int(miss)
        return {
            "calls": int(calls),
            "prompt_tokens": int(prompt),
            "cache_hit_tokens": int(hit),
            "cache_miss_tokens": int(miss),
            "completion_tokens": int(completion),
            "total_tokens": int(total),
            # 命中率只算"可缓存的部分"，completion 不掺进来
            "cache_hit_rate": round(int(hit) / cacheable, 4) if cacheable else 0.0,
            "avg_first_token_ms": round(float(avg_first)),
            "total_duration_ms": int(duration),
        }

    # ---- 控制台聚合（第 26 步）：只读统计，不碰业务表 ----

    def record_tool_call(
        self,
        session_id: str,
        name: str,
        is_error: bool,
        duration_ms: int | None = None,
        error_excerpt: str | None = None,
        round_no: int = 0,
    ) -> None:
        """记一次工具调用。runtime 在 tool_result 事件时调用，agent 保持零存储依赖。

        duration_ms / error_excerpt 是控制台「分析优化」的数据源：
        光知道「失败了」没法定位问题，「超时 8 秒」和「参数不匹配」是两种病。
        round_no 是轮次树的挂载点：没有它，工具调用没法归属到「第几轮」。
        """
        self._conn.execute(
            """
            INSERT INTO tool_calls
                (session_id, created_at, name, is_error, duration_ms, error_excerpt, round_no)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                datetime.now().isoformat(timespec="seconds"),
                name,
                int(is_error),
                duration_ms,
                error_excerpt,
                round_no,
            ),
        )
        self._conn.commit()

    def console_overview(self) -> dict[str, Any]:
        """跨会话聚合的开发者总账。全部一条 SQL 出一列，不循环查。"""
        by_model = [
            {
                "model": r[0],
                "calls": r[1],
                "total_tokens": r[2],
                "prompt_tokens": r[3],
                "completion_tokens": r[4],
                "cache_hit_tokens": r[5],
                "cache_miss_tokens": r[6],
                "reasoning_tokens": r[7],
            }
            for r in self._conn.execute(
                """
                SELECT model, COUNT(*),
                       COALESCE(SUM(total_tokens), 0),
                       COALESCE(SUM(prompt_tokens), 0),
                       COALESCE(SUM(completion_tokens), 0),
                       COALESCE(SUM(cache_hit_tokens), 0),
                       COALESCE(SUM(cache_miss_tokens), 0),
                       COALESCE(SUM(reasoning_tokens), 0)
                FROM llm_calls GROUP BY model ORDER BY SUM(total_tokens) DESC
                """
            ).fetchall()
        ]

        tool_stats = [
            {
                "name": r[0],
                "calls": r[1],
                "errors": r[2],
                # 耗时只有真正执行过的调用才有；None 行 AVG 会自动忽略
                "avg_ms": int(r[3]) if r[3] is not None else None,
                "max_ms": int(r[4]) if r[4] is not None else None,
            }
            for r in self._conn.execute(
                """
                SELECT name, COUNT(*), COALESCE(SUM(is_error), 0),
                       AVG(duration_ms), MAX(duration_ms)
                FROM tool_calls GROUP BY name ORDER BY COUNT(*) DESC LIMIT 20
                """
            ).fetchall()
        ]
        tool_row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(is_error), 0) FROM tool_calls"
        ).fetchone()

        # 近 14 天趋势：控制台从「总账」变「分析」的主视图。
        # created_at 是 ISO 字符串（2026-10-25T22:51:26），substr 前 10 位就是日期，
        # 比 date() 函数省一层转换，也和时区写入库时保持一致
        trend = [
            {"date": r[0], "llm_calls": r[1], "tokens": r[2], "tool_calls": r[3]}
            for r in self._conn.execute(
                """
                SELECT d.date, COALESCE(l.n, 0), COALESCE(l.tok, 0), COALESCE(t.n, 0)
                FROM (
                    -- 先用两条流水表里最近的日期造出连续 14 天的骨架，空日子也占一行
                    WITH RECURSIVE
                    max_day AS (
                        SELECT MAX(substr(created_at, 1, 10)) AS day FROM llm_calls
                        UNION SELECT MAX(substr(created_at, 1, 10)) FROM tool_calls
                    ),
                    span(day, _) AS (
                        SELECT day, 0 FROM max_day WHERE day IS NOT NULL
                        UNION ALL
                        SELECT date(day, '-1 day'), _ + 1 FROM span WHERE _ < 13
                    )
                    SELECT DISTINCT day AS date FROM span ORDER BY day
                ) AS d
                LEFT JOIN (
                    SELECT substr(created_at, 1, 10) AS day,
                           COUNT(*) AS n, SUM(total_tokens) AS tok
                    FROM llm_calls GROUP BY day
                ) AS l ON l.day = d.date
                LEFT JOIN (
                    SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n
                    FROM tool_calls GROUP BY day
                ) AS t ON t.day = d.date
                ORDER BY d.date
                """
            ).fetchall()
        ]

        # 最近的失败样本：控制台排障的第一入口，直接能看到报错原文
        error_samples = [
            {
                "session_id": r[0],
                "created_at": r[1],
                "name": r[2],
                "excerpt": r[3],
            }
            for r in self._conn.execute(
                """
                SELECT session_id, created_at, name, error_excerpt
                FROM tool_calls
                WHERE is_error = 1
                ORDER BY id DESC LIMIT 20
                """
            ).fetchall()
        ]

        by_session = [
            {
                "session_id": r[0],
                "llm_calls": r[1],
                "total_tokens": r[2],
                "prompt_tokens": r[3],
                "completion_tokens": r[4],
                "tool_calls": r[5],
                "alias": r[6] or "",
                # 首句标题：控制台列表里没有别名时用它当主标题
                "title": (r[7] or "")[:60].replace("\n", " "),
                "created_at": r[8] or "",
                "updated_at": r[9] or "",
            }
            for r in self._conn.execute(
                """
                SELECT l.session_id, COUNT(DISTINCT l.id),
                       COALESCE(SUM(l.total_tokens), 0),
                       COALESCE(SUM(l.prompt_tokens), 0),
                       COALESCE(SUM(l.completion_tokens), 0),
                       (SELECT COUNT(*) FROM tool_calls t WHERE t.session_id = l.session_id),
                       COALESCE((SELECT s.alias FROM sessions s
                                 WHERE s.session_id = l.session_id), ''),
                       COALESCE((SELECT m.content FROM messages m
                                 WHERE m.session_id = l.session_id AND m.role = 'user'
                                 ORDER BY m.id ASC LIMIT 1), ''),
                       COALESCE((SELECT MIN(m.created_at) FROM messages m
                                 WHERE m.session_id = l.session_id), ''),
                       COALESCE((SELECT MAX(m.created_at) FROM messages m
                                 WHERE m.session_id = l.session_id), '')
                FROM llm_calls l GROUP BY l.session_id ORDER BY SUM(l.total_tokens) DESC LIMIT 20
                """
            ).fetchall()
        ]

        return {
            "usage": self.usage_summary(None),
            "by_model": by_model,
            "tool_stats": tool_stats,
            "tool_calls_total": int(tool_row[0]),
            "tool_errors_total": int(tool_row[1]),
            "by_session": by_session,
            "trend": trend,
            "error_samples": error_samples,
        }

    @staticmethod
    def _last_user_text(snapshot_json: str | None, limit: int = 160) -> str:
        """从请求快照里解析「这一轮用户问的是什么」。

        为什么放在后端做：前端拿到的是 JSON 字符串，每个轮次都要解析一遍；
        而且超过 4KB 的快照是 {truncated, head, tail} 信封——那是两段残缺文本，
        从里面猜用户消息不可靠，宁可返回空串让前端隐藏这一行。

        多轮会话里每轮请求都带着全部历史，「最后一条 user 消息」恰好就是
        这一轮的新问题；没有 user 消息的轮次（比如纯工具续跑）复用上一轮的，
        也符合"这轮在处理什么"的直觉。
        """
        if not snapshot_json:
            return ""
        try:
            snapshot = json.loads(snapshot_json)
        except (ValueError, TypeError):
            return ""
        if not isinstance(snapshot, list):
            return ""  # 截断信封不是消息数组，解析不出可靠内容
        for message in reversed(snapshot):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                # 多模态消息的 content 是块数组，只取文本块
                content = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            return " ".join(str(content).split())[:limit]
        return ""

    def session_detail(self, session_id: str) -> dict[str, Any]:
        """单个会话的下钻明细：逐轮模型调用 + 逐次工具调用（带轮次）。

        控制台点开一个会话后看的就是它——「这个会话为什么烧了 30 万 token」
        能对到具体哪一轮、哪次工具。工具结果不在这（没落库），要看原文去主界面。
        req_snapshot 不在这：一条 4KB，列表里塞不下也用不着，单条点开时看 llm_call_snapshot。
        但每轮会带上 last_user_text（从快照解析的 160 字摘要）——
        时间线先讲故事（用户问了什么）再看指标（烧了多少 token），
        这是 LangSmith Trajectories 验证过的读法：先定位行为，再查细节。
        """
        llm = [
            {
                "id": r[0],
                "round_no": r[1],
                "created_at": r[2],
                "model": r[3],
                "prompt_tokens": r[4],
                "cache_hit_tokens": r[5],
                "cache_miss_tokens": r[6],
                "completion_tokens": r[7],
                "total_tokens": r[8],
                "duration_ms": r[9],
                "first_token_ms": r[10],
                "finish_reason": r[11],
                "reasoning_tokens": r[12],
                "req_messages": r[13],
                "req_tools": r[14],
                "last_user_text": self._last_user_text(r[15]),
            }
            for r in self._conn.execute(
                """
                SELECT id, round_no, created_at, model, prompt_tokens,
                       cache_hit_tokens, cache_miss_tokens, completion_tokens,
                       total_tokens, duration_ms, first_token_ms,
                       finish_reason, reasoning_tokens, req_messages, req_tools,
                       req_snapshot
                FROM llm_calls WHERE session_id = ? ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        ]
        tools = [
            {
                "round_no": r[0],
                "created_at": r[1],
                "name": r[2],
                "is_error": bool(r[3]),
                "duration_ms": r[4],
                "error_excerpt": r[5],
            }
            for r in self._conn.execute(
                """
                SELECT round_no, created_at, name, is_error, duration_ms, error_excerpt
                FROM tool_calls WHERE session_id = ? ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        ]
        return {"session_id": session_id, **self.session_meta(session_id),
                "llm_calls": llm, "tool_calls": tools}

    def llm_call_snapshot(self, call_id: int) -> dict[str, Any] | None:
        """取一次模型调用的请求体快照（截 4KB 的 messages JSON）。

        列表/树视图不带它——一条 4KB，几十条就把响应撑爆了；
        只在用户点开单条调用看「这次到底发了什么」时按需返回。
        """
        row = self._conn.execute(
            "SELECT id, created_at, model, req_messages, req_tools, req_snapshot "
            "FROM llm_calls WHERE id = ?",
            (call_id,),
        ).fetchone()
        if row is None:
            return None
        snapshot: Any = None
        if row[5]:
            try:
                snapshot = json.loads(row[5])
            except (ValueError, TypeError):
                snapshot = None  # 旧格式/半截快照，宁可给空也不给前端塞坏 JSON
        return {
            "id": int(row[0]),
            "created_at": row[1],
            "model": row[2],
            "req_messages": int(row[3]),
            "req_tools": int(row[4]),
            "snapshot": snapshot,
        }

    def recent_sessions(self, limit: int = 200) -> list[dict[str, Any]]:
        """最近的会话列表：会话号 / 别名 / 首句 / 时间 / 条数 / 所属空间。

        给服务端的会话列表用（桌面端、手机端的首页就是它）。
        一次聚合查完再逐条取首句，而不是每行查三次：
        SQLite 本地读很快，但 N+1 的写法在会话上百条时会明显变慢。

        space_id 用 COALESCE 兜 1：第 34 步之前建的会话元信息行还是 NULL。
        """
        rows = self._conn.execute(
            "SELECT m.session_id, MAX(m.created_at) AS last_at, MIN(m.created_at) AS first_at, "
            "       COUNT(*) AS n, COALESCE(s.alias, ''), COALESCE(s.space_id, 1) "
            "FROM messages m LEFT JOIN sessions s ON s.session_id = m.session_id "
            "GROUP BY m.session_id ORDER BY last_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

        sessions: list[dict[str, Any]] = []
        for session_id, last_at, first_at, count, alias, space_id in rows:
            first = self._conn.execute(
                "SELECT content FROM messages WHERE session_id = ? AND role = 'user' "
                "ORDER BY id ASC LIMIT 1",
                (session_id,),
            ).fetchone()
            title = (first[0] if first else "")[:60].replace("\n", " ")
            sessions.append(
                {
                    "session_id": session_id,
                    "alias": alias or "",
                    "title": title,
                    "created_at": first_at,
                    "updated_at": last_at,
                    "messages": int(count),
                    "space_id": int(space_id),
                }
            )
        return sessions

    def close(self) -> None:
        self._conn.close()
