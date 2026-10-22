"""执行 shell 命令。

全项目风险最高的工具，所以用三档策略而不是一个 requires_approval 布尔值：

- block：命中危险模式（提权、删根、下载即执行、磁盘操作）→ 直接拒绝，审批也不给；
- ask  ：其余一切（含任何管道 / 重定向 / 串联）→ 弹人工审批；
- allow：只读白名单「且」不含 shell 元字符 → 静默执行。

为什么 allow 要两个条件同时成立：`ls; rm -rf ~` 的第一个词也是 ls。
宁可多弹一次审批，不可误放行一次。
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from pathlib import Path
from typing import Any

from ohopilot.config import Config
from ohopilot.tools.base import Tool, ToolResult

#: 单命令只读白名单（不含 shell 元字符时才放行）
SAFE_SINGLE = {
    "ls", "pwd", "cat", "head", "tail", "wc", "echo", "date",
    "which", "whoami", "uname", "df", "du", "grep", "rg",
    "sort", "uniq", "tree", "file", "stat", "nl", "diff",
}

#: 两段式只读白名单。注意刻意不含 `git branch`——`git branch -D` 会删分支
SAFE_PAIRS = {
    "git status", "git diff", "git log", "git show", "git remote",
    "python --version", "python3 --version",
}

#: shell 元字符：出现任意一个，静态判断就不再可靠，一律升级为审批
_META = re.compile(r"[;&|><`$\n]")

#: 引号里的内容不是 shell 语法，判断元字符之前要先抹掉。
#: 反例：`git log --pretty=format:"%ad | %s"` 里的 | 只是输出格式里的字符，
#: 不抹就会把一条纯只读命令误升为审批。
#: 注意 BLOCK_PATTERNS 仍然拿原文匹配 —— 那一档宁可误杀，代价不对称。
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def _mask_quoted(cmd: str) -> str:
    return _QUOTED.sub('""', cmd)

#: 硬禁止模式：(正则, 人类可读的理由)
BLOCK_PATTERNS: list[tuple[str, str]] = [
    (r"\b(sudo|doas)\b", "提权命令"),
    (r"\bsu\s+-", "切换用户"),
    # fs 工具靠 PROTECTED 挡住 .env，但 shell 能绕过它：`cat .env` 直接把密钥打出来。
    # 所以这里再拦一道——注意 .env.example 不会被匹配（后面紧跟的是点）
    (r"[\s\"'=/]\.env(\s|$|[\"')])", "涉及 .env 密钥文件"),
    # 凭据目录比 .env 更值钱：私钥、云厂商凭据、k8s 配置
    (r"[\s\"'=/]\.(ssh|aws|gnupg|kube)(/|\b)", "涉及用户凭据目录"),
    (r"\brm\s+(-[^\s]+\s+)*-[a-z]*[rf][a-z]*\s+(/|/\*|~|\$HOME)(\s|$)", "删除根目录或家目录"),
    (r"\b(mkfs|fdisk|diskutil\s+erase)", "磁盘级操作"),
    (r"\bdd\s+if=", "裸写设备"),
    (r"\b(shutdown|reboot|halt)\b", "关机重启"),
    (r":\(\)\s*\{.*\}\s*;?\s*:", "fork 炸弹"),
    (r"(curl|wget)[^|;]*\|\s*(sudo\s+)?(ba|z|k)?sh\b", "把下载内容直接交给 shell 执行"),
    (r"\bchmod\s+(-R\s+)?777\s+/(\s|$)", "开放根目录权限"),
    (r">\s*/dev/(sd|disk|rdisk)", "直接写块设备"),
]

#: 环境变量里带这些词的，不给子进程继承——
#: 否则模型只要执行一次 `env`，就能把 API Key 读走（10b 那次泄露的同类风险）
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")


def _outside_paths(words: list[str], workspace: Path) -> str:
    """找出指向工作目录之外的路径参数；返回第一个越界的 token，没有就返回空串。

    允许列表只看"命令是什么"是不够的：`cat ~/.ssh/id_rsa` 里 cat 完全无害，
    危险的是它读的那个路径。fs 系列工具在 _resolve() 里做了边界校验，shell 必须补上。
    """
    root = workspace.resolve()
    for raw in words:
        token = raw.strip("\"'")
        if not token or token.startswith("-"):
            continue
        if token != "~" and "/" not in token and not token.startswith("~"):
            continue  # 纯文件名/参数，落不到工作目录外
        candidate = Path(token).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate.resolve().relative_to(root)
        except ValueError:
            return token
    return ""


def classify(command: str, workspace: Path | None = None) -> tuple[str, str]:
    """返回 (verdict, reason)，verdict 是 block / ask / allow 之一。"""
    cmd = command.strip()
    if not cmd:
        return "block", "命令为空"

    for pattern, reason in BLOCK_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "block", reason

    if _META.search(_mask_quoted(cmd)):
        return "ask", "命令包含管道、重定向或串联，无法静态判断影响"

    words = cmd.split()
    if workspace is not None:
        outside = _outside_paths(words, workspace)
        if outside:
            return "ask", f"命令访问了工作目录之外的路径（{outside}）"

    if words[0] in SAFE_SINGLE or " ".join(words[:2]) in SAFE_PAIRS:
        return "allow", ""
    return "ask", "命令不是只读操作，可能修改你的文件或系统"


def clean_env() -> dict[str, str]:
    """给子进程的环境：去掉所有像密钥的变量和 OHOPILOT_ 自己的配置。"""
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper.startswith("OHOPILOT_"):
            continue
        if any(hint in upper for hint in _SECRET_HINTS):
            continue
        env[name] = value
    return env


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """把整个进程组杀掉：只 kill 外层 shell 会留下孤儿进程继续跑。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class ShellTool(Tool):
    name = "shell_run"
    description = (
        "在工作目录里执行一条 shell 命令，返回退出码与合并后的输出。"
        "只读命令（ls/cat/grep/git status 等）直接执行；"
        "会修改文件或系统的命令需要用户确认；"
        "提权、删根目录、下载即执行这类命令被禁止。"
        "需要管道、重定向或多条命令串联时，尽量拆成几次调用，便于逐步确认。"
        "读文件内容优先用 fs_read_file，列目录优先用 fs_list_dir，"
        "找内容优先用 fs_search——专用工具对结果做过截断与格式化，shell 的原始输出又长又乱。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的完整命令"},
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 30，上限由配置 shell_timeout 决定",
            },
        },
        "required": ["command"],
    }

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def needs_approval(self, arguments: dict[str, Any]) -> tuple[bool, str]:
        verdict, reason = classify(str(arguments.get("command", "")), self.cfg.workspace)
        # block 不弹窗（run 里会拒绝），只有 ask 才需要人点头
        if verdict == "ask":
            return True, reason
        return False, ""

    async def run(self, command: str = "", timeout: int = 30, **_: Any) -> ToolResult:
        cmd = command.strip()
        verdict, reason = classify(cmd, self.cfg.workspace)
        if verdict == "block":
            return ToolResult.fail(f"命令被禁止执行（{reason}）：{cmd}")

        limit = max(1, min(int(timeout or 30), self.cfg.shell_timeout))
        started = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(self.cfg.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # 合并：对模型来说顺序比区分流更重要
                stdin=asyncio.subprocess.DEVNULL,  # 没有输入口，命令不会卡在等输入
                env=clean_env(),
                start_new_session=True,  # 自成进程组，超时才能整组杀掉
            )
        except OSError as exc:
            return ToolResult.fail(f"启动命令失败：{exc}")

        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=limit)
        except asyncio.TimeoutError:
            _kill_group(proc)
            # 杀完必须回收：不 await wait()，子进程会变成僵尸，
            # 事件循环关闭时还会抛 "Loop ... is closed" 警告
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass
            return ToolResult.fail(f"命令超过 {limit} 秒未结束，已终止：{cmd}")

        text = out.decode("utf-8", errors="replace")
        elapsed = time.monotonic() - started
        body = f"$ {cmd}\n(退出码 {proc.returncode}，耗时 {elapsed:.1f}s)\n{text or '(无输出)'}"

        # 非零退出码回失败：模型看到 is_error 才会去读输出里的原因，而不是当成成功了
        if proc.returncode != 0:
            return ToolResult.fail(body)
        return ToolResult.ok(body)
