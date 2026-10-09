"""HTTP 服务端：内核的第二个客户端。

桌面 App、手机 App 将来都接这里，内核本身完全不知道前端长什么样。
接口刻意保持极窄：
    GET  /health                  探活
    GET  /v1/sessions             会话列表
    POST /v1/sessions            新建会话
    GET  /v1/sessions/{id}        某会话的历史
    POST /v1/chat                发消息，SSE 流式返回事件
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core import events as ev
from ..core.llm import describe, has_credentials
from ..runtime import Runtime, build_runtime


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ApprovalRequest(BaseModel):
    call_id: str
    approved: bool


def create_app(runtime: Runtime | None = None) -> FastAPI:
    rt = runtime or build_runtime()

    app = FastAPI(title="OhoPilot", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 本地服务，仅监听 127.0.0.1
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 服务端模式下，危险操作默认走"待审批"队列，由前端调 /v1/approve 决定
    pending: dict[str, asyncio.Future[bool]] = {}

    async def server_approver(name: str, arguments: dict[str, Any], reason: str) -> bool:
        call_id = f"{name}:{id(arguments)}"
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        pending[call_id] = future
        try:
            return await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            return False
        finally:
            pending.pop(call_id, None)

    rt.agent.approver = server_approver

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "model": describe(rt.cfg),
            "credentials": has_credentials(rt.cfg),
            "workspace": str(rt.cfg.workspace),
            "tools": rt.registry.names(),
        }

    @app.get("/v1/sessions")
    async def list_sessions() -> dict[str, Any]:
        return {"sessions": rt.store.list_sessions()}

    @app.post("/v1/sessions")
    async def create_session(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        title = (payload or {}).get("title", "")
        return {"session_id": rt.store.create_session(title)}

    @app.get("/v1/sessions/{session_id}")
    async def session_history(session_id: str) -> dict[str, Any]:
        return {"messages": rt.store.history(session_id)}

    @app.post("/v1/approve")
    async def approve(payload: ApprovalRequest) -> dict[str, Any]:
        future = pending.get(payload.call_id)
        if future is None or future.done():
            raise HTTPException(status_code=404, detail="没有等待中的审批请求")
        future.set_result(payload.approved)
        return {"ok": True}

    @app.post("/v1/chat")
    async def chat(payload: ChatRequest) -> StreamingResponse:
        session_id = payload.session_id or rt.store.create_session()

        async def event_source() -> AsyncIterator[str]:
            try:
                async for event in rt.agent.run(session_id, payload.message):
                    yield f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001
                data = json.dumps(ev.error(str(exc)).to_dict(), ensure_ascii=False)
                yield f"data: {data}\n\n"

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Session-Id": session_id},
        )

    return app


__all__ = ["create_app"]
