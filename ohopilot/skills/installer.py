"""技能安装器：把外部技能装进 ~/.ohopilot/skills/，装前先过安全审计。

**为什么要有审计**：技能正文会被模型当「操作步骤」执行。一份恶意技能可以让
agent 跑 `rm -rf`、把 .env 里的 key 发到陌生服务器——而审批弹窗里用户看到的
只是「skill_install」，根本想不到背后藏了什么。所以在落地之前先扫一遍内容，
把明确的危险命令拦在门外。

来源两种：
- 本地目录：Claude 系技能的形态（目录里一个 SKILL.md）
- URL：一份裸 .md 文本

格式兼容：Claude 系 frontmatter 可能带 allowed-tools、version 等我们用不上的
字段，转换层只保留 name/description，名字归一化成 [a-zA-Z0-9_-]+。
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ohopilot.config import Config
from ohopilot.skills.loader import _NAME_RE, parse_skill

# ---------------------------------------------------------------------------
# 安全审计
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditFinding:
    severity: str  # "danger"（直接拒装）| "warning"（要 confirm_risky 才装）
    label: str
    line_no: int
    excerpt: str


#: danger：出现即拒装。模式必须是「无歧义的危险」，宁缺毋滥——
#: 误杀正常技能比放过一个可疑技能更伤生态
_DANGER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("sudo 提权", re.compile(r"\bsudo\s")),
    ("rm 递归/强制删除", re.compile(r"\brm\s+(-[a-z]*[rf][a-z]*\s|.*\s-[a-z]*r)")),
    ("下载并直接执行脚本", re.compile(r"(curl|wget)[^|\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b")),
    ("覆盖磁盘设备", re.compile(r"\bof=/dev/")),
    ("创建/修改系统关键目录", re.compile(r"\b(chmod|chown)\s+.*\s+/(etc|usr|System|Library)\b")),
    ("fork 炸弹", re.compile(r":\(\)\s*\{.*\};\s*:")),
]

#: warning：不拦，但要模型（和用户在审批弹窗里）知情
_WARNING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("联网请求", re.compile(r"\b(curl|wget|httpx|requests\.get)\b")),
    ("安装软件包", re.compile(r"\b(pip3? install|npm install|brew install)\b")),
    ("读取环境变量/密钥文件", re.compile(r"\b(printenv|cat\s+\.env|\.env\b.*API.?KEY)\b", re.I)),
    ("git 推送/强推", re.compile(r"\bgit push\b")),
]


def audit_skill_text(text: str) -> list[AuditFinding]:
    """扫描技能全文，返回发现的问题清单。没有问题返回空列表。"""
    findings: list[AuditFinding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for label, pattern in _DANGER_PATTERNS:
            if pattern.search(line):
                findings.append(AuditFinding("danger", label, line_no, line.strip()[:120]))
        for label, pattern in _WARNING_PATTERNS:
            if pattern.search(line):
                findings.append(AuditFinding("warning", label, line_no, line.strip()[:120]))
    return findings


# ---------------------------------------------------------------------------
# frontmatter 兼容转换（Claude 系 SKILL.md → 本项目格式）
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """拆出 `key: value` 形式的 frontmatter 和正文。格式不对返回空 meta。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end < 0:
        return {}, text

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = value.strip().strip("\"'")

    return meta, "\n".join(lines[end + 1 :])


def normalize_skill_text(text: str, fallback_name: str) -> str:
    """把外部技能文本归一化成项目格式。

    - name 缺失 → 用来源名兜底；非法字符（空格/点/中文）→ 归一化成合法标识符
    - description 缺失 → 取正文第一行非空内容兜底（loader 要求必须有）
    - 其他字段（allowed-tools 等）直接丢弃
    """
    meta, body = _split_frontmatter(text)

    name = meta.get("name") or fallback_name
    name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower() or "unnamed-skill"

    description = meta.get("description", "")
    if not description:
        for line in body.splitlines():
            if line.strip():
                description = line.lstrip("# ").strip()
                break

    new_meta = [f"---", f"name: {name}", f"description: {description}", "---"]
    return "\n".join(new_meta) + "\n\n" + body.strip() + "\n"


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------


def _fetch_url(url: str) -> str:
    """下载远端技能文本。独立成函数方便测试替换（测试纪律：不发真网）。"""
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310（URL 来自用户配置）
        return resp.read().decode("utf-8")


def install_skill(
    source: str,
    cfg: Config,
    *,
    confirm_risky: bool = False,
    replace: bool = False,
    fetcher=None,
) -> tuple[Path | None, list[AuditFinding], str]:
    """安装一个技能，返回 (落地路径, 审计发现, 消息)。

    决策矩阵：
    - danger 发现 → 无论 confirm 与否都拒装（危险内容没有「知情后放行」）
    - 只有 warning → confirm_risky=True 才装
    - 同名技能已存在 → replace=True 才覆盖
    """
    cfg.skills_dir.mkdir(parents=True, exist_ok=True)

    src = source.strip()
    if src.startswith(("http://", "https://")):
        fetch = fetcher or _fetch_url
        try:
            raw = fetch(src)
        except Exception as exc:  # 网络错误给可读信息，不裸抛
            return None, [], f"下载失败：{exc}"
        origin_name = Path(src).stem or "remote-skill"
    else:
        path = Path(src).expanduser()
        # Claude 技能形态：目录里一个 SKILL.md；也接受直接给 .md 文件
        if path.is_dir():
            path = path / "SKILL.md"
        if not path.is_file():
            return None, [], f"来源不存在或不是技能文件/目录：{source}"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            return None, [], f"读取失败：{exc}"
        origin_name = path.stem if path.stem != "SKILL" else path.parent.name

    findings = audit_skill_text(raw)
    if any(f.severity == "danger" for f in findings):
        report = "\n".join(f"  [危险] {f.label}（第 {f.line_no} 行）{f.excerpt}" for f in findings if f.severity == "danger")
        return None, findings, f"安全审计未通过，已拒装。发现以下危险内容：\n{report}"

    warnings = [f for f in findings if f.severity == "warning"]
    if warnings and not confirm_risky:
        report = "\n".join(f"  [注意] {f.label}（第 {f.line_no} 行）{f.excerpt}" for f in warnings)
        return None, findings, (
            "安全审计发现以下需知悉的内容，确认无误后带 confirm_risky=true 重试：\n"
            f"{report}"
        )

    normalized = normalize_skill_text(raw, origin_name)
    skill = parse_skill(normalized, origin_name, Path("."))
    if skill is None:
        return None, findings, "技能格式不合法：缺 name/description 或没有正文步骤"

    target = cfg.skills_dir / f"{skill.name}.md"
    if target.exists() and not replace:
        return None, findings, f"技能 {skill.name} 已存在。要覆盖请带 replace=true"

    target.write_text(normalized, encoding="utf-8")
    note = ""
    if warnings:
        note = "（安装时带 " + "、".join(sorted({f.label for f in warnings})) + " 等内容，已按你的确认放行）"
    return target, findings, f"技能 {skill.name} 已安装到 {target}{note}"
