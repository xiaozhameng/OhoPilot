"""最小命令行入口：一句话进去，流式结果出来。

10b 起接上了审批：危险操作会在终端弹 y/n 确认。
会话持久化、服务端仍是后面的步骤。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from rich.console import Console

from ohopilot.config import Config
from ohopilot.core.agent import Agent
from ohopilot.core.llm import has_credentials
from ohopilot.tools.base import ToolRegistry
from ohopilot.tools.fs import build_fs_tools

console = Console()

#: 终端展示用：参数太长就截断。既防刷屏，也防敏感内容（如 .env 内容）整段上屏
BRIEF_LIMIT = 160


def _brief(arguments: dict[str, Any], limit: int = BRIEF_LIMIT) -> str:
    text = str(arguments)
    return text if len(text) <= limit else text[:limit] + "…[已截断]"


async def terminal_approver(name: str, arguments: dict[str, Any], reason: str) -> bool:
    """终端里的审批员：把危险操作亮出来，等用户敲 y/n。

    决策权在内核（要不要问），交互在界面（怎么问）——
    将来服务端换一个 approver 实现即可，agent 不用动。
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


def build_agent() -> Agent:
    """装配：配置 → 工具注册表 → Agent。"""
    cfg = Config.load()
    cfg.ensure_dirs()
    registry = ToolRegistry(build_fs_tools(cfg))
    return Agent(cfg, registry, approver=terminal_approver)


async def ask(agent: Agent, question: str, history: list[dict[str, Any]]) -> None:
    """问一轮，把模型的文字增量直接写到终端。"""
    answer: list[str] = []

    async for event in agent.run(question, history):
        kind = event.type

        if kind == "text":
            delta = event.data["delta"]
            answer.append(delta)
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

        elif kind == "error":
            console.print()
            console.print(f"[red]出错：{event.data['message']}[/red]")

        elif kind == "usage":
            console.print(f"\n[dim]（本轮 {event.data['total_tokens']} tokens）[/dim]")

    # 把这一问一答存进内存里的上下文（第 11 步会换成 SQLite）
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": "".join(answer)})


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="oho",
        description="OhoPilot —— 跑在你电脑上的中文办公 agent",
    )
    parser.add_argument("question", nargs="*", help="要问的问题；不提供则进入交互模式")
    args = parser.parse_args()

    agent = build_agent()
    cfg = agent.cfg

    if not has_credentials(cfg):
        console.print("[red]还没配置模型密钥。[/red]请在项目 .env 里填 OHOPILOT_API_KEY")
        raise SystemExit(1)

    question = " ".join(args.question).strip()

    # 模式一：带问题参数 → 答一次就退出（便于写脚本 / 管道）
    if question:
        asyncio.run(ask(agent, question, []))
        console.print()
        return

    # 模式二：不带参数 → 交互模式
    console.print(f"[bold]OhoPilot[/bold]  模型：{cfg.model}")
    console.print(f"工作目录：{cfg.workspace}")
    console.print("直接输入问题开始。内置命令：/exit 退出，/new 清空上下文\n")

    history: list[dict[str, Any]] = []
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
                history.clear()
                console.print("[dim]上下文已清空[/dim]\n")
                continue

            asyncio.run(ask(agent, line, history))
            console.print()


if __name__ == "__&#8203;main__":
    main()
