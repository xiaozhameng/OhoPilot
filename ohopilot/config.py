""" 全局配置：集中读取环境变量，以及.env 文件

设计原则：
1.代码中不出现硬编码的密钥，路径，模型名
2.可调项目一律采用环境变量覆盖，前缀统一为 OHOPILOT_
3.只有一个入口，就是Config.load() 其他模块不再各自读取os.environ
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ENV_PREFIX = "OHOPILOT_"

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_API_BASE = "https://api.deepseek.com"

def _get(name: str, default: str = "") -> str:
    """读取OPTION_name，去掉首尾的空格"""
    return os.environ.get(f"{ENV_PREFIX}{name}", default).strip()


def _get_bool(name: str, default: bool = False) -> bool:
    """读取布尔值， 1/true/yes/on 都算真"""
    row = _get(name).lower()
    if not row:
        return default
    return row in {"true", "yes", "on", "1"}

def _get_int(name: str, default: int = 0) -> int:
    """读整数；写错了不崩溃，退回默认值。"""
    row = _get(name).strip()
    try:
        return int(row) if row else default
    except ValueError:
        return default


@dataclass
class Config:
    # 模型配置
    model: str = DEFAULT_MODEL
    api_base: str = DEFAULT_API_BASE
    api_key: str = ""

    # 位置
    home: Path = field(default_factory=lambda: Path.home() / ".ohopilot")
    workspace: Path = field(default_factory=Path.cwd)

    # 安全与限额
    allow_write: bool = True
    shell_timeout: int = 60
    max_tool_rounds: int = 12

    # 上下文压缩：未压缩的消息超过 summary_after 条时，
    # 把最老的一批压成摘要，只给最近 summary_keep 条留原文。
    # 设为 0 表示关闭摘要（退回"只取最近 200 条"的老行为）
    summary_after: int = 24
    summary_keep: int = 8

    # 服务端
    host: str = "127.0.0.1"
    port: int = 8737
    # HTTP 审批等待上限：前端迟迟不点「允许/拒绝」时，到点自动拒绝。
    # 服务端不能像终端那样无限等，否则一次请求会永久挂着
    approval_timeout: int = 180

    # 由home 派生出来的路径，不单独存储
    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def memory_file(self) -> Path:
        return self.home / "MEMORY.md"

    @property
    def db_path(self) -> Path:
        return self.home / "ohopilot.db"

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            self.memory_file.write_text("# OhoPilot 长期记忆\n", encoding="utf-8")

    @classmethod
    def load(cls, workspace: Path | None = None) -> Config:
        """唯一入口：先读 .env，再从环境变量组装出 Config。"""
        load_dotenv()

        return cls(
            model=_get("MODEL", DEFAULT_MODEL),
            api_base=_get("API_BASE", DEFAULT_API_BASE),
            api_key=_get("API_KEY"),
            home=Path(_get("HOME") or (Path.home() / ".ohopilot")).expanduser(),
            workspace=Path(workspace or Path.cwd()).expanduser().resolve(),
            allow_write=_get_bool("ALLOW_WRITE", True),
            shell_timeout=_get_int("SHELL_TIMEOUT", 60),
            max_tool_rounds=_get_int("MAX_TOOL_ROUNDS", 12),
            summary_after=_get_int("SUMMARY_AFTER", 24),
            summary_keep=_get_int("SUMMARY_KEEP", 8),
            host=_get("HOST", "127.0.0.1"),
            port=_get_int("PORT", 8737),
            approval_timeout=_get_int("APPROVAL_TIMEOUT", 180),
        )
