"""GitHub 仓库技能发现：给扩展中心页面提供「从别人的仓库挑技能」的能力。

流程两步：
1. discover_repo_skills("anthropics/skills") → 列出仓库里长得像技能的 .md 文件
2. 拿到每条的 raw_url，交给 installer.install_skill() 走原有的下载 → 审计 → 安装管道

**为什么发现和安装分开**：发现只看目录不下载内容（快、省流量），
审计在安装时才做（要下载全文扫描）。页面先列表、用户勾选、逐个装，
每一步都有明确的失败点可提示。

识别规则（宁缺毋滥，不追求全覆盖）：
- 任何目录下的 SKILL.md —— Claude 系技能仓库的标准形态
- 仓库根目录的 *.md（排除 README/LICENSE/CHANGELOG 这类仓库说明文件）
"""

from __future__ import annotations

import json
import re
import urllib.request

#: 仓库根目录这些 md 是「仓库自己的说明」，不是技能
_REPO_META_FILES = {"readme", "license", "changelog", "contributing", "code_of_conduct"}

#: 候选上限。真仓库几百个技能时页面也刷不动，先截断
MAX_CANDIDATES = 50


def parse_repo(repo: str) -> tuple[str, str]:
    """把 'owner/repo' 或 GitHub 网页地址解析成 (owner, repo)。

    接受：anthropics/skills、https://github.com/anthropics/skills、…/skills.git
    解析不出来就抛 ValueError，让接口层转成 422。
    """
    text = repo.strip()
    text = re.sub(r"^https?://github\.com/", "", text)
    # 先去尾部斜杠再去 .git，"…/skills.git/" 这种顺序才剥得干净
    text = text.strip("/").removesuffix(".git").strip("/")
    parts = text.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"仓库格式不认识：{repo}（要 owner/repo 或 GitHub 地址）")
    return parts[0], parts[1]


def _github_json(url: str) -> dict:
    """拉一个 GitHub API JSON。独立成函数方便测试替换（测试纪律：不发真网）。"""
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ohopilot"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310（URL 由本模块拼出）
        return json.loads(resp.read().decode("utf-8"))


def discover_repo_skills(repo: str, getter=None) -> list[dict[str, str]]:
    """列出仓库里的技能候选。返回 [{name, path, raw_url}]。

    name 取「SKILL.md 所在目录名」（Claude 形态）或根目录 md 的文件名，
    安装时它就是 frontmatter 缺失情况下的兜底名。
    """
    fetch = getter or _github_json
    owner, name = parse_repo(repo)
    base = f"https://api.github.com/repos/{owner}/{name}"

    info = fetch(base)
    branch = info.get("default_branch") or "main"
    tree = fetch(f"{base}/git/trees/{branch}?recursive=1")

    candidates: list[dict[str, str]] = []
    for item in tree.get("tree", []):
        if item.get("type") != "blob":
            continue
        path: str = item.get("path", "")
        if path.endswith("SKILL.md"):
            # 用目录名当技能名：skills/weekly-report/SKILL.md → weekly-report
            parts = path.split("/")[:-1]
            skill_name = parts[-1] if parts else "remote-skill"
        elif path.endswith(".md") and "/" not in path:
            stem = path[:-3]
            if stem.lower() in _REPO_META_FILES:
                continue
            skill_name = stem
        else:
            continue
        candidates.append(
            {
                "name": skill_name,
                "path": path,
                "raw_url": f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/{path}",
            }
        )
        if len(candidates) >= MAX_CANDIDATES:
            break
    return candidates
