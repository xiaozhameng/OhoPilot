"""文件工具：读、写、列目录、搜索。

工作根目录是"安全边界"——写操作分四层防线：
0. 配置总开关（OHOPILOT_ALLOW_WRITE=0 时一律不落盘）；
1. 路径越出工作目录 → run() 里直接拒绝；
2. 命中硬禁止清单（.env / .git）→ run() 里直接拒绝，不给审批机会；
3. 工作目录内的写操作 → requires_approval=True，走人工审批。
"""
import os
from pathlib import Path
from typing import Any

from ohopilot.config import Config
from ohopilot.tools.base import Tool, ToolResult

MAX_READ_BYTES = 200_000

#: 搜索时的成本上限：扫太多文件会拖慢一次对话，还不如让模型缩小范围
MAX_SCAN_FILES = 2_000
MAX_SCAN_BYTES = 1_000_000

#: 默认不进入的目录。注意它和 PROTECTED 是两回事：
#: PROTECTED 是安全边界（命中就拒绝），这里是噪声过滤（命中就跳过，不算错误）。
SKIP_DIRS = {
    ".venv", "venv", ".git", "__pycache__", "node_modules",
    ".idea", ".vscode", ".mypy_cache", ".pytest_cache", "dist", "build",
}

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
        "读取一个文本文件，返回带行号的内容。适合：已经知道路径、要读它的内容时。"
        "不知道文件在哪时先用 fs_search 定位，只想看目录结构用 fs_list_dir，"
        "两者都比盲猜路径再重试省得多。大文件用 offset/limit 分段读；"
        "二进制文件（图片、压缩包）读不了。"
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
            # 错误要给补救路径：只说"不存在"，模型多半会换个猜的路径再试一次
            return ToolResult.fail(
                f"文件不存在：{target}\n可用 fs_search 找文件名或内容，或用 fs_list_dir 看目录里实际有什么。"
            )
        if target.is_dir():
            return ToolResult.fail(f"这是一个目录不是文件：{target}\n用 fs_list_dir 查看它里面的内容。")

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
        # 防线零：总开关。只想让它读、不许它写的时候，一次都不该落盘
        if not self.cfg.allow_write:
            return ToolResult.fail(
                "写操作已被配置关闭（OHOPILOT_ALLOW_WRITE=0），本次没有写入任何内容。"
            )

        target, inside = self._resolve(path)

        # 防线一：硬边界。越出工作目录的写操作直接拒绝
        if not inside:
            return ToolResult.fail(
                f"路径超出工作目录，已拒绝：{target}\n"
                f"写操作只允许在工作目录内进行：{self.cfg.workspace}"
            )

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


class ListDirTool(_FsTool):
    name = "fs_list_dir"
    description = (
        "列出目录下的文件和子目录。目录名带 / 后缀，文件带字节数。"
        "用于了解项目结构；只看一层，不递归。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径，默认当前工作目录"},
            "limit": {"type": "integer", "description": "最多列出多少项，默认 200"},
        },
        "required": [],
    }

    async def run(self, path: str = ".", limit: int = 200, **_: Any) -> ToolResult:
        target, inside = self._resolve(path)
        if not inside:
            return ToolResult.fail(
                f"路径超出工作目录，已拒绝：{target}\n"
                f"访问只允许在工作目录内进行：{self.cfg.workspace}"
            )
        if self._is_protected(target):
            return ToolResult.fail(f"禁止访问受保护路径：{target}")
        if not target.exists():
            return ToolResult.fail(
                f"目录不存在：{target}\n可用 fs_list_dir 的默认参数先看工作目录里有什么。"
            )
        if not target.is_dir():
            return ToolResult.fail(f"这不是目录，是文件：{target}")

        try:
            entries = list(target.iterdir())
        except OSError as exc:
            return ToolResult.fail(f"读取目录失败：{exc}")

        # 目录在前、文件在后，各自按名字排序：模型和人都更容易扫结构
        entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))

        # 受保护项不列出来：只给名字也会诱导模型去读，然后被拒
        visible = [e for e in entries if e.name not in self.PROTECTED]
        hidden = len(entries) - len(visible)
        shown = visible[: max(1, limit)]

        lines: list[str] = []
        for entry in shown:
            if entry.is_dir():
                lines.append(f"{entry.name}/")
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0  # 断掉的符号链接等，别让一条坏的拖垮整个列表
            lines.append(f"{entry.name}  ({size} B)")

        header = f"{target}  (共 {len(visible)} 项可显示，显示 {len(shown)}"
        if hidden:
            header += f"，已隐藏 {hidden} 个受保护项"
        header += ")\n"
        return ToolResult.ok(header + "\n".join(lines))


