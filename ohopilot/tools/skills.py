"""技能工具：让模型按需把一份技能的正文取进上下文。

系统提示里只有技能目录（一行一个），模型判断相关后才调这个工具。
这是「渐进披露」的执行端 —— 没有它，技能清单就只能靠人手动贴进对话。
"""

from __future__ import annotations

from typing import Any

from ohopilot.config import Config
from ohopilot.skills.loader import load_skills
from ohopilot.tools.base import Tool, ToolResult


class SkillLoadTool(Tool):
    name = "skill_load"
    description = (
        "读取某个技能的完整操作步骤。系统提示里只列了技能名和一句话说明，"
        "当你判断某个技能和当前任务相关时，先用这个工具把正文取出来，再按里面的步骤执行。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名，必须与系统提示里列出的一致"},
        },
        "required": ["name"],
    }

    #: 只读：读的是 ~/.ohopilot/skills/ 下的说明文件，不改任何东西
    requires_approval = False

    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def run(self, name: str = "", **_: Any) -> ToolResult:
        wanted = (name or "").strip()
        skills = load_skills(self.cfg)

        if not skills:
            return ToolResult.fail(f"技能目录里没有任何可用技能：{self.cfg.skills_dir}")

        for skill in skills:
            if skill.name == wanted:
                return ToolResult.ok(f"# 技能：{skill.name}\n\n{skill.body}")

        available = ", ".join(s.name for s in skills)
        return ToolResult.fail(f"没有叫 {wanted} 的技能。现有技能：{available}")


def build_skill_tools(cfg: Config) -> list[Tool]:
    return [SkillLoadTool(cfg)]
