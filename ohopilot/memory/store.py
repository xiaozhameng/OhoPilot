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
                ctx_tool_results   INTEGER NOT NULL DEFAULT 0
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
                is_error    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._migrate_tool_calls()
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls (session_id)"
        )

    def _migrate_tool_calls(self) -> None:
        """给老库补列：耗时 + 错误样本。探测到有就不动，幂等。

        为什么不直接写进 CREATE TABLE：老用户的 db 文件已经存在，
        IF NOT EXISTS 不会重建表，新列必须用 ALTER TABLE 补。
        """
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
        if "duration_ms" not in cols:
            self._conn.execute("ALTER TABLE tool_calls ADD COLUMN duration_ms INTEGER")
        if "error_excerpt" not in cols:
            self._conn.execute("ALTER TABLE tool_calls ADD COLUMN error_excerpt TEXT")
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
    ) -> None:
        """记录一次模型调用。context 是各类内容的字符数，缺了就记 0。"""
        ctx = context or {}
        self._conn.execute(
            """
            INSERT INTO llm_calls (
                session_id, created_at, round_no, model,
                prompt_tokens, cache_hit_tokens, cache_miss_tokens,
                completion_tokens, total_tokens, duration_ms, first_token_ms,
                ctx_system, ctx_history, ctx_question, ctx_tool_results
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ) -> None:
        """记一次工具调用。runtime 在 tool_result 事件时调用，agent 保持零存储依赖。

        duration_ms / error_excerpt 是控制台「分析优化」的数据源：
        光知道「失败了」没法定位问题，「超时 8 秒」和「参数不匹配」是两种病。
        """
        self._conn.execute(
            """
            INSERT INTO tool_calls
                (session_id, created_at, name, is_error, duration_ms, error_excerpt)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                datetime.now().isoformat(timespec="seconds"),
                name,
                int(is_error),
                duration_ms,
                error_excerpt,
            ),
        )
        self._conn.commit()

    def console_overview(self) -> dict[str, Any]:
        """跨会话聚合的开发者总账。全部一条 SQL 出一列，不循环查。"""
        by_model = [
            {"model": r[0], "calls": r[1], "total_tokens": r[2], "prompt_tokens": r[3], "completion_tokens": r[4]}
            for r in self._conn.execute(
                """
                SELECT model, COUNT(*),
                       COALESCE(SUM(total_tokens), 0),
                       COALESCE(SUM(prompt_tokens), 0),
                       COALESCE(SUM(completion_tokens), 0)
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
            {"session_id": r[0], "llm_calls": r[1], "total_tokens": r[2]}
            for r in self._conn.execute(
                """
                SELECT session_id, COUNT(*), COALESCE(SUM(total_tokens), 0)
                FROM llm_calls GROUP BY session_id ORDER BY SUM(total_tokens) DESC LIMIT 20
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

    def session_detail(self, session_id: str) -> dict[str, Any]:
        """单个会话的下钻明细：逐轮模型调用 + 逐次工具调用。

        控制台点开一个会话后看的就是它——「这个会话为什么烧了 30 万 token」
        能对到具体哪一轮、哪次工具。工具结果不在这（没落库），要看原文去主界面。
        """
        llm = [
            {
                "round_no": r[0],
                "created_at": r[1],
                "model": r[2],
                "prompt_tokens": r[3],
                "completion_tokens": r[4],
                "total_tokens": r[5],
                "duration_ms": r[6],
                "first_token_ms": r[7],
            }
            for r in self._conn.execute(
                """
                SELECT round_no, created_at, model, prompt_tokens,
                       completion_tokens, total_tokens, duration_ms, first_token_ms
                FROM llm_calls WHERE session_id = ? ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        ]
        tools = [
            {
                "created_at": r[0],
                "name": r[1],
                "is_error": bool(r[2]),
                "duration_ms": r[3],
                "error_excerpt": r[4],
            }
            for r in self._conn.execute(
                """
                SELECT created_at, name, is_error, duration_ms, error_excerpt
                FROM tool_calls WHERE session_id = ? ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        ]
        return {"session_id": session_id, "llm_calls": llm, "tool_calls": tools}

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
