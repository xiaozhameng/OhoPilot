""" 最小 MCP stdio 服务器（测试用）：实现协议必需的三个方法

提供一个 echo 工具（原样返回 arguments）和一个 add 工具（a+b），
让集成测试走「真子进程 + 真 stdin/stdout + 真 JSON-RPC」，
而不是 mock——这样协议实现的正确性是被钉住的，不是被假设的。
"""

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "原样返回输入，测试连通性",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "两数相加",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
]


def handle(msg: dict) -> dict | None:
    if msg.get("method") == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-server", "version": "1.0.0"},
            },
        }
    # 无 id 的是通知（如 notifications/initialized），不回信
    if "id" not in msg or msg.get("id") is None:
        return None
    if msg.get("method") == "tools/list":
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": TOOLS}}
    if msg.get("method") == "tools/call":
        args = msg["params"]["arguments"]
        name = msg["params"]["name"]
        if name == "echo":
            text = args["text"]
        elif name == "add":
            text = str(args["a"] + args["b"])
        else:
            # MCP 规范：未知工具返回工具级错误，不能让服务器崩
            return {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True},
            }
        return {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {"content": [{"type": "text", "text": text}]},
        }
    return {
        "jsonrpc": "2.0",
        "id": msg["id"],
        "error": {"code": -32601, "message": f"未知方法 {msg.get('method')}"},
    }


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
