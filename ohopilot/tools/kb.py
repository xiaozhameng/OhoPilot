""" 知识库工具：kb_index（索引文档）+ kb_search（语义检索）

数据流：
  kb_index: 文件 → 解析成纯文本（复用 docs.py 的三个解析器）→ 切块
            → embedder 算向量 → VectorStore 入库
  kb_search: query → embedder → VectorStore KNN → 片段+来源+分数

为什么 source 用「相对工作目录的路径」：重新索引同一文件时按它删旧块，
文件挪了位置算新来源——这是特性不是缺陷（旧位置的内容可能已经变了）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ohopilot.config import Config
from ohopilot.kb.chunker import chunk_text
from ohopilot.kb.embedder import build_embedder
from ohopilot.kb.store import build_vector_store
from ohopilot.tools.base import Tool, ToolResult
from ohopilot.tools.docs import read_docx_text, read_pdf_text, read_xlsx_text

#: 一次索引最多多少块——防止有人把 10 万行的 csv 灌进来烧穿 embedding 配额
MAX_CHUNKS = 2000

#: 纯文本后缀直读；其余按文档解析
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".py", ".json", ".yaml", ".yml", ".html"}


def _load_text(target: Path) -> str:
    """按后缀把文件变成纯文本。解析逻辑全部复用 docs.py，不写第二份。"""
    suffix = target.suffix.lower()
    if suffix == ".pdf":
        text, _ = read_pdf_text(target, None)
        return text
    if suffix == ".docx":
        text, _ = read_docx_text(target)
        return text
    if suffix == ".xlsx":
        text, _ = read_xlsx_text(target, None)
        return text
    if suffix in TEXT_SUFFIXES or suffix == "":
        return target.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"不支持的格式：{suffix}（支持纯文本与 pdf/docx/xlsx）")


def _source_name(cfg: Config, target: Path) -> str:
    """来源名：相对工作目录的路径；家目录文件（如上传件）用文件名前缀区分。"""
    try:
        return str(target.relative_to(cfg.workspace))
    except ValueError:
        return str(target)


def _chunk_ids(source: str, n: int) -> list[str]:
    """块 id：来源路径 hash + 序号。同源重建时 id 稳定可预测。"""
    h = hashlib.md5(source.encode()).hexdigest()[:10]
    return [f"{h}-{i}" for i in range(n)]


class KbIndexTool(Tool):
    name = "kb_index"
    description = (
        "把一份文档索引进个人知识库，供日后语义检索。"
        "支持纯文本（txt/md/csv/代码等）与 PDF / Word / Excel。"
        "同一文件重复索引会先删旧块再入库（覆盖式更新）。"
        "什么时候用：用户说「把这份文档加进知识库」「记到知识库里」。"
        "一次性问题（读一下这份文件说什么）不需要索引，直接 fs_read_document。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要索引的文件路径"},
        },
        "required": ["path"],
    }

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    async def run(self, path: str, **_: Any) -> ToolResult:
        try:
            embedder = build_embedder(self.cfg)
        except ValueError as exc:
            return ToolResult.fail(str(exc))  # 配置缺失：错误信息里已带配置指引

        target = Path(path).expanduser()
        if not target.is_absolute():
            target = self.cfg.workspace / target
        target = target.resolve()
        if not target.exists():
            return ToolResult.fail(f"文件不存在：{target}")

        try:
            text = _load_text(target)
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        except Exception as exc:  # 解析库对损坏文件抛的异常家族很杂
            return ToolResult.fail(f"文档解析失败（文件可能损坏或加密）：{exc}")

        chunks = chunk_text(text)
        if not chunks:
            return ToolResult.fail("文件内容为空，没有可索引的内容。")
        if len(chunks) > MAX_CHUNKS:
            return ToolResult.fail(
                f"文档切出 {len(chunks)} 块，超过单次上限 {MAX_CHUNKS}。"
                "请先拆分文档，或只索引其中一部分。"
            )

        source = _source_name(self.cfg, target)
        store = build_vector_store(self.cfg)
        try:
            removed = store.delete_by_source(source)  # 覆盖式：先删旧块
            vectors = embedder.embed_batched(chunks)
            store.add(_chunk_ids(source, len(chunks)), vectors, [source] * len(chunks), chunks)
        finally:
            store.close()

        return ToolResult.ok(
            f"已索引：{source}\n"
            f"切了 {len(chunks)} 块（每块约 500 字符），向量后端 {type(store).__name__}。"
            + (f"（覆盖了旧索引的 {removed} 块）" if removed else "")
        )


class KbSearchTool(Tool):
    name = "kb_search"
    description = (
        "在个人知识库里做语义检索：给一句自然语言查询，返回最相关的文档片段。"
        "结果带来源文件和相似度分数。"
        "什么时候用：回答涉及知识库内容的问题前，先检索再回答，不要凭印象编。"
        "先用 kb_index 索引过的内容才查得到；查不到就说知识库里没有。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "自然语言查询"},
            "top_k": {"type": "integer", "description": "返回几条，默认 5，最多 20"},
        },
        "required": ["query"],
    }

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    async def run(self, query: str, top_k: int = 5, **_: Any) -> ToolResult:
        try:
            embedder = build_embedder(self.cfg)
        except ValueError as exc:
            return ToolResult.fail(str(exc))

        top_k = max(1, min(int(top_k), 20))
        store = build_vector_store(self.cfg)
        try:
            if store.count() == 0:
                return ToolResult.ok("知识库是空的。先用 kb_index 索引一些文档再查。")
            qvec = embedder.embed([query])[0]
            hits = store.search(qvec, top_k=top_k)
        except ValueError as exc:
            # 维度不匹配=换过 embedding 模型，错误信息里已带重建指引
            return ToolResult.fail(str(exc))
        finally:
            store.close()

        if not hits:
            return ToolResult.ok("知识库里没有和这句话相关的内容。")

        lines = []
        for i, h in enumerate(hits, 1):
            lines.append(f"[{i}] 来源 {h.source}（相关度 {h.score:.2f}）\n{h.text}")
        return ToolResult.ok("\n\n---\n\n".join(lines))


def build_kb_tools(cfg: Config) -> list[Tool]:
    return [KbIndexTool(cfg), KbSearchTool(cfg)]
