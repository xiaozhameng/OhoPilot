"""命令执行工具。

桌面 agent 的价值就在于"真能操作你的机器"，所以不能一律塞容器；
但必须给一道硬闸：危险命令直接拒绝，其余逐条人工确认。
"""

from __future__ import annotations

import asyncio
import re
import shlex
from typing import Any

from ..config import Config
from .base import Tool, ToolResult

# 一旦命中，直接拒绝执行，连确认机会都不给
DENY_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*r[a-zA-Z]*f", "递归强制删除"),
    (r"\brm\s+-rf\b", "递归强制删除"),
    (r":\s*\(\s*\)\s*\{", "fork 炸弹"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bdd\s+.*of=/dev/", "直接写裸设备"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b", "关机或重启"),
    (r"\bdiskutil\s+(eraseDisk|eraseVolume|reformat)", "抹掉磁盘"),
    (r"\bchmod\s+-R\s+777\s+/", "对根目录放开权限"),
    (r">\s*/dev/sd[a-z]", "写裸设备"),
    (r"\bcurl\b[^|]*\|\s*(ba)?sh", "下载后直接执行远程脚本"),
]


class ShellTool(Tool):
    name = "shell.run"
    description = (
        "在工作目录中执行一条 shell 命令并返回标准输出与错误输出。"
        "适合跑构建、测试、git、查系统信息。危险命令（删除、格式化、关机）会被拒绝。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "integer", "description": "超时秒数，默认取配置值"},
            "cwd": {"type": "string", "description": "工作目录，默认项目工作目录"},
        },
        "required": ["command"],
    }
    requires_approval = True

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def needs_approval(self, arguments: dict[str, Any]) -> tuple[bool, str]:
        command = str(arguments.get("command", ""))
        for pattern, label in DENY_PATTERNS:
            if re.search(pattern, command, flags=re.IGNORECASE):
                # 用特殊标记让 agent 直接拒绝
                return True, f"__DENY__命中禁止规则：{label}"
        return True, "即将在你的电脑上执行命令"

    @staticmethod
    def is_denied(reason: str) -> bool:
        return reason.startswith("__DENY__")

    async def run(self, command: str, timeout: int | None = None, cwd: str = "", **_: Any) -> ToolResult:
        workdir = cwd or str(self.cfg.workspace)
        limit = timeout or self.cfg.shell_timeout

        try:
            argv = ["/bin/zsh", "-lc", command]
        except ValueError:
            argv = ["/bin/sh", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return ToolResult.fail(f"无法启动命令：{exc}")

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=limit)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult.fail(f"命令超时（{limit}s）已终止：{command}")

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        code = proc.returncode

        parts = [f"$ {command}", f"exit code: {code}"]
        if out:
            parts.append(f"--- stdout ---\n{out}")
        if err:
            parts.append(f"--- stderr ---\n{err}")
        body = "\n".join(parts)
        return ToolResult.fail(body) if code else ToolResult.ok(body)


def build_shell_tool(cfg: Config) -> Tool:
    return ShellTool(cfg)


__all__ = ["ShellTool", "build_shell_tool", "DENY_PATTERNS", "shlex"]
