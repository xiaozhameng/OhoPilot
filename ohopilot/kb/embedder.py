""" 向量化：文本 → embedding 向量

两个实现：
- LitellmEmbedder：真实调用，走 OpenAI 兼容接口（litellm 薄层，和主模型同一策略）
- FakeEmbedder：测试用，按文本 hash 造确定性向量，不发真网

为什么 embedding 配置和主模型分开：DeepSeek 没有 embedding API，
主 key 调不了；且切换 embedding 模型会使旧向量全部作废（维度都变了），
独立配置让「换模型」这件事显式可见。
"""

from __future__ import annotations

import hashlib
import struct

from ohopilot.config import Config

#: 一次请求最多多少条文本。embedding API 普遍有单请求条数限制
BATCH_SIZE = 32


class Embedder:
    """接口：把一批文本变成等长的一组向量。"""

    name: str = "base"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_batched(self, texts: list[str]) -> list[list[float]]:
        """分批 embed，结果顺序和输入一致。"""
        vectors: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            vectors.extend(self.embed(texts[i : i + BATCH_SIZE]))
        return vectors


class FakeEmbedder(Embedder):
    """确定性假向量：同一文本永远得到同一向量，相近文本向量相近。

    做法：文本每个字符映射到固定维度的桶里累加——这样
    「包含相同字符多的两段文本」向量夹角小，KNN 排序符合直觉，
    测试能写出「A 比 B 更像查询 Q」这类断言。
    """

    def __init__(self, dim: int = 32) -> None:
        self.dim = dim
        self.name = f"fake:{dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for ch in text:
                idx = int(hashlib.md5(ch.encode()).hexdigest(), 16) % self.dim
                vec[idx] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


class LitellmEmbedder(Embedder):
    """真实实现：OpenAI 兼容 embedding 接口。

    model 写法沿用 litellm 惯例（provider 前缀可选，如
    openai/text-embedding-3-small 或 openai/BAAI/bge-m3 指向硅基流动）。
    """

    def __init__(self, model: str, api_base: str, api_key: str) -> None:
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.name = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        import litellm  # 惰性导入：没装 litellm 也能跑其他功能（其实主模型依赖它，防御性写法）

        resp = litellm.embedding(
            model=self.model,
            input=texts,
            api_base=self.api_base or None,
            api_key=self.api_key or None,
        )
        # litellm 统一返回 data[i]["embedding"]；个别后端嵌在 .vector 里，都兼容
        vectors: list[list[float]] = []
        for item in resp.data:
            emb = item.get("embedding") or getattr(item, "vector", None)
            if emb is None:
                raise ValueError(f"embedding 返回缺少向量字段: {item!r:.200}")
            vectors.append([float(x) for x in emb])
        return vectors


def build_embedder(cfg: Config) -> Embedder:
    """从配置构造 embedder。没配模型就抛带指引的错误——不静默退化。"""
    if not cfg.embedding_model:
        raise ValueError(
            "知识库需要 embedding 模型。请在 .env 里配置：\n"
            "  OHOPILOT_EMBEDDING_MODEL=openai/BAAI/bge-m3\n"
            "  OHOPILOT_EMBEDDING_API_BASE=https://api.siliconflow.cn/v1\n"
            "  OHOPILOT_EMBEDDING_API_KEY=你的key\n"
            "任何 OpenAI 兼容接口都可以（硅基流动的 bge-m3 有免费额度）。"
        )
    return LitellmEmbedder(
        cfg.embedding_model,
        cfg.embedding_api_base,
        cfg.embedding_api_key or cfg.api_key,
    )


def pack(vec: list[float]) -> bytes:
    """向量 → sqlite-vec 存储用的紧凑字节。"""
    return struct.pack(f"{len(vec)}f", *vec)
