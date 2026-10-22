"""工具协议与注册表。

每个工具三件套：schema 给模型看、run 真正干活、needs_approval 决定要不要人工确认。

写新工具前过一遍这五条（源自 OpenAI / Anthropic / MCP 公开的工具设计指南）：
1. description 写「什么时候用 + 什么时候不用」，别只写"它是什么"——模型靠场景匹配选工具；
2. 参数名自解释，required 列表最小化——每多一个必填参数，模型编错的机会多一分；
3. 错误信息要包含"怎么补救"——只说"失败了"，模型会换个猜的路径重试同样的错；
4. 返回里带结构化线索：路径、行号、总数、截断提示——让模型下一步有依据；
5. 一个工具只做一件事——宁可多个窄工具，不要一个带十个 flag 的万能工具。
"""

import abc
import json
from dataclasses import dataclass
from typing import Any

import re
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

MAX_RESULT_CHARS = 12_000

@dataclass
class ToolResult:
    content: str
    is_error: bool = False

    @classmethod
    def ok(cls, content: str) -> "ToolResult":
        return cls(content=content, is_error=False)

    @classmethod
    def fail(cls, content: str) -> "ToolResult":
        return cls(content=content, is_error=True)

def _truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    """工具结果会回填进上下文，必须截断，否则一次 ls 大目录就爆 token。"""
    if len(text) <= limit:
        return text
    head = text[:limit // 2]
    tail = text[-limit // 2:]
    return f"{head}\n\n...[已省略{len(text) - limit}字符]\n\n{tail}"

class Tool(abc.ABC):
    name: str= ""
    description: str = ""
    parameters: dict[str, Any] = {}
    #: 静态风险标记；需要按参数动态判断时覆盖 needs_approval
    requires_approval: bool = False


    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

    def needs_approval(self, arguments: dict[str, Any]) -> tuple[bool, str]:
        if self.requires_approval:
            return True, "该操作可能修改你的电脑"
        return False, ""


    @abc.abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        ...

    async def safe_run(self, arguments: dict[str, Any]) -> ToolResult:
        """统一兜住工具内部的异常，别让一个工具挂掉整个循环。"""
        try:
            return _wrap(await self.run(**arguments))
        except TypeError as exc:
            return ToolResult.fail(f"参数不匹配：{exc}")
        except Exception as exc:
            return ToolResult.fail(f"{self.name} 执行失败：{type(exc).__name__}: {exc}")


def _wrap(value: Any) -> ToolResult:
    if isinstance(value, ToolResult):
        return ToolResult(content=_truncate(value.content), is_error=value.is_error)
    return ToolResult.ok(_truncate(str(value)))


class ToolRegistry:
    """内核通过它查找工具；同时负责生成发给模型的 schema 列表。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("工具必须有 name")
        if not _NAME_RE.match(tool.name):
            raise ValueError(
                f"工具名不合法：{tool.name}（只允许字母、数字、下划线、连字符）"
            )
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._tools[name].schema() for name in self.names()]

    def describe(self) -> str:
        return json.dumps(self.names(), ensure_ascii=False)