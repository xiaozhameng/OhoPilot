"""HTTP + SSE 服务端。

三个必须守住的约束（第 15 步的设计前提），每个都有对应用例：
  1. 事件流是 SSE 且带 type 字段；
  2. 同一会话串行（per-session 锁），否则历史会交错；
  3. 审批用 Future 挂起协程，不阻塞事件循环，超时按拒绝。

全部走 httpx.ASGITransport：不需要真的起端口，但走的是真实的 ASGI 调用链。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from ohopilot.core import events as ev
from ohopilot.server import app as server_app
from ohopilot.server.app import create_app, lifespan
from ohopilot.server.approvals import ApprovalBroker


class FakeAgent:
    """替换真实 Agent：不发模型请求，只按剧本吐事件。"""

    def __init__(self, chunks: tuple[str, ...] = ("你好", "，世界"), delay: float = 0.01):
        self.chunks = chunks
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.questions: list[str] = []

    async def run(self, question, history=None):
        self.questions.append(question)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            for chunk in self.chunks:
                yield ev.text(chunk)
            yield ev.usage(1, 2, 3)
            yield ev.done("")
        finally:
            self.active -= 1


class ApprovingAgent:
    """会触发一次审批的假 Agent，用来测 /approvals 的完整回路。"""

    def __init__(self, broker: ApprovalBroker):
        self.broker = broker

    async def run(self, question, history=None):
        yield ev.approval_request("call-1", "fs_write_file", {"path": "a.txt"}, "写操作")
        allowed = await self.broker.approver("call-1", "fs_write_file", {}, "写操作")
        yield ev.text("已允许" if allowed else "已拒绝")
        yield ev.done("")


@pytest_asyncio.fixture
async def server(monkeypatch, tmp_path):
    """起一个隔离的服务端：home 在临时目录，密钥校验恒为真。"""
    monkeypatch.setenv("OHOPILOT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(server_app, "has_credentials", lambda cfg: True)

    app = create_app()
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield SimpleNamespace(client=client, app=app, rt=app.state.rt)


# ---------- 基础端点 ----------

async def test_health_报告配置与工具清单(server):
    resp = await server.client.get("/health")
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert set(body["tools"]) == {
        "fs_read_file", "fs_write_file", "fs_list_dir", "fs_search",
        "web_fetch", "skill_load", "memory_append", "shell_run",
    }
    assert body["pending_approvals"] == 0


async def test_没有密钥时_chat_返回_503(server, monkeypatch):
    monkeypatch.setattr(server_app, "has_credentials", lambda cfg: False)
    resp = await server.client.post("/chat", json={"message": "你好"})
    assert resp.status_code == 503


async def test_sessions_初始为空(server):
    resp = await server.client.get("/sessions")
    assert resp.json() == {"sessions": []}


# ---------- 一轮问答 ----------

async def test_chat_返回_SSE_流并落库(server):
    server.rt.agent = FakeAgent()

    resp = await server.client.post("/chat", json={"message": "在吗"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    session_id = resp.headers["x-session-id"]
    assert len(session_id) == 12

    # 每行都是 `data: {json}`，且 json 里带 type 字段（一个 onmessage 就能分派）
    payloads = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    kinds = [p["type"] for p in payloads]
    assert kinds[0] == "text"
    assert kinds[-1] == "done"
    assert "".join(p["delta"] for p in payloads if p["type"] == "text") == "你好，世界"
    assert payloads[-1]["session_id"] == session_id

    # 双写：SQLite 里能看到这一问一答
    history = server.rt.store.load_history(session_id)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "在吗"


async def test_chat_带_session_id_时接续同一会话(server):
    server.rt.agent = FakeAgent()
    first = await server.client.post("/chat", json={"message": "第一问"})
    session_id = first.headers["x-session-id"]

    second = await server.client.post(
        "/chat", json={"message": "第二问", "session_id": session_id}
    )

    assert second.headers["x-session-id"] == session_id
    history = server.rt.store.load_history(session_id)
    assert [m["content"] for m in history if m["role"] == "user"] == ["第一问", "第二问"]


async def test_空白_message_被拒绝(server):
    assert (await server.client.post("/chat", json={"message": ""})).status_code == 422
    assert (await server.client.post("/chat", json={"message": "   "})).status_code == 422


async def test_同一会话的请求串行执行(server):
    """两个请求同时改一个会话的历史，顺序会交错 —— 锁必须把它们排开。"""
    agent = FakeAgent(delay=0.05)
    server.rt.agent = agent

    await asyncio.gather(
        server.client.post("/chat", json={"message": "A", "session_id": "same"}),
        server.client.post("/chat", json={"message": "B", "session_id": "same"}),
    )

    assert agent.max_active == 1


async def test_不同会话可以并发(server):
    agent = FakeAgent(delay=0.05)
    server.rt.agent = agent

    await asyncio.gather(
        server.client.post("/chat", json={"message": "A", "session_id": "s1"}),
        server.client.post("/chat", json={"message": "B", "session_id": "s2"}),
    )

    assert agent.max_active == 2


async def test_会话详情回放历史(server):
    server.rt.agent = FakeAgent()
    resp = await server.client.post("/chat", json={"message": "记住这句话"})
    session_id = resp.headers["x-session-id"]

    detail = await server.client.get(f"/sessions/{session_id}")

    assert detail.status_code == 200
    assert detail.json()["messages"][0]["content"] == "记住这句话"


async def test_不存在的会话返回_404(server):
    assert (await server.client.get("/sessions/nope")).status_code == 404


async def test_会话列表带标题(server):
    server.rt.agent = FakeAgent()
    await server.client.post("/chat", json={"message": "第一句就是标题"})

    sessions = (await server.client.get("/sessions")).json()["sessions"]
    assert sessions[0]["title"] == "第一句就是标题"


# ---------- 审批回路 ----------

async def test_未知_call_id_返回_404(server):
    resp = await server.client.post("/approvals/never-existed", json={"allow": True})
    assert resp.status_code == 404


async def test_审批全链路_前端允许后继续执行(server):
    """这是服务端最巧的一处：请求挂在 Future 上，另一个 HTTP 请求把它唤醒。"""
    server.rt.agent = ApprovingAgent(server.app.state.broker)

    chat = asyncio.create_task(server.client.post("/chat", json={"message": "写个文件"}))

    # 等到审批真的挂起（事件流已经把 approval_request 推出去了）
    for _ in range(200):
        if server.app.state.broker.pending_count:
            break
        await asyncio.sleep(0.01)
    assert server.app.state.broker.pending_count == 1

    answer = await server.client.post("/approvals/call-1", json={"allow": True})
    assert answer.json() == {"resolved": True, "call_id": "call-1", "allow": True}

    resp = await chat
    assert "已允许" in resp.text
    assert "approval_request" in resp.text


async def test_审批拒绝时按拒绝继续(server):
    server.rt.agent = ApprovingAgent(server.app.state.broker)
    chat = asyncio.create_task(server.client.post("/chat", json={"message": "写个文件"}))

    for _ in range(200):
        if server.app.state.broker.pending_count:
            break
        await asyncio.sleep(0.01)

    await server.client.post("/approvals/call-1", json={"allow": False})
    resp = await chat
    assert "已拒绝" in resp.text


# ---------- 设置端点 ----------

async def test_设置可以换模型且_health_同步(server):
    resp = await server.client.post("/settings", json={"model": "deepseek-chat"})

    assert resp.status_code == 200
    assert resp.json()["model"] == "deepseek-chat"
    # 改的是同一个 cfg 对象，所以 /health 立刻看到
    assert (await server.client.get("/health")).json()["model"] == "deepseek-chat"


async def test_空模型名被拒绝(server):
    resp = await server.client.post("/settings", json={"model": "   "})
    assert resp.status_code == 400


async def test_工作目录必须已存在(server):
    resp = await server.client.post("/settings", json={"workspace": "/tmp/no-such-dir-oho"})
    assert resp.status_code == 400
    assert "目录不存在" in resp.json()["detail"]


async def test_合法目录切换立即生效(server, tmp_path):
    target = tmp_path / "ws-switched"
    target.mkdir()

    resp = await server.client.post("/settings", json={"workspace": str(target)})

    assert resp.json()["workspace"] == str(target.resolve())


async def test_已构建时根路径返回前端页面(server):
    resp = await server.client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert b'id="root"' in resp.content


# ---------- ApprovalBroker 单元行为 ----------

async def test_broker_挂起直到有人回答():
    broker = ApprovalBroker(timeout=5)

    task = asyncio.create_task(broker.approver("c1", "shell_run", {}, "理由"))
    await asyncio.sleep(0.01)
    assert broker.pending_count == 1

    assert broker.resolve("c1", True) is True
    assert await task is True
    assert broker.pending_count == 0  # 唤醒后立刻清理，别留悬空 Future


async def test_broker_超时按拒绝():
    broker = ApprovalBroker(timeout=0.05)
    assert await broker.approver("c1", "x", {}, "r") is False


async def test_broker_重复回答返回_False():
    """已经答过 / 已超时的 call_id 不该再被改一次。"""
    broker = ApprovalBroker(timeout=5)
    task = asyncio.create_task(broker.approver("c1", "x", {}, "r"))
    await asyncio.sleep(0.01)

    assert broker.resolve("c1", False) is True
    assert broker.resolve("c1", True) is False
    assert await task is False


async def test_broker_多个会话各挂一个互不干扰():
    broker = ApprovalBroker(timeout=5)
    a = asyncio.create_task(broker.approver("a", "x", {}, "r"))
    b = asyncio.create_task(broker.approver("b", "x", {}, "r"))
    await asyncio.sleep(0.01)
    assert broker.pending_count == 2

    broker.resolve("b", True)
    assert await b is True
    assert not a.done()

    broker.resolve("a", False)
    assert await a is False


async def test_客户端断开时审批按拒绝处理():
    """SSE 断开会取消协程，不能让它一直挂着。"""
    broker = ApprovalBroker(timeout=5)
    task = asyncio.create_task(broker.approver("c1", "x", {}, "r"))
    await asyncio.sleep(0.01)

    task.cancel()
    try:
        result = await task
    except asyncio.CancelledError:
        result = False  # 两种收敛都算「没有挂住」，都不该是 True

    assert result is False
    assert broker.pending_count == 0
