"""技能系统：渐进式披露。

启动时只把技能的 name + description 塞进系统提示词（几十个字），
模型判断需要某个技能时，再用 skills.load 读全文。
这样技能可以无限多，而上下文不会被撑爆。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..tools.base import Tool, ToolResult


@dataclass
class SkillMeta:
    name: str
    description: str
    path: Path

    def summary_line(self) -> str:
        return f"- {self.name}: {self.description}"


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """解析 --- 之间的 YAML 风格头，只认 key: value，不引入 yaml 依赖。"""
    if not text.startswith("---"):
        return {}, text
    _, _, rest = text.partition("---")
    head, sep, body = rest.partition("\n---")
    if not sep:
        return {}, text
    meta: dict[str, str] = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body.strip()


class SkillLoader:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def scan(self) -> list[SkillMeta]:
        if not self.cfg.skills_dir.exists():
            return []
        skills: list[SkillMeta] = []
        for path in sorted(self.cfg.skills_dir.glob("*.md")):
            try:
                meta, _ = _parse_front_matter(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            skills.append(
                SkillMeta(
                    name=meta.get("name") or path.stem,
                    description=meta.get("description", "（未填写 description）"),
                    path=path,
                )
            )
        return skills

    def index_prompt(self) -> str:
        skills = self.scan()
        if not skills:
            return ""
        lines = "\n".join(skill.summary_line() for skill in skills)
        return (
            "## 可用技能\n"
            "以下技能是你沉淀下来的工作流程。当用户的请求命中某个技能的适用场景时，"
            "先调用 `skills.load` 读取它的完整内容，再严格按里面的步骤执行。\n"
            f"{lines}\n"
        )

    def read(self, name: str) -> str | None:
        for skill in self.scan():
            if skill.name == name:
                _, body = _parse_front_matter(skill.path.read_text(encoding="utf-8"))
                return body
        return None


class SkillLoadTool(Tool):
    name = "skills.load"
    description = "读取一个技能的完整内容。先用它看清步骤，再动手执行。"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名（见系统提示词里的可用技能列表）"},
        },
        "required": ["name"],
    }

    def __init__(self, loader: SkillLoader) -> None:
        self.loader = loader

    async def run(self, name: str, **_: object) -> ToolResult:
        body = self.loader.read(name)
        if body is None:
            available = [skill.name for skill in self.loader.scan()]
            return ToolResult.fail(f"没有名为 {name} 的技能。可用：{available}")
        return ToolResult.ok(body)


def build_skill_tool(cfg: Config) -> tuple[SkillLoader, Tool]:
    loader = SkillLoader(cfg)
    return loader, SkillLoadTool(loader)


__all__ = ["SkillLoader", "SkillMeta", "SkillLoadTool", "build_skill_tool"]
