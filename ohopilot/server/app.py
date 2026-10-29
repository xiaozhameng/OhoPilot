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
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ohopilot.config import Config
from ohopilot.core import events as ev
from ohopilot.core.llm import has_credentials
from ohopilot.memory.longterm import (
    MAX_MEMORY_CHARS,
    MAX_MEMORY_FILE_CHARS,
    load_longterm,
    read_longterm_raw,
    write_longterm_raw,
)
from ohopilot.mcp.client import load_mcp_config
from ohopilot.runtime import Runtime
from ohopilot.server.approvals import ApprovalBroker
from ohopilot.skills.discovery import discover_repo_skills
from ohopilot.skills.installer import install_skill
from ohopilot.skills.loader import load_skills
from ohopilot.tools.grants import GrantStore

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
    #: 新会话归到哪个空间（不传 = 默认空间）。老会话已经归过空间，这里被忽略。
    space_id: int | None = Field(default=None, description="新会话所属空间")


class ApprovalAnswer(BaseModel):
    allow: bool
    #: 授权的范围：once=仅本次，file=这个文件，dir=这个目录及子目录。
    #: 只有 allow=true 时才有意义——拒绝不该顺手记住任何规则。
    kind: Literal["once", "file", "dir"] = "once"
    path: str = Field(default="", description="kind=file/dir 时的绝对路径")


class AliasBody(BaseModel):
    """会话元信息：别名 + （可选）归属空间。两者都是展示层的事。"""

    alias: str = Field(default="", max_length=80)
    space_id: int | None = None


class SpaceBody(BaseModel):
    """新建/修改空间。

    folder 三态，别混：
      - 不传（None）→ 不动现有绑定（改名时走这条，不能顺手把目录清掉）
      - 空串        → 解绑
      - 绝对路径    → 绑定它
    """

    name: str = Field(default="", max_length=60)
    folder: str | None = Field(default=None, max_length=400)


class MemoryBody(BaseModel):
    """长期记忆的整文件覆写。长度校验交给 write_longterm_raw（给 422 带可读理由）。"""

    content: str


class SettingsBody(BaseModel):
    """只允许改这两项：其余（home/审批超时/轮次上限）属于进程级配置，改了下一次启动才生效，
    放在界面上改会让人以为立刻生效，不如不给入口。"""

    model: str | None = Field(default=None, description="模型名，如 deepseek-v4-flash")
    workspace: str | None = Field(default=None, description="工作目录，必须是已存在的绝对路径")


class SkillInstallBody(BaseModel):
    """安装来源两种：http(s) 直链 / 本地路径。仓库发现出来的 raw_url 也走直链。"""

    source: str = Field(min_length=1)
    confirm_risky: bool = Field(default=False, description="审计有 warning 时需显式确认")
    replace: bool = Field(default=False, description="同名技能已存在时是否覆盖")


class SkillDiscoverBody(BaseModel):
    repo: str = Field(min_length=1, description="owner/repo 或 GitHub 仓库地址")


class McpServerBody(BaseModel):
    """Claude Desktop 同格式：command + args + env。名称归一化成合法标识符。"""

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


