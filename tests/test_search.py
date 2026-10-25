"""web_search 的行为契约。

测试纪律（conftest 三条）：不发真实网络——这里用 httpx.MockTransport 假冒三个
搜索后端，顺带把「请求长什么样」（路径、参数、鉴权头）也钉进断言。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ohopilot.config import Config
from ohopilot.tools.search import (
    MAX_RESULTS_CAP,
    MAX_SNIPPET_CHARS,
    WebSearchTool,
    build_search_tools,
)


def make_cfg(**over) -> Config:
    """测试专用配置：不碰真实 ~/.ohopilot，搜索字段直接给死。"""
    fields = {
        "search_provider": "searxng",
        "searxng_base_url": "http://127.0.0.1:8080",
        "tavily_api_key": "",
        "bocha_api_key": "",
        "workspace": Path("/tmp/oho-search-test"),
    }
    fields.update(over)
    return Config(**fields)


# ---------- 三个后端的响应假货 ----------

def searxng_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/search"
    assert request.url.params["format"] == "json"
    assert request.url.params["q"]  # query 必须带上了
    return httpx.Response(200, json={"results": [
        {"title": "创业板今日涨幅榜", "url": "https://finance.example.com/cyb",
         "content": "今日创业板涨幅居前的股票为中际旭创、新易盛…"},
        {"title": "第二条", "url": "https://example.com/two",
         "content": "长" * 500},  # 故意超长，验证摘要截断
    ]})


def tavily_handler(request: httpx.Request) -> httpx.Response:
    # 鉴权方式钉死：Bearer 头，不是塞在 body 里
    assert request.headers["authorization"] == "Bearer tvly-test-key"
    assert request.url.host == "api.tavily.com"
    return httpx.Response(200, json={"results": [
        {"title": "Tavily 结果", "url": "https://example.com/tav", "content": "来自 tavily 的摘要"},
    ]})


def bocha_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer bocha-test-key"
    return httpx.Response(200, json={"code": 200, "data": {"webPages": {"value": [
        {"name": "博查结果", "url": "https://example.com/bocha",
         "snippet": "网页截取的摘要", "summary": "AI 提炼的更贴题摘要"},
    ]}}})


def mock(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ---------- 行为 ----------

async def test_searxng_返回规整结果_超长摘要被截断():
    tool = WebSearchTool(make_cfg(), transport=mock(searxng_handler))

    result = await tool.run(query="今日创业板涨幅榜")

    assert not result.is_error
    assert "共 2 条搜索结果" in result.content
    assert "创业板今日涨幅榜" in result.content
    assert "https://finance.example.com/cyb" in result.content
    # 第二条 500 字的摘要被截到上限，且末尾带省略号
    assert "…" in result.content
    assert "长" * (MAX_SNIPPET_CHARS + 1) not in result.content
    assert "web_fetch" in result.content  # 提示模型下一步用哪个工具深读


async def test_结果为空时提示换关键词():
    empty = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
    result = await WebSearchTool(make_cfg(), transport=empty).run(query="不存在的词")

    assert not result.is_error
    assert "没有搜到" in result.content
    assert "换个更具体的关键词" in result.content


async def test_tavily_缺key直接失败并给申请指引():
    result = await WebSearchTool(make_cfg(search_provider="tavily")).run(query="x")

    assert result.is_error
    assert "TAVILY_API_KEY" in result.content
    assert "tavily.com" in result.content  # 指路，而不是让模型对着 401 猜


async def test_tavily_走Bearer头并解析results():
    cfg = make_cfg(search_provider="tavily", tavily_api_key="tvly-test-key")
    result = await WebSearchTool(cfg, transport=mock(tavily_handler)).run(query="x")

    assert not result.is_error
    assert "Tavily 结果" in result.content
    assert "来自 tavily 的摘要" in result.content


async def test_bocha_解析webPages嵌套_优先用AI摘要():
    cfg = make_cfg(search_provider="bocha", bocha_api_key="bocha-test-key")
    result = await WebSearchTool(cfg, transport=mock(bocha_handler)).run(query="x")

    assert not result.is_error
    assert "博查结果" in result.content
    # summary 比 snippet 更贴题，优先选它
    assert "AI 提炼的更贴题摘要" in result.content
    assert "网页截取的摘要" not in result.content


async def test_未知provider报错并列出可选值():
    result = await WebSearchTool(make_cfg(search_provider="google")).run(query="x")

    assert result.is_error
    assert "google" in result.content
    assert "searxng/tavily/bocha" in result.content


async def test_空query被拒绝():
    result = await WebSearchTool(make_cfg(), transport=mock(searxng_handler)).run(query="  ")

    assert result.is_error
    assert "query 不能为空" in result.content


async def test_max_results_默认5_上限封顶10():
    many = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"results": [
            {"title": f"第{i}条", "url": f"https://e.com/{i}", "content": "摘要"}
            for i in range(30)
        ]}))
    tool = WebSearchTool(make_cfg(), transport=many)

    default = await tool.run(query="x")
    assert "共 5 条搜索结果" in default.content

    capped = await tool.run(query="x", max_results=50)
    assert f"共 {MAX_RESULTS_CAP} 条搜索结果" in capped.content


async def test_搜索服务挂了返回失败而不是抛异常():
    dead = httpx.MockTransport(lambda request: httpx.Response(500))
    result = await WebSearchTool(make_cfg(), transport=dead).run(query="x")

    assert result.is_error
    assert "搜索失败" in result.content


def test_注册入口产出web_search工具():
    tools = build_search_tools(make_cfg())
    assert [t.name for t in tools] == ["web_search"]
