"""长期记忆：~/.ohopilot/MEMORY.md 的读写。

和 store.py 的分工：
- store.py 管「会话历史」——每轮都取、会一直变长，属于上下文；
- 这里管「跨会话的事实与偏好」——体量小、变化慢，属于提示词素材。

写入走 append（只追加），不做「读-改-写」：并发或中途崩溃都不会把旧记忆弄丢。
"""

from __future__ import annotations

from ohopilot.config import Config

#: 注入系统提示的最大字符数。
#: 长期记忆会跟着每一个请求一起发出去，没有上限等于每轮都在为它付 token。
MAX_MEMORY_CHARS = 4_000


def _is_note(line: str) -> bool:
    """以 `>` 开头的是写给人看的注释（文件的默认说明），不该进提示词。"""
    return line.lstrip().startswith(">")


def _is_heading(line: str) -> bool:
    return line.lstrip().startswith("#")


def load_longterm(cfg: Config, limit: int = MAX_MEMORY_CHARS) -> str:
    """读出长期记忆正文；没有任何实质内容时返回空串。

    返回空串而不是空标题，是为了让调用方能干净地决定「不注入这一段」——
    系统提示里塞一个只有标题的空章节，纯属浪费 token。
    """
    try:
        text = cfg.memory_file.read_text(encoding="utf-8").strip()
    except OSError:
        # 文件不存在 / 没权限 / 编码坏了，都不该让对话起不来：记忆是增强项
        return ""

    # 只剩标题和注释（比如 ensure_dirs 刚建出来的空文件）就当没有内容
    body = "\n".join(
        line for line in text.splitlines() if not _is_heading(line) and not _is_note(line)
    ).strip()
    if not body:
        return ""

    # 去掉注释行，标题和正文都保留（标题给模型提供结构感）
    cleaned = "\n".join(line for line in text.splitlines() if not _is_note(line)).strip()

    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + f"\n\n…[长期记忆超过 {limit} 字符，已截断]"


def append_longterm(cfg: Config, text: str) -> int:
    """往长期记忆末尾追加一条，返回写入后文件的总字符数。

    每条统一成 markdown 列表项，方便人和模型都扫得动；
    已经写成 `- xxx` 的不重复加前缀。
    """
    content = text.strip()
    if not content:
        raise ValueError("记忆内容不能为空")

    path = cfg.memory_file
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    if existing and not existing.endswith("\n"):
        existing += "\n"

    line = content if content.startswith("- ") else f"- {content}"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")

    return len(existing) + len(line) + 1