def _checked_folder(raw: str | None) -> str | None:
    """校验空间要绑定的目录。

    None / 空串原样返回（= 不改 / 解绑），非空必须是**已存在的目录**，
    否则 422 当场说清楚——写错一个字母就让整个空间的会话跑回默认目录，
    那种"文件写到别的项目里去了"的故障排查起来极其费劲。
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return ""
    target = Path(text).expanduser()
    if not target.is_dir():
        raise HTTPException(status_code=422, detail=f"目录不存在：{target}")
    return str(target.resolve())


def _health(rt: Runtime, broker: ApprovalBroker) -> dict[str, Any]:
    return {
        "ok": has_credentials(rt.cfg),
        "model": rt.cfg.model,
        # 这里是**默认**工作目录（空间没绑目录时用它），不是某个会话此刻的目录：
        # /health 是进程级接口，不知道前端正在看哪个会话。按会话的目录由
        # Runtime.session_workspace() 解析，前端拿 space.folder 自己算展示值。
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
        """改运行时配置。改的是 rt.cfg 这个**同一个对象**，所以立刻生效：
        模型名读 cfg.model；工作目录这里改的是**默认值**（cfg.workspace），
        会话所在空间绑了目录时用那个目录，不读这个默认值。"""
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
    async def list_sessions(request: Request, limit: int = 200) -> dict[str, Any]:
        """会话列表（带别名、时间、条数、所属空间）。侧栏按空间分组就靠它。"""
        rt: Runtime = request.app.state.rt
        return {"sessions": rt.store.recent_sessions(min(limit, 500))}

    @app.get("/spaces")
    async def list_spaces(request: Request) -> dict[str, Any]:
        """空间列表（含各自会话数）。默认空间永远在第一位。"""
        rt: Runtime = request.app.state.rt
        return {"spaces": rt.store.list_spaces()}

    @app.post("/spaces")
    async def create_space(body: SpaceBody, request: Request) -> dict[str, Any]:
        rt: Runtime = request.app.state.rt
        try:
            return rt.store.create_space(body.name, _checked_folder(body.folder) or "")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/spaces/{space_id}")
    async def patch_space(space_id: int, body: SpaceBody, request: Request) -> dict[str, Any]:
        """改名或绑定目录。name 传空串表示"不改"，folder 不传表示"不动"。"""
        rt: Runtime = request.app.state.rt
        try:
            return rt.store.update_space(
                space_id,
                name=body.name or None,
                folder=_checked_folder(body.folder),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/spaces/{space_id}")
    async def remove_space(space_id: int, request: Request) -> dict[str, Any]:
        """删空间：里面的会话退回默认空间，不删会话。"""
        rt: Runtime = request.app.state.rt
        try:
            rt.store.delete_space(space_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"deleted": space_id, "moved_to": rt.store.DEFAULT_SPACE_ID}

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

    # ---- 开发者控制台（第 26 步）----
    # 接口按 /console/api/* 分组（api 前缀是为了和页面路径 /console/calls 等
    # 区分开：页面导航和 API 同名会撞路由，浏览器直接收到 JSON）。单用户阶段
    # 不做鉴权，将来拆独立服务时
    # 这组前缀整体迁走即可，主界面接口一行不用动（用户拍板的预留边界）。

    @app.get("/console/api/overview")
    async def console_overview(request: Request) -> dict[str, Any]:
        """跨会话聚合：token 总账 / 按模型拆分 / 工具排行 / 每会话成本 / 趋势 / 失败样本。"""
        rt: Runtime = request.app.state.rt
        return rt.store.console_overview()

    @app.get("/console/api/sessions/{session_id}")
    async def console_session(session_id: str, request: Request) -> dict[str, Any]:
        """控制台点开单个会话的下钻明细：逐轮模型调用 + 逐次工具调用（带轮次）。"""
        rt: Runtime = request.app.state.rt
        return rt.store.session_detail(session_id)

    @app.patch("/console/api/sessions/{session_id}")
    async def console_rename_session(
        session_id: str, body: AliasBody, request: Request
    ) -> dict[str, Any]:
        """改会话别名 / 移动会话到另一个空间。都只动元信息，不碰 messages。"""
        rt: Runtime = request.app.state.rt
        if not rt.store.session_meta(session_id)["messages"]:
            raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
        alias = rt.store.set_alias(session_id, body.alias)
        space_id = body.space_id
        if space_id is not None:
            try:
                rt.store.set_session_space(session_id, space_id)
            except KeyError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"session_id": session_id, "alias": alias, "space_id": space_id}

    @app.get("/console/api/grants")
    async def console_grants(request: Request) -> dict[str, Any]:
        """目录白名单：谁被放行了、在哪个目录、什么时候加的。

        必须给界面看：静默放行是安全事故的温床——用户得能一眼看到
        "agent 现在可以随便写哪里"，并且一键撤销。
        """
        rt: Runtime = request.app.state.rt
        store = GrantStore(rt.cfg.home)
        return {
            "file": str(store.file),
            "grants": [g.to_dict() for g in store.list()],
        }

    @app.delete("/console/api/grants/{grant_id}")
    async def console_delete_grant(grant_id: str, request: Request) -> dict[str, Any]:
        rt: Runtime = request.app.state.rt
        if not GrantStore(rt.cfg.home).remove(grant_id):
            raise HTTPException(status_code=404, detail=f"没有这条白名单：{grant_id}")
        return {"deleted": grant_id}

    @app.get("/console/api/memory")
    async def console_get_memory(request: Request) -> dict[str, Any]:
        """长期记忆原文 + 注入规则，给控制台的「记忆」页。

        返回的是文件原文（含 > 注释行）：编辑器里看到的和落盘的一致，
        不然用户改完保存，文件里却多出/少了几行，无从对账。
        injected_chars 是按注入规则（去注释、截 4000）实际会发给模型的部分，
        两个数并排，用户才知道"我写的这些模型到底看得到多少"。
        """
        rt: Runtime = request.app.state.rt
        content = read_longterm_raw(rt.cfg)
        injected = load_longterm(rt.cfg)
        return {
            "path": str(rt.cfg.memory_file),
            "content": content,
            "chars": len(content),
            "injected_chars": len(injected),
            "limit": MAX_MEMORY_CHARS,
            "file_limit": MAX_MEMORY_FILE_CHARS,
        }

    @app.put("/console/api/memory")
    async def console_put_memory(body: MemoryBody, request: Request) -> dict[str, Any]:
        """整体覆写长期记忆。这是人工整理（删/并/改写），和 agent 的追加互补。"""
        rt: Runtime = request.app.state.rt
        try:
            chars = write_longterm_raw(rt.cfg, body.content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        injected = load_longterm(rt.cfg)
        return {
            "path": str(rt.cfg.memory_file),
            "chars": chars,
            "injected_chars": len(injected),
        }

    @app.get("/console/api/calls")
    async def console_calls(
        request: Request, limit: int = 100, session_id: str | None = None
    ) -> dict[str, Any]:
        """模型调用流水（最新在前），全局或按会话过滤。

        独立成一个接口而不是塞进 overview：流水会一直涨，总账是聚合值
        不会涨，两者缓存策略和刷新频率完全不同，混在一起每次都白拉全量。
        """
        rt: Runtime = request.app.state.rt
        return {"calls": rt.store.llm_calls(session_id, limit=min(limit, 500))}

    @app.get("/console/api/llm-calls/{call_id}")
    async def console_call_detail(call_id: int, request: Request) -> dict[str, Any]:
        """单次模型调用的请求快照（截 4KB 的 messages JSON）。

        列表和树都不带快照——一条 4KB，塞在列表里既慢又刷屏；
        用户点「看请求原文」时才按需拉这一条。
        """
        rt: Runtime = request.app.state.rt
        detail = rt.store.llm_call_snapshot(call_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="调用记录不存在")
        return detail

    # ---- 扩展中心（使用者视角）：技能 + MCP 的可视化管理 ----
    # 和 /console/*（开发者看数据）分开：这里改的是「agent 能干什么」的配置。
    # 复用第 24 步的 install_skill 管道，UI 只是换了入口，审计规则一套不差。

    @app.get("/skills")
    async def list_skills(request: Request) -> dict[str, Any]:
        rt: Runtime = request.app.state.rt
        skills = load_skills(rt.cfg)
        return {
            "dir": str(rt.cfg.skills_dir),
            "skills": [
                {"name": s.name, "description": s.description, "file": s.path.name}
                for s in skills
            ],
        }

    @app.post("/skills/discover")
    async def discover_skills(body: SkillDiscoverBody) -> dict[str, Any]:
        try:
            candidates = discover_repo_skills(body.repo)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 —— 网络/限流/仓库不存在都给可读信息
            raise HTTPException(status_code=502, detail=f"GitHub 请求失败：{exc}") from exc
        return {"repo": body.repo, "candidates": candidates}

    @app.post("/skills/install")
    async def install_skill_endpoint(body: SkillInstallBody, request: Request) -> dict[str, Any]:
        rt: Runtime = request.app.state.rt
        path, findings, message = install_skill(
            body.source,
            rt.cfg,
            confirm_risky=body.confirm_risky,
            replace=body.replace,
        )
        # findings 原样给前端：审计报告要在页面上看得见，不能只给一句结论
        return {
            "ok": path is not None,
            "message": message,
            "findings": [
                {
                    "severity": f.severity,
                    "label": f.label,
                    "line_no": f.line_no,
                    "excerpt": f.excerpt,
                }
                for f in findings
            ],
        }

    @app.delete("/skills/{name}")
    async def delete_skill(name: str, request: Request) -> dict[str, Any]:
        rt: Runtime = request.app.state.rt
        # 名字白名单校验：防 ../ 之类的路径穿越，技能名本来就是标识符
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise HTTPException(status_code=422, detail="技能名不合法")
        target = rt.cfg.skills_dir / f"{name}.md"
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"技能不存在：{name}")
        target.unlink()
        return {"deleted": name}

    @app.get("/mcp")
    async def get_mcp(request: Request) -> dict[str, Any]:
        """返回 mcp.json 的原始内容（Claude Desktop 同格式）+ 文件路径。"""
        rt: Runtime = request.app.state.rt
        mcp_path = rt.cfg.home / "mcp.json"
        if mcp_path.exists():
            try:
                data = json.loads(mcp_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise HTTPException(
                    status_code=500, detail=f"mcp.json 解析失败，请手工检查：{exc}"
                ) from exc
        else:
            data = {}
        return {"path": str(mcp_path), "servers": data.get("mcpServers") or {}}

    @app.post("/mcp/servers")
    async def add_mcp_server(body: McpServerBody, request: Request) -> dict[str, Any]:
        rt: Runtime = request.app.state.rt
        mcp_path = rt.cfg.home / "mcp.json"
        data: dict[str, Any] = {}
        if mcp_path.exists():
            try:
                data = json.loads(mcp_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise HTTPException(
                    status_code=500, detail=f"mcp.json 解析失败，请先手工修复：{exc}"
                ) from exc
        servers: dict[str, Any] = data.setdefault("mcpServers", {})
        # 名称归一化：和工具名同一套规则，mcp__<服务器>__<工具> 拼出来才合法
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", body.name).strip("-")
        if not safe_name:
            raise HTTPException(status_code=422, detail="服务器名不合法")
        servers[safe_name] = {
            "command": body.command,
            "args": body.args,
            "env": body.env,
        }
        mcp_path.parent.mkdir(parents=True, exist_ok=True)
        mcp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {
            "added": safe_name,
            "path": str(mcp_path),
            # MCP 子进程是启动时拉起来的，改配置不影响已运行的服务器
            "note": "已写入 mcp.json，重启 oho-server 后生效",
        }

    @app.delete("/mcp/servers/{name}")
    async def delete_mcp_server(name: str, request: Request) -> dict[str, Any]:
        rt: Runtime = request.app.state.rt
        mcp_path = rt.cfg.home / "mcp.json"
        if not mcp_path.exists():
            raise HTTPException(status_code=404, detail="还没有 mcp.json")
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        servers: dict[str, Any] = data.get("mcpServers") or {}
        if name not in servers:
            raise HTTPException(status_code=404, detail=f"没有这个 MCP 服务器：{name}")
        del servers[name]
        mcp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {"deleted": name, "note": "已写入 mcp.json，重启 oho-server 后生效"}

    @app.post("/chat")
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        rt: Runtime = request.app.state.rt
        if not has_credentials(rt.cfg):
            raise HTTPException(status_code=503, detail="服务端没有配置模型密钥")

        question = body.message.strip()
        if not question:
            raise HTTPException(status_code=422, detail="message 不能为空白")
        new_session = body.session_id is None
        session_id = body.session_id or rt.new_session()
        # 只有新会话才认 space_id：老会话搬空间是显式操作（PATCH），
        # 不能因为浏览器还记着上次选的空间就把历史会话偷偷搬走
        if new_session and body.space_id:
            try:
                rt.store.set_session_space(session_id, body.space_id)
            except KeyError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

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
        if not broker.resolve(call_id, body.allow, body.kind, body.path):
            raise HTTPException(
                status_code=404,
                detail="没有等待中的审批：可能已超时、已回答，或 call_id 不对",
            )
        return {
            "resolved": True,
            "call_id": call_id,
            "allow": body.allow,
            "kind": body.kind if body.allow else "once",
            "path": body.path if body.allow else "",
        }

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

        # /console/* 和 /extensions 是同一个 SPA 的多入口：前端按 pathname
        # 渲染对应页面。后台是多页结构（总览/流水/工具/会话/扩展），
        # 所以 /console 下的所有子路径都要回落到 index.html，否则刷新 404。
        # 返回同一个 index.html，静态资源仍然只从 /assets 拿
        @app.get("/console/{rest:path}", include_in_schema=False)
        @app.get("/console", include_in_schema=False)
        @app.get("/extensions", include_in_schema=False)
        async def admin_index(rest: str = "") -> FileResponse:
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
