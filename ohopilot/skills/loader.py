"""技能加载：把 ~/.ohopilot/skills/*.md 读成结构化对象。

一个技能就是一份 Markdown 文件：

    ---
    name: weekly-report
    description: 生成周报。用户提到周报、本周总结时使用。
    ---
    正文：具体步骤

**为什么要有这一层：** 它是「工具太多」的另一种解法。工具是代码能力，
每加一个都要占 function calling 的 schema 预算；技能是**提示词能力**，
系统提示里只放一行 `名字: 什么时候用`，模型需要时才把正文取进上下文。
加十个技能只多十行 token，而不是十份文档。

这叫渐进披露（progressive disclosure）：先给目录，再给正文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ohopilot.config import Config

#: 技能名要和工具名一样「能当标识符用」，否则模型复制粘贴时容易出错
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

#: 单个技能正文上限。技能是提示词，不是文档库；太长说明该拆成多个技能
MAX_SKILL_CHARS = 8_000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path


def parse_skill(text: str, fallback_name: str, path: Path) -> Skill | None:
    """解析一份技能文件。缺 name 用文件名兜底，缺 description 就丢弃。

    为什么缺 description 必须丢弃：description 是模型「要不要用这个技能」
    的唯一判断依据。没有它，这一行在系统提示里就是个噪声条目。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end < 0:
        return None

    # 只认 `key: value` 单行形式 —— 够用，且不用为一行元数据引入 PyYAML
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip("\"'")

    name = meta.get("name") or fallback_name
    description = meta.get("description", "")
    if not _NAME_RE.match(name) or not description:
        return None

    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        return None  # 只有元数据没有步骤，等于一句 description，没必要单独成技能

    if len(body) > MAX_SKILL_CHARS:
        body = body[:MAX_SKILL_CHARS] + "\n\n…[技能正文过长，已截断]"

    return Skill(name=name, description=description, body=body, path=path)


def load_skills(cfg: Config) -> list[Skill]:
    """扫描技能目录。坏文件静默跳过 —— 技能是增强项，不该让启动失败。"""
    folder = cfg.skills_dir
    if not folder.is_dir():
        return []

    skills: list[Skill] = []
    seen: set[str] = set()
    for path in sorted(folder.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        skill = parse_skill(text, path.stem, path)
        if skill is None or skill.name in seen:
            continue
        seen.add(skill.name)
        skills.append(skill)
    return skills


def render_skill_index(skills: list[Skill]) -> str:
    """生成系统提示里的技能清单：一行一个，只有名字和触发条件。"""
    if not skills:
        return ""
    lines = [f"- {s.name}：{s.description}" for s in skills]
    return "\n".join(lines)
