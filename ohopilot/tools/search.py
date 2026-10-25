"""搜索工具：模型给关键词，返回「标题 + 链接 + 摘要」列表。

和 web_fetch 的分工：search 负责「找到」，fetch 负责「读透」。
模型先用 search 拿到候选列表，再挑一条用 web_fetch 深读。

三个后端（provider）可切换，差异只在「请求怎么发、响应怎么解析」，
所以每个后端就是一个函数 + PROVIDERS 里一个名字，不搞类继承。
统一结果格式：[{"title", "url", "snippet"}, ...]

安全边界（和 web_fetch 的 SSRF 防护对比着看）：
搜索端点全部来自 .env 配置，模型只能控制 query 这个字符串——
「去哪查」它说了不算，所以这里不需要 SSRF 防护。
searxng 通常是本机/局域网自建服务，客户端 trust_env=False，
避免环境变量里的 HTTP_PROXY 把本机请求也劫走（比如 Clash）；
tavily / bocha 是公网 API，保留 trust_env=True（可能需要代理才能通）。
"""

from __future__ import annotations

from typing import Any

import httpx

from ohopilot.config import Config
from ohopilot.tools.base import Tool, ToolResult

TIMEOUT_SECONDS = 15.0
#: 单条摘要交给模型的字符上限——10 条 × 300 字远小于基类 12000 的兜底线
MAX_SNIPPET_CHARS = 300
DEFAULT_RESULTS = 5
#: 上限封顶：模型要求 50 条也不给，防一次搜索撑爆上下文
MAX_RESULTS_CAP = 10

UA = "OhoPilot/0.1 (+local agent)"


async def _searxng(client: httpx.AsyncClient, cfg: Config, query: str, n: int) -> list[dict[str, str]]:
    """自建 SearXNG。注意：它的 JSON 输出默认关闭，
    要在 settings.yml 的 search.formats 里加上 json（见 README）。"""
    resp = await client.get(
        f"{cfg.searxng_base_url}/search",
        params={"q": query, "format": "json", "language": "zh-CN"},
    )
    resp.raise_for_status()
    rows = resp.json().get("results", [])[:n]
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in rows
    ]


async def _tavily(client: httpx.AsyncClient, cfg: Config, query: str, n: int) -> list[dict[str, str]]:
    """Tavily：免费 1000 次/月，Agent 生态最常用。国内访问可能需要代理。"""
    resp = await client.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {cfg.tavily_api_key}"},
        json={"query": query, "max_results": n},
    )
    resp.raise_for_status()
    rows = resp.json().get("results", [])[:n]
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in rows
    ]


async def _bocha(client: httpx.AsyncClient, cfg: Config, query: str, n: int) -> list[dict[str, str]]:
    """博查：付费（≈0.03 元/次），中文效果最好，国内直连。"""
    resp = await client.post(
        "https://api.bochaai.com/v1/web-search",
        headers={"Authorization": f"Bearer {cfg.bocha_api_key}"},
        json={"query": query, "summary": True, "count": n, "freshness": "noLimit"},
    )
    resp.raise_for_status()
    # 响应嵌套：data.webPages.value[]，每条是 {name, url, snippet, summary}
    rows = resp.json()["data"]["webPages"]["value"][:n]
    return [
        {
            "title": r.get("name", ""),
            "url": r.get("url", ""),
            # summary 是博查用 AI 提炼的摘要，比网页截取的 snippet 更贴题，优先用它
            "snippet": r.get("summary") or r.get("snippet", ""),
        }
        for r in rows
    ]


#: provider 名字 → 实现。加第四家 = 写一个函数 + 在这里加一行
PROVIDERS = {
    "searxng": _searxng,
    "tavily": _tavily,
    "bocha": _bocha,
}


