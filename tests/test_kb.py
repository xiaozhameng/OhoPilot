""" 知识库测试：切块器 + 向量库 + 两个工具端到端

网络纪律：全程 FakeEmbedder，不发真网。
向量库用真 sqlite-vec + 临时文件——把 KNN 排序、覆盖删除这些真实行为钉住。
"""

from __future__ import annotations

import pytest

from ohopilot.config import Config
from ohopilot.kb.chunker import CHUNK_OVERLAP, CHUNK_SIZE, chunk_text
from ohopilot.kb.embedder import FakeEmbedder
from ohopilot.kb.store import SQLiteVecStore
from ohopilot.tools.base import ToolResult
from ohopilot.tools.docs import ReadDocumentTool  # noqa: F401 确认 docs 可导入
from ohopilot.tools.kb import KbIndexTool, KbSearchTool


# ---------- chunker ----------


def test_切块_短文不分块():
    assert chunk_text("短文本") == ["短文本"]


def test_切块_空文本返回空():
    assert chunk_text("  \n  ") == []


def test_切块_多段落按目标长度合并():
    paras = [f"第{i}段" + "内容" * 40 for i in range(10)]  # 每段约 45 字符
    text = "\n\n".join(paras)
    chunks = chunk_text(text)
    assert all(len(c) <= CHUNK_SIZE + 2 for c in chunks[:-1])  # 最后一块可不封顶
    assert len(chunks) >= 2


def test_切块_超长段落按句子切():
    # 一个段落 ~2000 字符、句子完整——应按句切而不是把句子腰斩
    para = "".join(f"这是第{i}个句子，描述某个完整的意思。" for i in range(100))
    chunks = chunk_text(para)
    assert len(chunks) > 1
    for c in chunks:
        # 每块的结尾要么是句号（句边界），要么是整体硬切兜底
        assert c.rstrip().endswith(("。", "。")) or len(c) >= CHUNK_SIZE


def test_切块_相邻块有重叠():
    paras = [f"第{i}段" + "内容" * 60 for i in range(20)]
    chunks = chunk_text("\n\n".join(paras))
    for prev, cur in zip(chunks, chunks[1:]):
        assert cur.startswith(prev[-CHUNK_OVERLAP:][-20:]) or CHUNK_OVERLAP > len(prev)


def test_切块_无分隔符超长行硬切兜底():
    chunks = chunk_text("啊" * 5000)
    assert len(chunks) >= 3


# ---------- FakeEmbedder ----------


def test_假向量_确定性():
    e = FakeEmbedder(dim=16)
    v1 = e.embed(["你好"])[0]
    v2 = e.embed(["你好"])[0]
    assert v1 == v2
    assert len(v1) == 16


def test_假向量_相近文本方向接近():
    e = FakeEmbedder(dim=64)
    (a,) = e.embed(["苹果 香蕉 水果 好吃"])
    (b,) = e.embed(["苹果 香蕉 水果"])
    (c,) = e.embed(["数据库 索引 事务 回滚"])
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))
    assert dot(a, b) > dot(a, c)


# ---------- SQLiteVecStore ----------


def _vec(seed: float, dim: int = 8) -> list[float]:
    v = [0.0] * dim
    v[int(seed) % dim] = 1.0
    v[(int(seed) + 1) % dim] = seed - int(seed)
    return v


def test_store_增删查(tmp_path):
    s = SQLiteVecStore(tmp_path / "kb.db")
    s.add(["a", "b"], [_vec(1), _vec(2)], ["d1", "d1"], ["文本甲", "文本乙"])
    assert s.count() == 2

    hits = s.search(_vec(1), top_k=2)
    assert hits[0].chunk_id == "a"
    assert hits[0].text == "文本甲"
    assert hits[0].source == "d1"

    assert s.delete_by_source("d1") == 2
    assert s.count() == 0
    assert s.search(_vec(1)) == []
    s.close()


def test_store_空库返回空结果(tmp_path):
    s = SQLiteVecStore(tmp_path / "kb.db")
    assert s.count() == 0
    assert s.search(_vec(1)) == []
    s.close()


