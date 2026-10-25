""" 切块器：把长文本切成适合检索的片段

为什么 500 字符一块、重叠 80：一块太大检索出来的内容噪点多，
太小则语义不完整。重叠（overlap）是防止一句话正好被切在边界上——
相邻两块共享尾部/头部文字，边界附近的内容两块都能命中。
"""

from __future__ import annotations

#: 单块目标长度（字符）。中文一字一字符，500 字约一小节
CHUNK_SIZE = 500
#: 相邻块重叠字符数
CHUNK_OVERLAP = 80
#: 单个句子超长时硬切的兜底长度（没有任何分隔符的超长行，如压缩过的 JSON）
_HARD_LIMIT = CHUNK_SIZE * 2


def _split_long(text: str, limit: int) -> list[str]:
    """没有任何换行/句号可借力的超长文本，按 limit 硬切。"""
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _split_paragraph(para: str) -> list[str]:
    """把一个段落切成不超过 CHUNK_SIZE 的片段。

    优先按句子边界（。！？；!?;）切，最后兜底硬切。
    """
    if len(para) <= CHUNK_SIZE:
        return [para]

    # 按句切开（保留句尾标点），再贪心合并
    sentences: list[str] = []
    buf = ""
    for ch in para:
        buf += ch
        if ch in "。！？；!?;":
            sentences.append(buf)
            buf = ""
    if buf:
        sentences.append(buf)

    pieces: list[str] = []
    cur = ""
    for s in sentences:
        # 单句就超长——只能硬切，这是兜底不是常态
        if len(s) > _HARD_LIMIT:
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.extend(_split_long(s, CHUNK_SIZE))
            continue
        if cur and len(cur) + len(s) > CHUNK_SIZE:
            pieces.append(cur)
            cur = s
        else:
            cur += s
    if cur:
        pieces.append(cur)
    return pieces


def chunk_text(text: str) -> list[str]:
    """把全文切成块列表。

    策略：空行分段 → 段落贪心合并到 CHUNK_SIZE → 段内超长按句切。
    返回的块不带重叠（重叠在 _apply_overlap 里事后加工），
    这样切块逻辑和重叠逻辑各自独立、各自可测。
    """
    text = text.strip()
    if not text:
        return []

    paras = [p.strip() for p in text.split("\n") if p.strip()]
    pieces: list[str] = []
    cur = ""
    for para in paras:
        for piece in _split_paragraph(para):
            if cur and len(cur) + len(piece) + 1 > CHUNK_SIZE:
                pieces.append(cur)
                cur = piece
            elif cur:
                cur += "\n" + piece
            else:
                cur = piece
    if cur:
        pieces.append(cur)

    return _apply_overlap(pieces)


def _apply_overlap(pieces: list[str]) -> list[str]:
    """给第 2 块起的开头拼上上一块的尾部，制造重叠。"""
    if len(pieces) <= 1 or CHUNK_OVERLAP <= 0:
        return pieces
    out = [pieces[0]]
    for prev, cur in zip(pieces, pieces[1:]):
        tail = prev[-CHUNK_OVERLAP:]
        out.append(tail + cur)
    return out
