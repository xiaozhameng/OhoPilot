"""命令行界面：内核的第一个客户端。

    oho                     交互式对话
    oho "帮我把这个目录整理一下"   带一句提示词直接开始
    oho run "..."            非交互，跑完即退（适合脚本调用）
    oho serve                启动 HTTP 服务端
    oho doctor               体检：模型、密钥、工具、技能
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from .config import Config
from .core import events as ev
from .core.llm import describe, has_credentials
from .runtime import Runtime, build_runtime

console = Console()


def _approver_factory() -> object:
    """CLI 下的审批：危险操作逐条问用户。"""

    async def approver(name: str, arguments: dict, reason: str) -> bool:
        console.print()
        console.print(
            Panel(
                f"[bold yellow]{name}[/]\n参数：{arguments}\n\n{reason}",
                title="[yellow]需要你确认[/]",
                border_style="yellow",
            )
        )
        answer = await asyncio.to_thread(
            Confirm.ask, "  允许执行吗？", default=False
        )
        return bool(answer)

    return approver


async def _render(rt: Runtime, session_id: str, text: str) -> None:
    """消费事件流并渲染。工具调用显示成一行摘要，正文走 Markdown。"""
    buffer: list[str] = []
    started = False

    async for event in rt.agent.run(session_id, text):
        if event.type == "text":
            if not started:
                console.print()
                started = True
            buffer.append(event.data.get("delta", ""))
            console.print(event.data.get("delta", ""), end="", soft_wrap=True, highlight=False)
        elif event.type == "tool_call":
            console.print(
                f"\n[dim]→ {event.data['name']}({_short(event.data.get('arguments', {}))})[/]"
            )
        elif event.type == "tool_result":
            flag = "[red]✗[/]" if event.data.get("is_error") else "[green]✓[/]"
            console.print(f"[dim]{flag} {event.data['name']} 返回 {len(event.data.get('content', ''))} 字符[/]")
        elif event.type == "usage":
            console.print(
                f"\n[dim]tokens: {event.data['total_tokens']} "
                f"(prompt {event.data['prompt_tokens']} / completion {event.data['completion_tokens']})[/]"
            )
        elif event.type == "error":
            console.print(f"\n[bold red]出错了：[/]{event.data['message']}")
        elif event.type == "done":
            pass

    if not started:
        console.print("[dim](模型没有返回文本)[/]")
    console.print()


def _short(value: object, limit: int = 90) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."


def cmd_doctor(rt: Runtime) -> int:
    console.print(Panel.fit("[bold]OhoPilot 体检[/]", border_style="cyan"))
    ok = True

    model_ok = has_credentials(rt.cfg)
    console.print(f"模型      {describe(rt.cfg)}  {'[green]OK[/]' if model_ok else '[red]缺密钥[/]'}")
    if not model_ok:
        ok = False
        console.print("          [yellow]→ 在 .env 里设 OHOPILOT_API_KEY，或 export DEEPSEEK_API_KEY=...[/]")

    console.print(f"工作目录  {rt.cfg.workspace}")
    console.print(f"配置目录  {rt.cfg.home}")
    console.print(f"工具      {', '.join(rt.registry.names())}")

    skills = rt.skills.scan()
    console.print(f"技能      {len(skills)} 个" + (f"：{', '.join(s.name for s in skills)}" if skills else ""))
    console.print(f"会话      {len(rt.store.list_sessions())} 个")

    import importlib.util

    has_server = importlib.util.find_spec("fastapi") is not None
    console.print(f"服务端    {'已安装 fastapi' if has_server else '未安装（pip install -e \".[server]\" 后可开）'}")

    return 0 if ok else 1


async def cmd_interactive(rt: Runtime, first: str = "") -> None:
    session_id = rt.store.create_session(title="CLI 会话")
    console.print(
        Panel.fit(
            f"[bold cyan]OhoPilot[/]  [dim]「哦豁」[/]\n"
            f"model: {rt.cfg.model}\n"
            f"workspace: {rt.cfg.workspace}\n\n"
            "输入内容开始对话；[bold]/exit[/] 退出，[bold]/new[/] 新会话，[bold]/tools[/] 看工具",
            border_style="cyan",
        )
    )
    if first:
        await _render(rt, session_id, first)

    while True:
        try:
            line = await asyncio.to_thread(console.input, "\n[bold cyan]你 ›[/] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见。[/]")
            return

        line = line.strip()
        if not line:
            continue
        if line in {"/exit", "/quit", ":q"}:
            console.print("[dim]再见。[/]")
            return
        if line == "/new":
            session_id = rt.store.create_session(title="CLI 会话")
            console.print("[dim]已开新会话。[/]")
            continue
        if line == "/tools":
            for name in rt.registry.names():
                tool = rt.registry.get(name)
                console.print(f"  [bold]{name}[/] — {(tool.description or '').splitlines()[0]}")
            continue

        await _render(rt, session_id, line)


async def cmd_run(rt: Runtime, prompt: str) -> int:
    session_id = rt.store.create_session(title=prompt[:40])
    chunks: list[str] = []
    async for event in rt.agent.run(session_id, prompt):
        if event.type == "text":
            chunks.append(event.data.get("delta", ""))
        elif event.type == "tool_call":
            print(f"[tool] {event.data['name']}", file=sys.stderr)
        elif event.type == "error":
            print(f"[error] {event.data['message']}", file=sys.stderr)
            return 1
    print("".join(chunks))
    return 0


def cmd_serve(rt: Runtime) -> int:
    try:
        import uvicorn
    except ImportError:
        console.print("[red]未安装服务端依赖。[/] 先执行：pip install -e \".[server]\"")
        return 1

    from .server.app import create_app

    app = create_app(rt)
    console.print(
        f"[cyan]OhoPilot 服务端启动[/] http://{rt.cfg.host}:{rt.cfg.port}\n"
        f"[dim]model: {rt.cfg.model}    workspace: {rt.cfg.workspace}[/]"
    )
    uvicorn.run(app, host=rt.cfg.host, port=rt.cfg.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oho",
        description="OhoPilot —— 跑在你电脑上、会自己长技能的中文办公 agent",
    )
    parser.add_argument("prompt", nargs="*", help="直接带一句提示词开始")
    parser.add_argument("-C", "--cwd", default="", help="指定工作目录")
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="非交互执行一条提示词后退出")
    run_parser.add_argument("prompt", nargs="+", help="提示词")

    sub.add_parser("serve", help="启动 HTTP 服务端")
    sub.add_parser("doctor", help="检查配置与环境")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    workspace = Path(args.cwd).expanduser() if args.cwd else None
    rt = build_runtime(workspace, approver=_approver_factory())  # type: ignore[arg-type]

    try:
        if args.command == "doctor":
            return cmd_doctor(rt)
        if args.command == "serve":
            return cmd_serve(rt)
        if args.command == "run":
            return asyncio.run(cmd_run(rt, " ".join(args.prompt)))

        if not has_credentials(rt.cfg):
            console.print(
                "[yellow]还没配密钥。[/] 在项目目录建 .env 写入：\n"
                "  OHOPILOT_API_KEY=sk-xxxx\n"
                "或先跑 [bold]oho doctor[/] 看体检报告。"
            )
            return 1

        first = " ".join(args.prompt) if args.prompt else ""
        asyncio.run(cmd_interactive(rt, first))
        return 0
    finally:
        rt.close()


if __name__ == "__main__":
    raise SystemExit(main())