def test_store_维度不匹配拒绝写入(tmp_path):
    s = SQLiteVecStore(tmp_path / "kb.db")
    s.add(["a"], [_vec(1, 8)], ["d"], ["x"])
    with pytest.raises(ValueError, match="维度不匹配"):
        s.add(["b"], [_vec(2, 4)], ["d"], ["y"])
    s.close()


def test_store_按来源删除不影响其他来源(tmp_path):
    s = SQLiteVecStore(tmp_path / "kb.db")
    s.add(["a", "b"], [_vec(1), _vec(2)], ["f1", "f2"], ["甲", "乙"])
    assert s.delete_by_source("f1") == 1
    assert s.count() == 1
    assert s.search(_vec(2))[0].chunk_id == "b"
    s.close()


# ---------- 工具端到端（FakeEmbedder 注入，不发真网） ----------


@pytest.fixture
def kb_cfg(tmp_path):
    cfg = Config(workspace=tmp_path, home=tmp_path / "home")
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def fake_embed(monkeypatch):
    e = FakeEmbedder(dim=32)
    monkeypatch.setattr("ohopilot.tools.kb.build_embedder", lambda cfg: e)
    return e


async def test_kb_index_与_search_端到端(kb_cfg, fake_embed, tmp_path):
    doc = tmp_path / "手册.md"
    doc.write_text("本产品的退款政策：七天内无理由退款。\n" * 3 + "# 其他章节\n" + "物流说明：顺丰包邮。\n" * 30)
    result = await KbIndexTool(kb_cfg).run(path="手册.md")
    assert not result.is_error, result.content
    assert "已索引" in result.content

    result = await KbSearchTool(kb_cfg).run(query="怎么退款")
    assert not result.is_error
    assert "退款政策" in result.content
    assert "手册.md" in result.content  # 来源可见


async def test_kb_index_重复索引覆盖旧块(kb_cfg, fake_embed, tmp_path):
    doc = tmp_path / "a.md"
    doc.write_text("第一版内容。\n" * 50)
    await KbIndexTool(kb_cfg).run(path="a.md")
    doc.write_text("第二版完全不同的内容。\n" * 50)
    result = await KbIndexTool(kb_cfg).run(path="a.md")
    assert "覆盖了旧索引" in result.content

    result = await KbSearchTool(kb_cfg).run(query="第一版内容")
    # 旧块已删：搜旧句子不该再有高相关结果（假向量对旧文本的匹配应弱于现库）
    assert "第一版内容" not in result.content


async def test_kb_search_空库给指引(kb_cfg, fake_embed):
    result = await KbSearchTool(kb_cfg).run(query="任何")
    assert "知识库是空的" in result.content
    assert "kb_index" in result.content


async def test_kb_未配置_embedding_报错带指引(kb_cfg):
    # kb_cfg.embedding_model 为空：不注入 fake_embedder，走真实 build_embedder
    result = await KbIndexTool(kb_cfg).run(path="x.md")
    assert result.is_error
    assert "OHOPILOT_EMBEDDING_MODEL" in result.content
    result = await KbSearchTool(kb_cfg).run(query="x")
    assert result.is_error
    assert "OHOPILOT_EMBEDDING_MODEL" in result.content


async def test_kb_index_文件不存在(kb_cfg, fake_embed):
    result = await KbIndexTool(kb_cfg).run(path="不存在.md")
    assert result.is_error
    assert "文件不存在" in result.content


async def test_kb_index_不支持的格式(kb_cfg, fake_embed, tmp_path):
    binary = tmp_path / "x.bin"
    binary.write_bytes(b"\x00\x01\x02")
    result = await KbIndexTool(kb_cfg).run(path="x.bin")
    assert result.is_error
    assert "不支持的格式" in result.content


async def test_kb_index_索引PDF走docs解析器(kb_cfg, fake_embed, tmp_path):
    """复用第 21 步解析器：测试里程序化构造合法 PDF 再索引。"""
    from tests.test_docs import _make_pdf

    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)
    result = await KbIndexTool(kb_cfg).run(path="doc.pdf")
    assert not result.is_error, result.content

    result = await KbSearchTool(kb_cfg).run(query="Hello OhoPilot PDF")
    assert "doc.pdf" in result.content
