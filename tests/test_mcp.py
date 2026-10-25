""" MCP 客户端测试：真子进程、真协议，不发网

echo_mcp_server.py 是一个最小但真实的 MCP 服务器，
测试通过它验证我们的客户端实现和协议是对得上的。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ohopilot.config import Config
from ohopilot.mcp.bridge import bridge_server, connect_all, mcp_tool_name
from ohopilot.mcp.client import McpError, McpStdioClient, load_mcp_config
from ohopilot.tools.base import ToolRegistry

SERVER = [sys.executable, str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")]


@pytest.fixture
async def client():
    c = McpStdioClient("test", SERVER)
    await c.start()
    yield c
    await c.stop()


async def test_握手拿到服务器信息(client):
    assert client.server_version == "1.0.0"


async def test_拉取工具清单(client):
    tools = await client.list_tools()
    names = {t["name"] for t in tools}
    assert names == {"echo", "add"}
    assert tools[0]["inputSchema"]["type"] == "object"


async def test_调用工具(client):
    assert await client.call_tool("echo", {"text": "你好"}) == "你好"
    assert await client.call_tool("add", {"a": 2, "b": 40}) == "42"


async def test_调用不存在工具得到错误(client):
    with pytest.raises(McpError, match="未知工具"):
        await client.call_tool("nope", {})


async def test_多次调用按id正确配对(client):
    """乱序场景的底线：连发三个请求，各自的响应回到各自的 future。"""
    results = await asyncio_gather_safe(client)
    assert results == ["一", "二", "三"]


async def asyncio_gather_safe(client: McpStdioClient) -> list[str]:
    import asyncio

    calls = [
        client.call_tool("echo", {"text": t})
        for t in ("一", "二", "三")
    ]
    return list(await asyncio.gather(*calls))


async def test_服务器进程被干净回收(client):
    import asyncio

    proc = client._proc
    assert proc is not None and proc.returncode is None
    await client.stop()
    for _ in range(50):  # terminate 是异步生效的，给一点时间
        if proc.returncode is not None:
            break
        await asyncio.sleep(0.02)
    assert proc.returncode is not None


# ---------- bridge ----------


async def test_bridge_把远程工具包成proxy(client):
    proxies = await bridge_server(client, "test")
    assert {p.name for p in proxies} == {mcp_tool_name("test", "echo"), mcp_tool_name("test", "add")}
    p = proxies[0]
    assert p.requires_approval is True
    assert "MCP 服务器 test" in p.description


async def test_connect_all_坏服务器跳过不崩():
    clients, tools = await connect_all({
        "bad": {"command": ["绝对不存在的命令xyz"], "env": {}},
        "good": {"command": SERVER, "env": {}},
    })
    assert set(clients) == {"good"}
    assert {t.name for t in tools} >= {mcp_tool_name("good", "echo")}
    for c in clients.values():
        await c.stop()


# ---------- 配置与 Runtime 集成 ----------


def test_load_mcp_config_各分支(tmp_path):
    missing = load_mcp_config(tmp_path / "no.json")
    assert missing == {}

    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert load_mcp_config(bad) == {}

    good = tmp_path / "mcp.json"
    good.write_text(
        '{"mcpServers": {"a": {"command": "npx", "args": ["-y", "srv"], "env": {"K": "V"}},'
        ' "b": {"command": ""}}}',
        encoding="utf-8",
    )
    parsed = load_mcp_config(good)
    assert parsed == {"a": {"command": ["npx", "-y", "srv"], "env": {"K": "V"}}}


async def test_runtime_setup_mcp_端到端(tmp_path):
    """真 Runtime + 真 mcp.json + 真子进程：工具进 registry、能调、能关。"""
    cfg = Config(workspace=tmp_path, home=tmp_path / "home")
    cfg.ensure_dirs()
    (cfg.home / "mcp.json").write_text(
        '{"mcpServers": {"test": {"command": "%s", "args": ["%s"]}}}'
        % (sys.executable, Path(__file__).parent / "fixtures" / "echo_mcp_server.py"),
        encoding="utf-8",
    )
    from ohopilot.runtime import Runtime

    rt = Runtime.create(cfg=cfg)
    await rt.setup_mcp()
    try:
        name = mcp_tool_name("test", "echo")
        assert name in rt.registry.names()
        tool = rt.registry.get(name)
        assert tool is not None
        result = await tool.safe_run({"text": "集成"})
        assert not result.is_error
        assert "集成" in result.content
    finally:
        await rt.shutdown_mcp()
        rt.close()
