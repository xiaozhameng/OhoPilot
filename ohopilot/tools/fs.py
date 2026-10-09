"""文件工具：读、写、列目录、搜索。

工作根目录之外的路径一律升级为"需要人工确认"，这是最基础的一道边界。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from ..config import Config
from .base import Tool, ToolResult

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".DS_Store"}
MAX_READ_BYTES = 200_000
MAX_LIST_ENTRIES = 400


class _FsTool(Tool):
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _resolve(self, raw: str) -> tuple[Path, bool]:
        """返回 (绝对路径, 是否在工作根目录内)。"""
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.cfg.workspace / path
        path = path.resolve()
        try:
            path.relative_to(self.cfg.workspace)
            inside = True
        except ValueError:
            inside = False
        return path, inside


class ReadFileTool(_FsTool):
    name = "fs.read_file"
    description = (
        "读取一个文本文件的内容。参数 path 可以是相对路径（相对工作目录）或绝对路径。"
        "可用 offset/limit 读取大文件的一部分。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "offset": {"type": "integer", "description": "起始行号，从 1 开始"},
            "limit": {"type": "integer", "description": "最多读取行数，默认 500"},
        },
        "required": ["path"],
    }

    async def run(self, path: str, offset: int = 1, limit: int = 500, **_: Any) -> ToolResult:
        target, _ = self._resolve(path)
        if not target.exists():
            return ToolResult.fail(f"文件不存在：{target}")
        if target.is_dir():
            return ToolResult.fail(f"这是一个目录，不是文件：{target}")

        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            return ToolResult.fail(f"文件过大（{size} 字节），请用 offset/limit 分段读取。")

        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return ToolResult.fail(f"读取失败：{exc}")

        start = max(1, offset) - 1
        chunk = lines[start : start + max(1, limit)]
        body = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(chunk))
        header = f"{target}  (共 {len(lines)} 行，显示 {start + 1}-{start + len(chunk)})\n"
        return ToolResult.ok(header + body)


class WriteFileTool(_FsTool):
    name = "fs.write_file"
    description = (
        "把内容写入文件（覆盖已有内容）。父目录不存在会自动创建。"
        "追加内容请用 append=true。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要写入的文本"},
            "append": {"type": "boolean", "description": "true 表示追加而不是覆盖"},
        },
        "required": ["path", "content"],
    }

    def needs_approval(self, arguments: dict[str, Any]) -> tuple[bool, str]:
        path, inside = self._resolve(str(arguments.get("path", "")))
        if not inside:
            return True, f"目标在工作目录之外：{path}"
        if not self.cfg.allow_write:
            return True, "当前配置为写操作需确认"
        return False, ""

    async def run(self, path: str, content: str, append: bool = False, **_: Any) -> ToolResult:
        target, _ = self._resolve(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with target.open(mode, encoding="utf-8") as handle:
                handle.write(content)
        except OSError as exc:
            return ToolResult.fail(f"写入失败：{exc}")
        verb = "追加" if append else "写入"
        return ToolResult.ok(f"已{verb} {len(content)} 字符到 {target}")


class ListDirTool(_FsTool):
    name = "fs.list_dir"
    description = "列出一个目录下的文件与子目录，含大小。默认不递归。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径，默认工作目录"},
            "recursive": {"type": "boolean", "description": "是否递归列出"},
        },
        "required": [],
    }

    async def run(self, path: str = ".", recursive: bool = False, **_: Any) -> ToolResult:
        target, _ = self._resolve(path)
        if not target.exists():
            return ToolResult.fail(f"目录不存在：{target}")
        if not target.is_dir():
            return ToolResult.fail(f"不是目录：{target}")

        rows: list[str] = []
        if recursive:
            children = sorted(target.rglob("*"))
        else:
            children = sorted(target.iterdir())

        for child in children:
            if any(part in SKIP_DIRS for part in child.parts):
                continue
            try:
                size = child.stat().st_size if child.is_file() else 0
            except OSError:
                size = 0
            kind = "d" if child.is_dir() else "f"
            rel = child.relative_to(target) if child.is_relative_to(target) else child
            rows.append(f"{kind} {size:>10}  {rel}")
            if len(rows) >= MAX_LIST_ENTRIES:
                rows.append(f"... 超过 {MAX_LIST_ENTRIES} 项，已截断 ...")
                break

        return ToolResult.ok(f"{target}\n" + ("\n".join(rows) or "(空目录)"))


class SearchFilesTool(_FsTool):
    name = "fs.search_files"
    description = (
        "按文件名通配符（如 *.md、*周报*）或文件内容关键词在工作目录内搜索文件。"
        "找资料、找日志、找旧文档时优先用它，比逐层列目录快。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "文件名通配符，如 *.py"},
            "keyword": {"type": "string", "description": "可选的文本关键词，用于搜内容"},
            "path": {"type": "string", "description": "搜索起点，默认工作目录"},
            "max_results": {"type": "integer", "description": "最多返回条数，默认 50"},
        },
        "required": [],
    }

    async def run(
        self,
        pattern: str = "*",
        keyword: str = "",
        path: str = ".",
        max_results: int = 50,
        **_: Any,
    ) -> ToolResult:
        root, _ = self._resolve(path)
        if not root.is_dir():
            return ToolResult.fail(f"不是目录：{root}")

        hits: list[str] = []
        for child in root.rglob(pattern):
            if any(part in SKIP_DIRS for part in child.parts) or not child.is_file():
                continue
            if keyword:
                try:
                    text = child.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if keyword not in text and not fnmatch.fnmatch(child.name.lower(), f"*{keyword.lower()}*"):
                    continue
            hits.append(str(child))
            if len(hits) >= max_results:
                break

        if not hits:
            return ToolResult.ok(f"未找到匹配项（pattern={pattern} keyword={keyword}）")
        return ToolResult.ok(f"找到 {len(hits)} 项：\n" + "\n".join(hits))


def build_fs_tools(cfg: Config) -> list[Tool]:
    return [ReadFileTool(cfg), WriteFileTool(cfg), ListDirTool(cfg), SearchFilesTool(cfg)]
