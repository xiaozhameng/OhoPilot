"""工具集入口：把内置工具装进注册表。"""

from __future__ import annotations

from ..config import Config
from .base import Tool, ToolRegistry, ToolResult
from .fs import build_fs_tools
from .shell import build_shell_tool
from .web import build_web_tools


def build_registry(cfg: Config) -> ToolRegistry:
    tools: list[Tool] = []
    tools.extend(build_fs_tools(cfg))
    tools.append(build_shell_tool(cfg))
    tools.extend(build_web_tools())
    return ToolRegistry(tools)


__all__ = ["Tool", "ToolRegistry", "ToolResult", "build_registry"]
