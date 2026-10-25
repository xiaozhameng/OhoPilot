"""HTTP + SSE 服务端：让内核被终端之外的东西使用。

为什么是 SSE 而不是 WebSocket：
  这一轮问答的数据流是**单向**的（服务端持续推事件，客户端只管"开始"和"审批"），
  SSE 就是「一个不结束的 HTTP 响应」，浏览器 EventSource、curl、任何语言都直接能读，
  不需要握手、心跳、重连协议。审批的回传走普通 POST，够用且简单。

三条必须守住的约束（都是第 14 步埋下的坑）：
  1. 一个长驻事件循环：Runtime 在 lifespan 里建、关闭时 close()。
     绝不能在请求里新建 loop——SQLite 连接、子进程 watcher 都绑在创建它的 loop 上。
  2. 同一会话串行：两个请求同时改一个会话的历史，顺序会交错。
     用 per-session 锁，第二次请求等第一次跑完。
  3. 审批不许阻塞循环：ApprovalBroker 用 Future 挂起「这个协程」，不是挂住整个循环。
"""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ohopilot.config import Config
from ohopilot.core import events as ev
from ohopilot.core.llm import has_credentials
from ohopilot.runtime import Runtime
from ohopilot.server.approvals import ApprovalBroker

#: 前端构建产物（web/ 里 npm run build 会输出到这里）
STATIC_DIR = Path(__file__).parent / "static"

#: 上传上限 20MB：文档类够用，也防一次请求把内存吃满
UPLOAD_LIMIT_BYTES = 20 * 1024 * 1024
#: 文件名里不允许出现的字符（Windows 保留字符 + 控制场景），统一替换成下划线
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


@asynccontextmanager
async def lifespan(app: FastAPI):
    """进程级生命周期：启动建 Runtime，退出时关会话库。"""
    cfg = Config.load()
    broker = ApprovalBroker(timeout=cfg.approval_timeout)
    app.state.cfg = cfg
    app.state.broker = broker
    app.state.rt = Runtime.create(approver=broker.approver, cfg=cfg)
    #: session_id → Lock。惰性创建，不用预分配
    app.state.locks = {}
    await app.state.rt.setup_mcp()
    try:
        yield
    finally:
        await app.state.rt.shutdown_mcp()
        app.state.rt.close()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="用户这一轮的问题")
    session_id: str | None = Field(default=None, description="不传就开新会话")


class ApprovalAnswer(BaseModel):
    allow: bool


class SettingsBody(BaseModel):
    """只允许改这两项：其余（home/审批超时/轮次上限）属于进程级配置，改了下一次启动才生效，
    放在界面上改会让人以为立刻生效，不如不给入口。"""

    model: str | None = Field(default=None, description="模型名，如 deepseek-v4-flash")
    workspace: str | None = Field(default=None, description="工作目录，必须是已存在的绝对路径")


def _health(rt: Runtime, broker: ApprovalBroker) -> dict[str, Any]:
    return {
        "ok": has_credentials(rt.cfg),
        "model": rt.cfg.model,
        "workspace": str(rt.cfg.workspace),
        "tools": rt.registry.names(),
        "pending_approvals": broker.pending_count,
    }


def _sse(event: ev.Event) -> str:
    """一个事件 = 一条 SSE 消息。

    只写 data 不写 event: 字段，是刻意的：一旦用了 event: 名字，
    浏览器 EventSource 的 onmessage 就不再触发，必须给每种类型注册监听器。
    data 里带 type 字段，前端一个 onmessage 就能分派。
    """
    return f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"


