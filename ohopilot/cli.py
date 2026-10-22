"""命令行入口：把 Runtime 吐出来的事件渲染到终端。

这里只有三件事：
  1. 解析参数、决定用哪个会话；
  2. 把事件渲染成人看得懂的终端输出；
  3. 危险操作时弹 y/n 问人（其它入口换成 HTTP 弹窗即可）。

装配（配置/工具/Agent/会话库）在 runtime.py，
编排（压缩摘要/双写存储）也在那儿。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from rich.console import Console

from ohopilot.core.llm import has_credentials
from ohopilot.runtime import Runtime

console = Console()

#: 终端展示用：参数太长就截断。既防刷屏，也防敏感内容（如 .env 内容）整段上屏
BRIEF_LIMIT = 160


def _brief(arguments: dict[str, Any], limit: int = BRIEF_LIMIT) -> str:
    text = str(arguments)
    return text if len(text) <= limit else text[:limit] + "…[已截断]"


async def terminal_approver(name: str, arguments: dict[str, Any], reason: str) -> bool:
    """终端里的审批员：把危险操作亮出来，等用户敲 y/n。

    决策权在内核（要不要问），交互在界面（怎么问）——
    服务端只要换一个实现（推 HTTP 给前端弹窗），agent 不用动。
    """
    console.print()
    console.print(f"[yellow]⚠ 需要确认[/yellow] {name} 想执行一个有风险的操作：{reason}")
    console.print(f"  参数：{_brief(arguments)}")
    try:
        answer = input("  允许执行吗？(y = 允许，其他任意键 = 拒绝) › ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        # 管道 / 脚本场景下没人能回答 —— 问不了就等于拒绝
        return False
    return answer in {"y", "yes"}


async def ask(
    rt: Runtime,
    session_id: str,
    question: str,
    history: list[dict[str, Any]],
) -> None:
    """问一轮，把事件渲染到终端。存储、摘要都由 Runtime 负责。"""
    async for event in rt.ask(session_id, question, history):
        kind = event.type

        if kind == "text":
            delta = event.data["delta"]
            sys.stdout.write(delta)
            sys.stdout.flush()

        elif kind == "tool_call":
            console.print()  # 先换行，免得和流式文字粘在同一行
            console.print(
                f"  · 调用 {event.data['name']} {_brief(event.data['arguments'])}",
                style="dim",
                markup=False,
            )

        elif kind == "tool_result":
            flag = "失败" if event.data["is_error"] else "完成"
            console.print(
                f"  · {flag}，返回 {len(event.data['content'])} 字符",
                style="dim",
            )

        elif kind == "notice":
            console.print(f"[dim]  · {event.data['message']}[/dim]")

        elif kind == "error":
            console.print()
            console.print(f"[red]出错：{event.data['message']}[/red]")

        elif kind == "usage":
            console.print(f"\n[dim]（本轮 {event.data['total_tokens']} tokens）[/dim]")

        # done / approval_request 不需要在这里渲染：
        # 前者是给客户端判断「这轮结束了」的信号，后者的交互在 terminal_approver 里


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="oho",
        description="OhoPilot —— 跑在你电脑上的中文办公 agent",
    )
    parser.add_argument("question", nargs="*", help="要问的问题；不提供则进入交互模式")
    args = parser.parse_args()

    rt = Runtime.create(approver=terminal_approver)
    cfg = rt.cfg

    if not has_credentials(cfg):
        console.print("[red]还没配置模型密钥。[/red]请在项目 .env 里填 OHOPILOT_API_KEY")
        raise SystemExit(1)

    question = " ".join(args.question).strip()

    # 模式一：带问题参数 → 答一次就退出（便于写脚本 / 管道）
    # 每次一次性提问都开新会话：问完即走，不复用上下文
    if question:
        session_id = rt.new_session()
        asyncio.run(ask(rt, session_id, question, []))
        console.print(f"\n[dim]（会话 {session_id} 已存档）[/dim]")
        console.print()
        rt.close()
        return

    # 模式二：不带参数 → 交互模式
    # 有历史会话就自动接续最近的一个：进程重启对话不丢
    session_id = rt.latest_session() or rt.new_session()
    history: list[dict[str, Any]] = rt.load_history(session_id)

    console.print(f"[bold]OhoPilot[/bold]  模型：{cfg.model}")
    console.print(f"工作目录：{cfg.workspace}")
    if history:
        console.print(f"[dim]已接续会话 {session_id}（{len(history)} 条历史消息），/new 开新会话[/dim]")
    console.print("直接输入问题开始。内置命令：/exit 退出，/new 开新会话\n")

    while True:
        try:
            line = input("你 › ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not line:
            continue
        if line in {"/exit", "/quit"}:
            break
        if line == "/new":
            session_id = rt.new_session()
            history.clear()
            console.print(f"[dim]已开新会话 {session_id}（旧会话仍留在库里，随时可查）[/dim]\n")
            continue

        asyncio.run(ask(rt, session_id, line, history))
        console.print()

    rt.close()


if __name__ == "__main__":
    main()
