"""目录白名单：把「每个文件点一次允许」变成「这个目录以后都允许」。

存 `~/.ohopilot/grants.json`，人可读、可手改、随时能删。

为什么是文件而不是 SQLite 表：
  它是一条安全策略，用户需要在 agent 起不来的时候用编辑器直接看和删；
  数据库里的二进制行做不到这点。规则通常只有几条，每次读文件的成本可以忽略。

为什么规则只记「工具 + 路径 + 文件/目录」，不记命令前缀：
  命令前缀（`cat >`、`python`）能被无数种写法绕过，记了也守不住；
  而「这个目录归 agent 管」是用户能想清楚、事后也能审计的一句话说清的事。
  真正危险的动作（rm/mv/chmod、提权、删根目录）不走白名单，永远弹审批。
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from ohopilot.tools.base import ApprovalScope

GRANT_FILE = "grants.json"

#: 生成候选范围时到此为止的目录：再往上就是把整台机器交出去
_STOP_DIRS = {Path("/"), Path("/Users"), Path("/home"), Path("/Volumes")}


def scope_options(
    folder: str | Path,
    home: str | Path,
    levels: int = 3,
) -> list[ApprovalScope]:
    """「仅本次 + 这个目录向上 N 级」的候选授权范围。

    入参 folder 是**目录**（可以还不存在）。调用方负责把文件路径换成它的父目录：
    文件工具直接知道自己在写文件，shell 那边得自己判断 token 是文件还是目录。

    为什么给多级：用户让 agent 在桌面建项目时，真实意图往往是
    「这个项目目录归你管」，但项目可能嵌在好几层里，让人自己敲路径不现实。
    到用户家目录 / 数据家目录 / 根目录就停——那已经不是一个"目录"了。
    """
    options = [ApprovalScope("once", "仅本次")]
    current = _norm(folder)
    home_path = _norm(home)
    user_home = _norm(Path.home())
    seen: list[Path] = []
    while len(seen) < levels:
        if current in _STOP_DIRS or current == current.parent:
            break
        if current == home_path or current == user_home:
            break
        seen.append(current)
        current = current.parent
    for depth, path in enumerate(seen):
        label = "本目录" if depth == 0 else f"上 {depth} 级目录"
        options.append(ApprovalScope("dir", f"{label}：{path}", str(path)))
    return options


@dataclass(frozen=True)
class Grant:
    id: str
    tool: str      # fs_write_file / shell_run / *（* 表示对所有工具生效）
    path: str      # 绝对路径
    kind: str      # file = 只这一个文件；dir = 这个目录及子目录
    created_at: str
    label: str     # 给界面看的说明，比如 "桌面/报告"

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "tool": self.tool,
            "path": self.path,
            "kind": self.kind,
            "created_at": self.created_at,
            "label": self.label,
        }


def _norm(raw: str | Path) -> Path:
    """统一成绝对、解析过符号链接的路径。

    macOS 上 /Users/x 和 /private/Users/x 是同一个地方（/Users 是符号链接），
    不 resolve 就会出现「明明授权了这个目录，却还是弹审批」。
    """
    path = Path(raw).expanduser()
    try:
        return path.resolve()
    except OSError:  # 路径不存在 / 权限不够时 resolve 可能抛错，退回绝对路径
        return path.absolute()


def _under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


class GrantStore:
    """白名单的读写。

    每次 `covers()` 都重新读文件（而不是缓存）：ShellTool 和 WriteFileTool
    各持一个实例，谁新加了规则，另一个必须立刻看得见。规则只有几条，
    读一次几十微秒，比缓存失效逻辑便宜得多。
    """

    def __init__(self, home: Path):
        self.file = Path(home) / GRANT_FILE

    # ---- 读 ----

    def _load(self) -> list[dict[str, str]]:
        if not self.file.is_file():
            return []
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 文件被手改坏了不该让工具全挂：当成没有规则（等于全部要审批），
            # 安全方向是「更严」而不是「更松」
            return []
        rules = data.get("grants") if isinstance(data, dict) else None
        return [r for r in rules or [] if isinstance(r, dict) and r.get("path")]

    def list(self) -> list[Grant]:
        out: list[Grant] = []
        for r in self._load():
            out.append(
                Grant(
                    id=str(r.get("id", "")),
                    tool=str(r.get("tool", "")),
                    path=str(r.get("path", "")),
                    kind=str(r.get("kind", "dir")),
                    created_at=str(r.get("created_at", "")),
                    label=str(r.get("label", "")),
                )
            )
        return out

    # ---- 写 ----

    def _save(self, rules: list[dict[str, str]]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再原子替换：写到一半被 kill 也不会留下半截 JSON
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"version": 1, "grants": rules}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.file)

    def add(self, tool: str, path: str | Path, kind: str = "dir", label: str = "") -> Grant:
        target = _norm(path)
        rules = self._load()
        for r in rules:
            # 同一个工具 + 同一个路径：重复授权不该堆多条，返回已有的
            if r.get("tool") == tool and r.get("kind") == kind and _norm(r["path"]) == target:
                return Grant(
                    id=str(r.get("id", "")),
                    tool=tool,
                    path=str(r["path"]),
                    kind=kind,
                    created_at=str(r.get("created_at", "")),
                    label=str(r.get("label", "")),
                )
        grant = Grant(
            id=uuid.uuid4().hex[:8],
            tool=tool,
            path=str(target),
            kind=kind if kind in {"file", "dir"} else "dir",
            created_at=datetime.now().isoformat(timespec="seconds"),
            label=label or target.name or str(target),
        )
        rules.append(grant.to_dict())
        self._save(rules)
        return grant

    def remove(self, grant_id: str) -> bool:
        rules = self._load()
        kept = [r for r in rules if str(r.get("id")) != grant_id]
        if len(kept) == len(rules):
            return False
        self._save(kept)
        return True

    # ---- 判断 ----

    def covers(self, tool: str, target: str | Path) -> bool:
        """target 是否已被某个规则覆盖。tool 只匹配自己或通配 `*`。"""
        path = _norm(target)
        for r in self._load():
            if r.get("tool") not in (tool, "*"):
                continue
            root = _norm(r["path"])
            if r.get("kind") == "file":
                if path == root:
                    return True
                continue
            if path == root or _under(path, root):
                return True
        return False

    def covers_all(self, tool: str, targets: Sequence[str | Path]) -> bool:
        """所有目标都被覆盖才算数。空列表返回 False——没目标就没法判断，不能默认放行。"""
        if not targets:
            return False
        return all(self.covers(tool, t) for t in targets)
