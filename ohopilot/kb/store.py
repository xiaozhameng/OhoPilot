""" 向量库：统一接口 + sqlite-vec / Milvus 双实现

为什么接口只有四个方法：知识库的使用面就是这么宽——
加（add）、查（search）、按来源删（delete_by_source）、数数（count）。
接口越小，换后端的成本越低。

sqlite-vec 是 SQLite 扩展（vec0 虚拟表），单文件零服务，默认后端；
Milvus 是独立服务，适合百万级以上块数。两者通过
OHOPILOT_VECTOR_BACKEND=sqlite-vec|milvus 切换。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ohopilot.kb.embedder import pack


@dataclass
class KbHit:
    """一条检索结果。score 是相似度（越大越相关），由距离换算而来。"""

    chunk_id: str
    source: str
    text: str
    score: float
    distance: float


class VectorStore:
    """接口。所有方法的向量都是 float 列表，字节化是各实现自己的事。"""

    def add(self, chunk_ids: list[str], vectors: list[list[float]], sources: list[str], texts: list[str]) -> None:
        raise NotImplementedError

    def search(self, vector: list[float], top_k: int = 5) -> list[KbHit]:
        raise NotImplementedError

    def delete_by_source(self, source: str) -> int:
        """删掉某来源的全部块，返回删除数。重新索引一个文件前先调它。"""
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def close(self) -> None:
        pass


# ---------------------------------------------------------------- sqlite-vec


class SQLiteVecStore(VectorStore):
    """默认后端：单文件 SQLite + sqlite-vec 扩展。

    为什么独立于会话主库（ohopilot.db）：向量库要加载扩展、
    表结构随 embedding 维度重建，和会话数据的生命周期完全不同；
    分开文件，主库永远不依赖 sqlite-vec 是否可用。
    """

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        try:
            import sqlite_vec
        except ImportError as exc:  # pragma: no cover - 安装指引
            raise ValueError(
                "缺少 sqlite-vec。安装：pip install \"ohopilot[rag]\""
            ) from exc
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.execute("PRAGMA journal_mode=WAL")

        # 元信息表：记录当前向量库是按什么维度/模型建的。
        # 换 embedding 模型后维度对不上，检索必须拒绝而不是静默出错
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        self._conn.commit()
        self._table_dim: int | None = self._read_dim()

    def _read_dim(self) -> int | None:
        row = self._conn.execute(
            "SELECT value FROM kb_meta WHERE key='dim'"
        ).fetchone()
        return int(row["value"]) if row else None

    def _ensure_table(self, dim: int) -> None:
        """vec0 表维度在建表时固定。首次入库时按实际维度建。"""
        if self._table_dim == dim:
            return
        if self._table_dim is not None and self._table_dim != dim:
            raise ValueError(
                f"向量维度不匹配：库里是 {self._table_dim} 维，当前模型给出 {dim} 维。\n"
                "你换过 embedding 模型——旧向量全部作废，请删除 "
                f"{self._conn.execute('PRAGMA database_list').fetchone()[2]} 后重新索引。"
            )
        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks USING vec0("
            f"chunk_id TEXT PRIMARY KEY, source TEXT, vec float[{dim}])"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kb_texts ("
            "chunk_id TEXT PRIMARY KEY, source TEXT, text TEXT)"
        )
        self._conn.execute(
            "INSERT INTO kb_meta(key, value) VALUES('dim', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(dim),),
        )
        self._conn.commit()
        self._table_dim = dim

    def add(self, chunk_ids: list[str], vectors: list[list[float]], sources: list[str], texts: list[str]) -> None:
        self._ensure_table(len(vectors[0]))
        rows = [
            (cid, src, pack(vec))
            for cid, src, vec in zip(chunk_ids, sources, vectors)
        ]
        self._conn.executemany(
            "INSERT INTO kb_chunks(chunk_id, source, vec) VALUES (?, ?, ?)", rows
        )
        self._conn.executemany(
            "INSERT INTO kb_texts(chunk_id, source, text) VALUES (?, ?, ?)",
            list(zip(chunk_ids, sources, texts)),
        )
        self._conn.commit()

    def search(self, vector: list[float], top_k: int = 5) -> list[KbHit]:
        if self._table_dim is None:
            return []  # 空库：没有表，直接返回没有结果
        if self._table_dim != len(vector):
            raise ValueError(
                f"查询向量 {len(vector)} 维，与库里 {self._table_dim} 维不符——"
                "embedding 模型和索引时不一致。"
            )
        rows = self._conn.execute(
            "SELECT chunk_id, source, distance FROM kb_chunks "
            "WHERE vec MATCH ? AND k = ? ORDER BY distance",
            (pack(vector), top_k),
        ).fetchall()
        return [
            KbHit(
                chunk_id=r["chunk_id"],
                source=r["source"],
                text=self._text_of(r["chunk_id"]),
                score=1.0 / (1.0 + r["distance"]),
                distance=r["distance"],
            )
            for r in rows
        ]

    def _text_of(self, chunk_id: str) -> str:
        """块正文按主键回查——vec0 虚拟表不适合塞大文本列，正文存普通表。"""
        row = self._conn.execute(
            "SELECT text FROM kb_texts WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
        return row["text"] if row else ""

    def delete_by_source(self, source: str) -> int:
        if self._table_dim is None:
            return 0  # 空库还没建表，自然没有可删的
        # vec0 虚拟表按非主键删除支持不稳，先查主键再删，行为确定
        ids = [r[0] for r in self._conn.execute(
            "SELECT chunk_id FROM kb_chunks WHERE source=?", (source,)
        ).fetchall()]
        if not ids:
            return 0
        self._conn.executemany(
            "DELETE FROM kb_chunks WHERE chunk_id=?", [(i,) for i in ids]
        )
        self._conn.executemany(
            "DELETE FROM kb_texts WHERE chunk_id=?", [(i,) for i in ids]
        )
        self._conn.commit()
        return len(ids)

    def count(self) -> int:
        if self._table_dim is None:
            return 0
        return self._conn.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------- Milvus


class MilvusStore(VectorStore):
    """Milvus 后端：独立服务，适合大知识库。惰性导入 pymilvus。

    连接参数来自 OHOPILOT_MILVUS_URI（如 http://127.0.0.1:19530）。
    """

    COLLECTION = "ohopilot_kb"

    def __init__(self, uri: str) -> None:
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover
            raise ValueError(
                "缺少 pymilvus。安装：pip install \"ohopilot[rag]\"，"
                "或者改回默认后端 OHOPILOT_VECTOR_BACKEND=sqlite-vec"
            ) from exc
        self._client = MilvusClient(uri)
        self._dim: int | None = None

    def _ensure_collection(self, dim: int) -> None:
        if self._client.has_collection(self.COLLECTION):
            info = self._client.describe_collection(self.COLLECTION)
            real_dim = next(
                f["params"]["dim"]
                for f in info["fields"]
                if f["name"] == "vector"
            )
            if real_dim != dim:
                raise ValueError(
                    f"Milvus 集合维度 {real_dim} 与当前模型 {dim} 维不符，"
                    "请 drop 集合 ohopilot_kb 后重新索引。"
                )
            return
        from pymilvus import DataType

        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("source", DataType.VARCHAR, max_length=1024)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="FLAT", metric_type="COSINE")
        self._client.create_collection(self.COLLECTION, schema, index_params)

    def add(self, chunk_ids: list[str], vectors: list[list[float]], sources: list[str], texts: list[str]) -> None:
        self._ensure_collection(len(vectors[0]))
        self._client.insert(
            self.COLLECTION,
            [
                {"chunk_id": c, "source": s, "text": t, "vector": v}
                for c, s, t, v in zip(chunk_ids, sources, texts, vectors)
            ],
        )

    def search(self, vector: list[float], top_k: int = 5) -> list[KbHit]:
        if not self._client.has_collection(self.COLLECTION):
            return []
        res = self._client.search(
            self.COLLECTION,
            data=[vector],
            limit=top_k,
            output_fields=["source", "text"],
        )[0]
        return [
            KbHit(
                chunk_id=r["id"],
                source=r["entity"]["source"],
                text=r["entity"]["text"],
                score=float(r["distance"]),  # COSINE 度量下 distance 即相似度
                distance=float(r["distance"]),
            )
            for r in res
        ]

    def delete_by_source(self, source: str) -> int:
        if not self._client.has_collection(self.COLLECTION):
            return 0
        before = self._client.get_collection_stats(self.COLLECTION)["row_count"]
        self._client.delete(self.COLLECTION, filter=f'source == "{source}"')
        after = self._client.get_collection_stats(self.COLLECTION)["row_count"]
        return before - after

    def count(self) -> int:
        if not self._client.has_collection(self.COLLECTION):
            return 0
        return self._client.get_collection_stats(self.COLLECTION)["row_count"]

    def close(self) -> None:
        self._client.close()


def build_vector_store(cfg) -> VectorStore:
    """按配置挑后端。sqlite-vec 是默认，不需要任何配置。"""
    backend = (cfg.vector_backend or "sqlite-vec").lower()
    if backend == "sqlite-vec":
        return SQLiteVecStore(cfg.home / "kb.db")
    if backend == "milvus":
        if not cfg.milvus_uri:
            raise ValueError("Milvus 后端需要配置 OHOPILOT_MILVUS_URI，如 http://127.0.0.1:19530")
        return MilvusStore(cfg.milvus_uri)
    raise ValueError(f"未知的向量后端：{backend}（可选 sqlite-vec / milvus）")