def _render(hits: list[dict[str, str]], query: str) -> str:
    """把统一格式压成给模型看的紧凑文本。"""
    if not hits:
        return f"没有搜到与「{query}」相关的结果。可以换个更具体的关键词重试。"
    lines = [f"共 {len(hits)} 条搜索结果（序号 | 标题 | 链接 | 摘要）："]
    for i, hit in enumerate(hits, 1):
        snippet = hit["snippet"].strip()
        if len(snippet) > MAX_SNIPPET_CHARS:
            snippet = snippet[:MAX_SNIPPET_CHARS] + "…"
        lines.append(f"{i}. {hit['title'].strip()}\n   {hit['url']}\n   {snippet}")
    lines.append("需要某条的完整内容时，用 web_fetch 抓对应的链接。")
    return "\n".join(lines)


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "用关键词搜索公开网页，返回「标题 + 链接 + 摘要」列表。"
        "适合：找资料来源、查新闻/榜单/文档位置、确认时效性信息。"
        "不适合：已知网址时（直接用 web_fetch）、查用户自己的文件（用 fs_search）。"
        "需要某条的完整内容时再调用 web_fetch 抓链接，不要靠摘要下结论。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，几个到十几个词效果最好"},
            "max_results": {
                "type": "integer",
                "description": "要几条结果，默认 5，最多 10。通常不需要填",
            },
        },
        "required": ["query"],
    }

    #: 只读操作，不审批
    requires_approval = False

    def __init__(self, cfg: Config, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # transport 是测试注入点：传 httpx.MockTransport 就不发真实网络请求
        self._cfg = cfg
        self._transport = transport

    async def run(self, query: str = "", max_results: int | None = None, **_: Any) -> ToolResult:
        query = (query or "").strip()
        if not query:
            return ToolResult.fail("query 不能为空。给一个具体的搜索关键词。")

        provider = self._cfg.search_provider
        fn = PROVIDERS.get(provider)
        if fn is None:
            return ToolResult.fail(
                f"未知的搜索后端：{provider}。可选：{'/'.join(PROVIDERS)}，"
                "在 .env 的 OHOPILOT_SEARCH_PROVIDER 里改。"
            )

        # searxng 不需要 key（自建服务），另外两家缺 key 直接指路，别等请求 401 才说
        if provider == "tavily" and not self._cfg.tavily_api_key:
            return ToolResult.fail(
                "搜索后端是 tavily 但没配密钥。去 https://tavily.com 免费注册"
                "（1000 次/月），把 key 填进 .env 的 OHOPILOT_TAVILY_API_KEY。"
            )
        if provider == "bocha" and not self._cfg.bocha_api_key:
            return ToolResult.fail(
                "搜索后端是 bocha 但没配密钥。去 https://bochaai.com 注册充值"
                "（约 0.03 元/次，中文效果好），把 key 填进 .env 的 OHOPILOT_BOCHA_API_KEY。"
            )

        n = min(int(max_results or DEFAULT_RESULTS), MAX_RESULTS_CAP)

        # 本机自建服务不走系统代理；公网 API 保留代理（国内访问 tavily 常需要）
        client_kwargs: dict[str, Any] = {
            "timeout": TIMEOUT_SECONDS,
            "headers": {"User-Agent": UA},
        }
        if provider == "searxng":
            client_kwargs["trust_env"] = False
        try:
            async with httpx.AsyncClient(transport=self._transport, **client_kwargs) as client:
                hits = await fn(client, self._cfg, query, n)
        except httpx.HTTPError as exc:
            return ToolResult.fail(
                f"搜索失败：{type(exc).__name__}: {exc}。"
                "检查搜索服务是否可用（searxng 需先 docker 启动并开启 json 输出）。"
            )
        except (KeyError, ValueError, TypeError) as exc:
            return ToolResult.fail(f"搜索响应格式不符合预期（{exc}），检查服务端版本。")

        return ToolResult.ok(_render(hits, query))


def build_search_tools(cfg: Config) -> list[Tool]:
    return [WebSearchTool(cfg)]
