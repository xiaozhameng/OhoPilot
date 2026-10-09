"""联网工具：抓网页 + 搜网页（只读，不需要确认）。"""

from __future__ import annotations

import html
import os
import re
from typing import Any
from urllib.parse import quote_plus

import httpx

from .base import Tool, ToolResult

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) OhoPilot/0.1"
MAX_PAGE_CHARS = 20_000

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", text, flags=re.IGNORECASE)
    text = _ANY_TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()


class FetchUrlTool(Tool):
    name = "web.fetch"
    description = "抓取一个网页并转成纯文本。用于读文档、看公告、取页面内容。"
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整的 http/https 地址"},
            "max_chars": {"type": "integer", "description": "最多返回字符数，默认 20000"},
        },
        "required": ["url"],
    }

    async def run(self, url: str, max_chars: int = MAX_PAGE_CHARS, **_: Any) -> ToolResult:
        if not url.startswith(("http://", "https://")):
            return ToolResult.fail("url 必须以 http:// 或 https:// 开头")
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True, headers={"User-Agent": UA}
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "html" not in content_type and "text" not in content_type:
                    return ToolResult.fail(f"不支持的内容类型：{content_type}")
                text = html_to_text(resp.text)
        except httpx.HTTPError as exc:
            return ToolResult.fail(f"抓取失败：{exc}")

        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[内容过长已截断]"
        return ToolResult.ok(f"{url}\n\n{text}")


class WebSearchTool(Tool):
    name = "web.search"
    description = (
        "在互联网上搜索信息，返回若干条标题、链接和摘要。"
        "需要查最新资讯、查不确定的事实时使用。搜索完可以再用 web.fetch 打开具体页面。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "limit": {"type": "integer", "description": "返回条数，默认 6"},
        },
        "required": ["query"],
    }

    async def run(self, query: str, limit: int = 6, **_: Any) -> ToolResult:
        # 优先用用户自备的搜索 API，没有就退回到 DuckDuckGo 的 HTML 端点
        api_key = os.getenv("OHOPILOT_SEARCH_API_KEY") or os.getenv("TAVILY_API_KEY")
        if api_key:
            return await self._search_tavily(query, limit, api_key)
        return await self._search_duckduckgo(query, limit)

    async def _search_tavily(self, query: str, limit: int, api_key: str) -> ToolResult:
        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
        }
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post("https://api.tavily.com/search", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ToolResult.fail(f"搜索失败：{exc}")

        lines = []
        for item in data.get("results", [])[:limit]:
            lines.append(
                f"- {item.get('title', '')}\n  {item.get('url', '')}\n  {item.get('content', '')[:300]}"
            )
        return ToolResult.ok("\n".join(lines) or "没有结果")

    async def _search_duckduckgo(self, query: str, limit: int) -> ToolResult:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True, headers={"User-Agent": UA}
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                raw = resp.text
        except httpx.HTTPError as exc:
            return ToolResult.fail(
                f"搜索失败（{exc}）。当前环境可能无法直连搜索引擎，"
                "可在 .env 里配置 TAVILY_API_KEY 换成 Tavily。"
            )

        items = re.findall(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            raw,
            flags=re.DOTALL,
        )
        snippets = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', raw, flags=re.DOTALL
        )
        lines = []
        for idx, (link, title) in enumerate(items[:limit]):
            snippet = html_to_text(snippets[idx]) if idx < len(snippets) else ""
            lines.append(f"- {html_to_text(title)}\n  {link}\n  {snippet[:300]}")
        return ToolResult.ok("\n".join(lines) or f"没有搜到「{query}」的结果")


def build_web_tools() -> list[Tool]:
    return [FetchUrlTool(), WebSearchTool()]