class SearchTool(_FsTool):
    name = "fs_search"
    description = (
        "在工作目录内搜索：先匹配文件名，再逐行匹配文本内容（不区分大小写）。"
        "返回「文件:行号: 内容」。想找某个函数、配置项、报错信息出现在哪时用它，"
        "比逐个 fs_read_file 快得多。成本上限：最多扫 2000 个文件。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "要找的关键词（纯文本，不是正则）"},
            "path": {"type": "string", "description": "搜索起点目录，默认当前工作目录"},
            "suffix": {"type": "string", "description": "只搜这个后缀的文件，如 .py；留空表示全部"},
            "limit": {"type": "integer", "description": "最多返回多少条命中，默认 40"},
        },
        "required": ["pattern"],
    }

    def _iter_files(self, root: Path, suffix: str):
        """遍历文件，顺便剪掉噪声目录。生成器：命中够了就停，不白扫。"""
        self._scanned = 0
        for dirpath, dirnames, filenames in os.walk(root):
            # 原地修改 dirnames 才会真正剪枝：os.walk 不会进入这些目录
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in sorted(filenames):
                if name in self.PROTECTED:
                    continue
                if suffix and not name.endswith(suffix):
                    continue
                self._scanned += 1
                if self._scanned > MAX_SCAN_FILES:
                    return
                yield Path(dirpath) / name

    async def run(
        self,
        pattern: str = "",
        path: str = ".",
        suffix: str = "",
        limit: int = 40,
        **_: Any,
    ) -> ToolResult:
        needle = pattern.strip()
        if not needle:
            return ToolResult.fail("pattern 不能为空")
        lower = needle.lower()

        root, inside = self._resolve(path)
        if not inside:
            return ToolResult.fail(f"路径超出工作目录，已拒绝：{root}")
        if self._is_protected(root):
            return ToolResult.fail(f"禁止搜索受保护路径：{root}")
        if not root.exists():
            return ToolResult.fail(f"路径不存在：{root}")

        files = [root] if root.is_file() else self._iter_files(root, suffix)
        max_hits = max(1, limit)
        hits: list[str] = []

        for file in files:
            try:
                rel = file.relative_to(self.cfg.workspace)
            except ValueError:
                rel = file

            if lower in file.name.lower():
                hits.append(f"{rel}  （文件名命中）")
                if len(hits) >= max_hits:
                    break

            try:
                if file.stat().st_size > MAX_SCAN_BYTES:
                    continue
                raw = file.read_bytes()
            except OSError:
                continue
            if b"\0" in raw[:2048]:
                continue  # 二进制文件，跳过

            text = raw.decode("utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                if lower in line.lower():
                    hits.append(f"{rel}:{lineno}: {line.strip()[:160]}")
                    if len(hits) >= max_hits:
                        break
            if len(hits) >= max_hits:
                break

        if not hits:
            # 没命中不是错误：回 ok，模型才不会以为工具坏了反复重试
            return ToolResult.ok(f"在 {root} 下没有找到「{needle}」")
        header = f"在 {root} 下搜索「{needle}」，命中 {len(hits)} 条：\n"
        body = header + "\n".join(hits)
        if getattr(self, "_scanned", 0) > MAX_SCAN_FILES:
            # 扫到上限就停：必须显式告诉模型结果不全，否则它会当成"只找到这些"
            body += f"\n（已扫描 {MAX_SCAN_FILES} 个文件后停止，结果可能不全；请缩小 path 或加 suffix）"
        return ToolResult.ok(body)


def build_fs_tools(cfg: Config) -> list[Tool]:
    return [ReadFileTool(cfg), WriteFileTool(cfg), ListDirTool(cfg), SearchTool(cfg)]