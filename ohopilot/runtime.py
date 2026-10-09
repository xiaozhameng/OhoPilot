"""运行时装配：把配置、工具、记忆、技能、内核拼在一起。

CLI 和 HTTP 服务端都从这里取实例，保证两边行为完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .core.agent import Agent, Approver, auto_approve
from .memory import MemoryStore
from .skills import SkillLoader, build_skill_tool
from .tools import ToolRegistry, build_registry


@dataclass
class Runtime:
    cfg: Config
    store: MemoryStore
    registry: ToolRegistry
    skills: SkillLoader
    agent: Agent

    def close(self) -> None:
        self.store.close()


def build_runtime(
    workspace: Path | None = None,
    approver: Approver | None = None,
) -> Runtime:
    cfg = Config.load(workspace)
    cfg.ensure_dirs()

    registry = build_registry(cfg)
    skills, skill_tool = build_skill_tool(cfg)
    registry.register(skill_tool)

    store = MemoryStore(cfg)
    agent = Agent(cfg, registry, store, skills, approver=approver or auto_approve)

    return Runtime(cfg=cfg, store=store, registry=registry, skills=skills, agent=agent)


__all__ = ["Runtime", "build_runtime"]