def create_app() -> FastAPI:
    app = FastAPI(title="OhoPilot", version="0.1.0", lifespan=lifespan)

    # 只放行本机来源：这个服务能执行 shell，不能让任意网页跨域调它
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        return _health(request.app.state.rt, request.app.state.broker)

    @app.post("/settings")
    async def update_settings(body: SettingsBody, request: Request) -> dict[str, Any]:
        """改运行时配置。改的是 rt.cfg 这个**同一个对象**，所以工具立刻就能看到新值：
        fs/shell 的边界读的是 cfg.workspace，模型名读的是 cfg.model。"""
        rt: Runtime = request.app.state.rt

        if body.model is not None:
            model = body.model.strip()
            if not model:
                raise HTTPException(status_code=400, detail="模型名不能为空")
            rt.cfg.model = model

        if body.workspace is not None:
            raw = body.workspace.strip()
            if not raw:
                raise HTTPException(status_code=400, detail="工作目录不能为空")
            # 只接受已存在的目录：写错一个字母就让所有文件工具失效，错误要当场报出来
            target = Path(raw).expanduser()
            if not target.is_dir():
                raise HTTPException(status_code=400, detail=f"目录不存在：{target}")
            rt.cfg.workspace = target.resolve()

        return _health(rt, request.app.state.broker)

    @app.get("/sessions")
    async def list_sessions(request: Request, limit: int = 20) -> dict[str, Any]:
        rt: Runtime = request.app.state.rt
        return {"sessions": rt.store.recent_sessions(limit)}

    @app.get("/sessions/{session_id}")
    async def session_detail(session_id: str, request: Request) -> dict[str, Any]:
        rt: Runtime = request.app.state.rt
        history = rt.load_history(session_id)
        if not history:
            raise HTTPException(status_code=404, detail=f"会话不存在或为空：{session_id}")
        return {"session_id": session_id, "messages": history}

    @app.get("/sessions/{session_id}/todos")
    async def session_todos(session_id: str, request: Request) -> dict[str, Any]:
        """某会话的任务清单快照。

        存在的理由：todo 表按会话持久化了，但前端刷新后只拉得到消息历史，
        没有这个接口清单就「丢了」（数据还在，界面不显示）。
        TodoStore 是全局单例、按 set_session 隔离——HTTP 并发下先设再读
        理论上可能被别的请求插进来改会话号，但清单操作都是同一事件循环里
        的同步短操作（无 await 夹在中间），实际不会交错。
        """
        rt: Runtime = request.app.state.rt
        rt.todos.set_session(session_id)
        return {"session_id": session_id, "items": rt.todos.list_items()}

    @app.post("/files")
    async def upload_file(request: Request, file: UploadFile) -> dict[str, Any]:
        """上传文件到数据家目录的 uploads/ 子目录，返回绝对路径。

        为什么存 ~/.ohopilot/uploads 而不是 workspace：
        上传的是用户的运行时数据，该住数据家目录（和数据库、技能同层），
        安装目录/workspace 保持干净。agent 仍能读——读操作不受工作目录
        限制（那是写操作的边界），模型拿绝对路径就能 fs_read_document。
        重名不覆盖——覆盖用户已有文件太危险，加时间戳后缀另存。
        """
        rt: Runtime = request.app.state.rt
        raw_name = Path(file.filename or "").name  # 只留文件名本身，剥掉任何路径成分
        if not raw_name:
            raise HTTPException(status_code=400, detail="缺少文件名")
        name = _UNSAFE_CHARS.sub("_", raw_name)

        data = await file.read()
        if len(data) > UPLOAD_LIMIT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过上传上限（{UPLOAD_LIMIT_BYTES // (1024 * 1024)}MB）",
            )

        uploads = rt.cfg.home / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / name
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = uploads / f"{target.stem}_{stamp}{target.suffix}"
        target.write_bytes(data)
        # 返回绝对路径：uploads 已不在 workspace 内，相对路径 fs 工具会解析错地方
        return {"path": str(target), "size": len(data)}

    @app.get("/usage")
    async def usage(
        request: Request,
        session_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """用量流水 + 总账。session_id 不传就是全部会话的全局视角。"""
        rt: Runtime = request.app.state.rt
        return {
            "summary": rt.store.usage_summary(session_id),
            "calls": rt.store.llm_calls(session_id, limit=limit),
        }

    @app.post("/chat")
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        rt: Runtime = request.app.state.rt
        if not has_credentials(rt.cfg):
            raise HTTPException(status_code=503, detail="服务端没有配置模型密钥")

        question = body.message.strip()
        if not question:
            raise HTTPException(status_code=422, detail="message 不能为空白")
        session_id = body.session_id or rt.new_session()

        async def stream() -> AsyncIterator[str]:
            # 锁必须在生成器里拿：拿到之前不占住请求，前端能立刻看到响应头
            lock: asyncio.Lock = request.app.state.locks.setdefault(session_id, asyncio.Lock())
            async with lock:
                # 每次现读历史：上一次请求可能刚把它写长
                history = rt.load_history(session_id)
                try:
                    async for event in rt.ask(session_id, question, history):
                        yield _sse(event)
                except asyncio.CancelledError:
                    # 客户端断开（关页面/断网）：让异常继续向上，别吞
                    raise
                except Exception as exc:  # noqa: BLE001 —— 兜住一切，别让前端收到空响应
                    yield _sse(ev.error(f"服务端异常：{exc}"))

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",   # 反代（nginx）别缓冲，否则流式变成一次性
                "X-Session-Id": session_id,  # 新会话的 id 从这里给前端
            },
        )

    @app.post("/approvals/{call_id}")
    async def answer_approval(
        call_id: str, body: ApprovalAnswer, request: Request
    ) -> dict[str, Any]:
        broker: ApprovalBroker = request.app.state.broker
        if not broker.resolve(call_id, body.allow):
            raise HTTPException(
                status_code=404,
                detail="没有等待中的审批：可能已超时、已回答，或 call_id 不对",
            )
        return {"resolved": True, "call_id": call_id, "allow": body.allow}

    # ---- 前端（可选）----
    # 构建过才挂载：开发阶段前端跑在 vite 的 5173，后端不需要知道它的存在。
    # 只挂 /assets 而不是把 StaticFiles 挂到 "/" —— 挂根路径会把 /health 这些接口一起吞掉。
    if (STATIC_DIR / "index.html").is_file():
        assets = STATIC_DIR / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    else:

        @app.get("/", include_in_schema=False)
        async def no_frontend() -> dict[str, str]:
            return {"detail": "前端还没构建。在 web/ 目录跑 npm install && npm run build"}

    return app


def main() -> None:
    """`oho-server` 或 `python -m ohopilot.server` 的入口。"""
    import uvicorn

    cfg = Config.load()
    # 用工厂字符串而不是传 app 对象：uvicorn 的 reload / workers 需要能自己 import
    uvicorn.run(
        "ohopilot.server.app:create_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
