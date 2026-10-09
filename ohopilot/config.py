"""统一配置入口：环境变量 > ~/.ohopilot/.env > 项目 .env > 默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析，够用即可，不引入额外依赖。"""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _apply_env_file(path: Path) -> None:
    for key, value in _parse_env_file(path).items():
        # 已有环境变量优先，.env 只做兜底
        os.environ.setdefault(key, value)


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass
class Config:
    """一次性把运行所需配置收拢，避免散落的 os.getenv。"""

    model: str
    api_base: str | None
    api_key: str | None
    home: Path
    workspace: Path
    allow_write: bool
    shell_timeout: int
    host: str
    port: int
    reasoning_effort: str | None = None

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
        """首次运行时把 ~/.ohopilot 铺好，并放一个示例技能。"""
        self.home.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            self.memory_file.write_text(
                "# OhoPilot 长期记忆\n\n"
                "> 这里存放跨会话需要记住的事实（你的偏好、常用路径、项目约定）。\n"
                "> agent 每次启动都会读到它，你可以直接手改。\n",
                encoding="utf-8",
            )
        sample = self.skills_dir / "weekly-report.md"
        if not sample.exists():
            sample.write_text(
                "---\n"
                "name: weekly-report\n"
                "description: 根据本周的工作记录，生成一份结构化的中文周报。"
                "当用户提到周报、本周总结、汇报时使用。\n"
                "---\n\n"
                "# 周报生成\n\n"
                "1. 先问清时间范围与项目名（若用户已给出则跳过）。\n"
                "2. 用 fs.search_files 在本机找相关的记录、日志、提交信息。\n"
                "3. 按「本周完成 / 进行中 / 风险与阻塞 / 下周计划」四段成文。\n"
                "4. 每条不超过 2 行，动词开头，能量化的一律带数字。\n"
                "5. 输出 Markdown，别加客套话。\n",
                encoding="utf-8",
            )

    @classmethod
    def load(cls, workspace: Path | None = None) -> "Config":
        home = Path(os.getenv("OHOPILOT_HOME", "") or (Path.home() / ".ohopilot")).expanduser()

        # 项目级 .env（当前工作目录）后加载，优先级低于已存在的环境变量
        _apply_env_file(Path.cwd() / ".env")
        _apply_env_file(home / ".env")

        ws = workspace or (Path(os.getenv("OHOPILOT_WORKSPACE", "") or Path.cwd()))
        ws = Path(ws).expanduser().resolve()

        api_key = (
            os.getenv("OHOPILOT_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )

        return cls(
            model=os.getenv("OHOPILOT_MODEL", "openai/deepseek-v4-flash"),
            api_base=os.getenv("OHOPILOT_API_BASE") or "https://api.deepseek.com/v1",
            api_key=api_key,
            home=home,
            workspace=ws,
            allow_write=_as_bool(os.getenv("OHOPILOT_ALLOW_WRITE"), True),
            shell_timeout=_as_int(os.getenv("OHOPILOT_SHELL_TIMEOUT"), 60),
            host=os.getenv("OHOPILOT_HOST", "127.0.0.1"),
            port=_as_int(os.getenv("OHOPILOT_PORT"), 8737),
            reasoning_effort=os.getenv("OHOPILOT_REASONING_EFFORT") or None,
        )
