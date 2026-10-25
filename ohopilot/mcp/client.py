""" MCP stdio 客户端：JSON-RPC 2.0 over 子进程 stdin/stdout

协议只有三个方法要实现：
  initialize          握手（声明协议版本，拿服务器能力）
  tools/list          拉工具清单（name/description/inputSchema）
  tools/call          调用工具（参数即 JSON，返回 content 数组）

为什么手写不用官方 SDK：stdio 模式就这三个方法 + 按行传 JSON，
SDK 是几百 KB 依赖换一个 while 循环。手写顺便把协议钉进测试。

为什么用「后台读循环 + pending 字典」而不是发一条等一条：
服务器可能乱序回响应、也可能主动发通知（无 id 的消息），
按 id 配对才能正确处理这两种情况。
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

#: 我们实现的协议版本。服务器响应会带它支持的版本，取我们声明的（MCP 允许）
PROTOCOL_VERSION = "2024-11-05"
#: 握手超时。stdio 服务器启动可能要装依赖，给足 30 秒
INIT_TIMEOUT = 30.0
#: 单次工具调用超时。MCP 工具可能做慢事（抓网页、查库），放宽
CALL_TIMEOUT = 120.0


#: 列工具通常很快，单独给短超时
LIST_TIMEOUT = 20.0


class McpError(Exception):
    """服务器返回的 JSON-RPC error 或协议不符合预期。"""


class McpStdioClient:
    """一个 MCP 服务器的连接。生命周期：start → list_tools/call_tool → stop。"""

    def __init__(self, name: str, command: list[str], env: dict[str, str] | None = None) -> None:
        self.name = name
        self.command = command
        self.env = env or {}
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self.server_version: str = ""

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        """拉起子进程并握手。失败时保证进程被清掉，不留孤儿。"""
        import os

        merged_env = {**os.environ, **self.env}
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,  # 服务器日志走 stderr，不污染协议通道
                env=merged_env,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise McpError(f"无法启动 MCP 服务器 {self.name}（{self.command[0]}）：{exc}") from exc

        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await self._initialize()
        except Exception:
            await self.stop()
            raise

    async def _initialize(self) -> None:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ohopilot", "version": "0.3.0"},
            },
            timeout=INIT_TIMEOUT,
        )
        info = result.get("serverInfo") or {}
        self.server_version = str(info.get("version", ""))
        # 握手完成的客户端通知。按规范必须发，之后才能调用方法
        await self._notify("notifications/initialized")

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None
        # 所有还在等的请求立刻失败，别挂着
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(McpError(f"MCP 服务器 {self.name} 已关闭"))
        self._pending.clear()

    # ---------- 对外两个业务方法 ----------

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {}, timeout=LIST_TIMEOUT)
        tools = result.get("tools") or []
        return [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
            }
            for t in tools
            if t.get("name")
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用远程工具，把 content 数组拼成纯文本返回。"""
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments}, timeout=CALL_TIMEOUT
        )
        if result.get("isError"):
            raise McpError(_content_to_text(result.get("content")) or "MCP 工具执行失败")
        return _content_to_text(result.get("content"))

    # ---------- JSON-RPC 底座 ----------

    async def _request(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        if not self._proc or not self._proc.stdin:
            raise McpError(f"MCP 服务器 {self.name} 未启动")
        self._next_id += 1
        req_id = self._next_id
        message = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut

        line = json.dumps(message, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode())
        try:
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._pending.pop(req_id, None)
            raise McpError(f"MCP 服务器 {self.name} 已断开") from exc

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise McpError(f"MCP 服务器 {self.name} 的 {method} 超时（{timeout:.0f}s）") from None
        finally:
            self._pending.pop(req_id, None)

    async def _notify(self, method: str) -> None:
        if not self._proc or not self._proc.stdin:
            return
        line = json.dumps({"jsonrpc": "2.0", "method": method}) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        """后台循环：按行读，按 id 分发到 pending future；无 id 的（通知/服务器请求）忽略。"""
        assert self._proc and self._proc.stdout
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:  # EOF：进程退了
                break
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue  # 协议通道里混进的杂音行，跳过
            if message.get("id") is None or message.get("id") not in self._pending:
                continue
            fut = self._pending[message["id"]]
            if fut.done():
                continue
            if "error" in message:
                err = message["error"]
                fut.set_exception(McpError(f"{err.get('code')}: {err.get('message')}"))
            else:
                fut.set_result(message.get("result", {}))


def _content_to_text(content: Any) -> str:
    """MCP 的返回是 content 数组（text/image/resource...），取全部文本块拼接。"""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


def load_mcp_config(path) -> dict[str, dict[str, Any]]:
    """读 mcp.json（Claude Desktop 同格式）。没有文件 = 没有外部工具，正常情况。"""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[mcp] {p} 解析失败，忽略所有外部工具：{exc}", file=sys.stderr)
        return {}
    servers = data.get("mcpServers") or {}
    out: dict[str, dict[str, Any]] = {}
    for name, spec in servers.items():
        command = spec.get("command")
        if not command:
            continue
        args = spec.get("args") or []
        out[name] = {"command": [command, *args], "env": spec.get("env") or {}}
    return out
