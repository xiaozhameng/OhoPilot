""" MCP 工具桥接：远程工具 → ToolRegistry 里的普通 Tool

为什么原样透传 inputSchema：远程工具的参数结构由服务器定义，
我们不解析也不改造——解析错了反而会把合法调用变成非法。
模型拿到 schema 决定怎么调，参数原样转发给服务器。
"""

from __future__ import annotations

import json
from typing import Any

from ohopilot.mcp.client import McpError, McpStdioClient
from ohopilot.tools.base import Tool, ToolResult


def mcp_tool_name(server: str, tool: str) -> str:
    """前缀防重名：两个服务器都有 fetch 时，mcp__a__fetch 和 mcp__b__fetch 并存。"""
    return f"mcp__{server}__{tool}"


class McpToolProxy(Tool):
    """一个远程 MCP 工具在宿主进程里的替身。

    run() 就是「把参数转发给子进程、把文本带回来」。
    默认需要审批：外部工具是第三方代码，权限未知。
    """

    requires_approval = True

    def __init__(self, client: McpStdioClient, server: str, name: str, description: str, input_schema: dict[str, Any]) -> None:
        self._client = client
        self._server = server
        self._remote_name = name
        self.name = mcp_tool_name(server, name)
        self.description = (
            f"{description or name}（来自 MCP 服务器 {server}，调用需要确认）"
        )
        self.parameters = input_schema or {"type": "object", "properties": {}}

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            text = await self._client.call_tool(self._remote_name, kwargs)
        except McpError as exc:
            return ToolResult.fail(f"MCP 工具 {self.name} 调用失败：{exc}")
        return ToolResult.ok(text or "（工具执行成功，无文本返回）")


async def bridge_server(client: McpStdioClient, server: str) -> list[McpToolProxy]:
    """连一个服务器，把它声明的全部工具包成 proxy 列表。"""
    tools = await client.list_tools()
    return [
        McpToolProxy(client, server, t["name"], t["description"], t["inputSchema"])
        for t in tools
    ]


async def connect_all(specs: dict[str, dict[str, Any]]) -> tuple[dict[str, McpStdioClient], list[Tool]]:
    """启动所有配置的 MCP 服务器并桥接工具。

    返回 (连上的 client 表, 桥接出的全部工具)。单个服务器失败只警告，
    绝不拖死宿主——外部工具坏了不该影响本地功能。
    """
    import sys

    clients: dict[str, McpStdioClient] = {}
    tools: list[Tool] = []
    for name, spec in specs.items():
        client = McpStdioClient(name, spec["command"], spec.get("env"))
        try:
            await client.start()
            bridged = await bridge_server(client, name)
        except McpError as exc:
            print(f"[mcp] 服务器 {name} 启动失败，已跳过：{exc}", file=sys.stderr)
            await client.stop()
            continue
        clients[name] = client
        tools.extend(bridged)
        print(f"[mcp] 已连接 {name}（{len(bridged)} 个工具）", file=sys.stderr)
    return clients, tools
