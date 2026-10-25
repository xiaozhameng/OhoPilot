"""技能工具：让模型按需把一份技能的正文取进上下文、或安装新技能。

系统提示里只有技能目录（一行一个），模型判断相关后才调这个工具。
这是「渐进披露」的执行端 —— 没有它，技能清单就只能靠人手动贴进对话。
"""

from __future__ import annotations

from typing import Any

from ohopilot.config import Config
from ohopilot.skills.installer import install_skill
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


class SkillInstallTool(Tool):
    name = "skill_install"
    description = (
        "安装一个新技能到技能目录（本地目录或 URL 的 .md 文件）。"
        "安装前会自动做安全审计：含危险命令（sudo、rm -rf、下载即执行等）的技能会被拒装；"
        "含联网、装包等敏感内容的技能需要你（模型）确认无误后带 confirm_risky 重试。"
        "同名技能默认拒绝覆盖，覆盖需 replace=true。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "技能来源：本地目录（含 SKILL.md）或 .md 文件的路径，或 http(s) URL",
            },
            "confirm_risky": {
                "type": "boolean",
                "description": "安全审计发现敏感内容（联网/装包等）时，确认后置 true 重试",
            },
            "replace": {"type": "boolean", "description": "同名技能已存在时是否覆盖"},
        },
        "required": ["source"],
    }

    #: 把第三方指令请进 agent 上下文 = 写操作，和 shell 同级对待
    requires_approval = True

    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def run(
        self, source: str = "", confirm_risky: bool = False, replace: bool = False, **_: Any
    ) -> ToolResult:
        if not source.strip():
            return ToolResult.fail("source 不能为空：给本地路径或 URL")
        target, findings, message = install_skill(
            source, self.cfg, confirm_risky=confirm_risky, replace=replace
        )
        if target is None:
            return ToolResult.fail(message)
        # 成功后提醒模型：新技能已进系统提示的技能目录，同会话即可 skill_load
        return ToolResult.ok(message + "\n现在可以通过 skill_load 使用它了。")


def build_skill_tools(cfg: Config) -> list[Tool]:
    return [SkillLoadTool(cfg), SkillInstallTool(cfg)]
