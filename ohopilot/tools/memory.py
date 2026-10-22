"""长期记忆工具：让模型能往 ~/.ohopilot/MEMORY.md 追加一条事实。

为什么不复用 fs_write_file：
1. 那个工具的硬边界是工作目录，而记忆文件在 ~/.ohopilot/ 下，路径根本到不了；
2. 那个工具是「整文件覆盖」，改记忆得先读全文再写回——中途失败就把旧记忆抹了；
3. 记忆是影响之后每一轮对话的东西，值得一条更窄、更专用的通道。
"""

from __future__ import annotations

from typing import Any

from ohopilot.config import Config
from ohopilot.memory.longterm import append_longterm
from ohopilot.tools.base import Tool, ToolResult


class MemoryAppendTool(Tool):
    name = "memory_append"
    description = (
        "把一条需要长期记住的事实追加到长期记忆文件，之后每次对话都会自动带上。"
        "适合：用户偏好与习惯、项目约定、身份背景、长期目标。"
        "不适合：一次性信息、本轮任务的临时结论、能从文件里随时查到的东西、"
        "操作系统与路径这类环境常识（每次都能自己看到，写进来只会污染记忆）。"
        "每次只写一条，写成一句陈述句。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要记住的一句话，例如「用户是后端出身，讲解代码时要逐行说明」",
            },
        },
        "required": ["text"],
    }

    #: 它改的是模型以后每一轮的行为，必须让用户过目
    requires_approval = True

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def needs_approval(self, arguments: dict[str, Any]) -> tuple[bool, str]:
        return True, "要往长期记忆里写入一条内容（之后每轮对话都会带上）"

    async def run(self, text: str = "", **_: Any) -> ToolResult:
        try:
            total = append_longterm(self.cfg, text)
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        except OSError as exc:
            return ToolResult.fail(f"写入长期记忆失败：{exc}")
        return ToolResult.ok(f"已记住（共 {total} 字符）：{self.cfg.memory_file}")
