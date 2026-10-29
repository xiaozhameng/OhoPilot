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

#: 记忆文件本身的大小上限（控制台整文件编辑的保险丝）。
#: 注入上限 4000 意味着超出部分根本不会被模型看到，写再多也是浪费；
#: 100K 足够宽松，真到这个量级说明该整理记忆而不是继续追加。
MAX_MEMORY_FILE_CHARS = 100_000


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

    # 分隔符必须真的写进文件：只把 existing 变量补一个 \n 是不够的，
    # 文件本身没有尾部换行时，新条目会粘在上一行末尾（"- 老的一条- 新的一条"），
    # 两条记忆就合成了一条谁也读不懂的东西。
    separator = "\n" if existing and not existing.endswith("\n") else ""

    line = content if content.startswith("- ") else f"- {content}"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(separator + line + "\n")

    return len(existing) + len(separator) + len(line) + 1


def read_longterm_raw(cfg: Config) -> str:
    """读记忆文件的原文（含注释和标题）。

    控制台的编辑器要给人看：`>` 开头的注释行虽然是写给初期用户的说明，
    藏着不展示反而让人困惑「为什么模型看到的和我看到的不一样」。
    文件不存在时返回空串，让编辑器从一张白纸开始。
    """
    try:
        return cfg.memory_file.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_longterm_raw(cfg: Config, text: str) -> int:
    """整体覆写记忆文件，返回写入的字符数。

    和 append 的分工：append 是 agent 运行时「记一条」，只加不改；
    这里是人主动整理记忆——删掉过期的、合并重复的、改写措辞，
    本质是编辑操作，必须整文件替换。

    原子写（先写临时文件再改名）：写到一半崩溃或断电，
    旧记忆还在，不会留下半个文件。
    """
    if len(text) > MAX_MEMORY_FILE_CHARS:
        raise ValueError(
            f"记忆内容 {len(text)} 字符，超过上限 {MAX_MEMORY_FILE_CHARS}；"
            "长期记忆是少量精选事实，太长请做删减"
        )

    path = cfg.memory_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return len(text)
