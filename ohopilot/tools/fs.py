"""文件工具：读、写、列目录、搜索。

工作根目录是"安全边界"——写操作分三层防线：
1. 路径越出工作目录 → run() 里直接拒绝；
2. 命中硬禁止清单（.env / .git）→ run() 里直接拒绝，不给审批机会；
3. 工作目录内的写操作 → requires_approval=True，走人工审批。
目前已实现：读取、写入。列目录与搜索在第 12 步补齐。
"""
from pathlib import Path
from typing import Any

from ohopilot.config import Config
from ohopilot.tools.base import Tool, ToolResult

MAX_READ_BYTES = 200_000

class _FsTool(Tool):
    """文件类工具的共用基类：拿到配置，并把路径解析到工作目录下。"""

    #: 硬禁止清单：读写都不许碰路径中的这些段。
    #: 写 .env 会毁掉配置；读 .env 会把密钥送进模型对话（10b 实测泄露过），同样要禁
    PROTECTED = {".env", ".git"}

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _is_protected(self, path: Path) -> bool:
        return any(part in self.PROTECTED for part in path.parts)

    def _resolve(self, raw: str) -> tuple[Path, bool]:
        """把用户给的路径变成绝对路径，并判断是否在工作根目录内。"""
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
    name = "fs_read_file"
    description = (
        "读取一个文本文件的内容，参数path 可以是相对路径，或者是绝对路径"
        "可用offset/limit读取大文件的一部分"
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "offset": {"type": "integer", "description": "起始行号，从 1 开始"},
            "limit": {"type": "integer", "description": "最多读取行数，默认 500"},
        },
        "required": ["path"]
    }

    async def run(self, path: str, offset: int = 1, limit: int = 500, **_: Any) -> ToolResult:
        target, _ = self._resolve(path)
        if self._is_protected(target):
            return ToolResult.fail(f"禁止读取受保护路径：{target}")
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
    name = "fs_write_file"
    description = (
        "把文本内容写入文件，整个文件会被覆盖。"
        "path 可以是相对路径或绝对路径；父目录不存在会自动创建。"
        "这是写操作，执行前需要用户确认。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要写入的文件路径"},
            "content": {"type": "string", "description": "完整的文件内容"},
        },
        "required": ["path", "content"],
    }

    #: 声明为危险操作：_execute() 会为它发 approval_request 事件
    requires_approval = True

    async def run(self, path: str, content: str = "", **_: Any) -> ToolResult:
        target, inside = self._resolve(path)

        # 防线一：硬边界。越出工作目录的写操作直接拒绝
        if not inside:
            return ToolResult.fail(f"路径超出工作目录，已拒绝：{target}")

        # 防线二：硬禁止清单（基类共用）。workspace/.env 和 workspace/sub/.env 都会被拦住
        if self._is_protected(target):
            return ToolResult.fail(f"禁止写入受保护路径：{target}")

        try:
            # parents=True：模型经常要"在子目录里新建文件"，父目录得能自动建
            # exist_ok=True：目录已存在时不报 FileExistsError
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult.fail(f"写入失败：{exc}")

        size = len(content.encode("utf-8"))
        return ToolResult.ok(f"已写入 {target}（{size} 字节）")


def build_fs_tools(cfg: Config) -> list[Tool]:
    return [ReadFileTool(cfg), WriteFileTool(cfg)]