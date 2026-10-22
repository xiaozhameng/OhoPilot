"""网络工具：抓一个网页，转成纯文本交给模型。

只用 httpx（本来就是依赖），不引 bs4——「读一篇文章」用简易提取就够，
少一个依赖就少一处将来的升级负担。

真实的安全重点是 SSRF：模型可以随便编 URL，绝不能让它摸到本机和内网。
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from ohopilot.tools.base import Tool, ToolResult

TIMEOUT_SECONDS = 20.0
#: 下载上限：先看 Content-Length，再边下边数，超了就停。防一个几百 MB 的文件把内存打满
MAX_BYTES = 2 * 1024 * 1024
#: 交给模型的字符上限（工具基类还有一层 12000 的兜底截断）
MAX_CHARS = 40_000
MAX_REDIRECTS = 3

BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost"}
BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".home.arpa")


def _check_host(host: str) -> str:
    """返回空串表示放行，否则返回拒绝理由。

    分两步：先挡明显的本地名，再解析成 IP 判断是否内网。
    第二步不能省——`127.0.0.1.nip.io` 这种域名看起来是公网，解析结果却是回环地址。
    """
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return "URL 里没有主机名"
    if h in BLOCKED_HOSTS or h.endswith(BLOCKED_SUFFIXES):
        return f"拒绝访问本机地址：{host}"

    try:
        infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return f"域名解析失败：{host}"

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        # is_private 覆盖回环、内网段、链路本地（169.254 含云厂商元数据地址）
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return f"拒绝访问内网地址：{host} → {ip}"
    return ""


class _TextExtractor(HTMLParser):
    """把 HTML 压成「像文章一样」的纯文本。

    刻意简陋：丢掉 script/style/head，块级元素处断行，其余文字照收。
    目的是让模型能读，不是还原排版。
    """

    SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head", "iframe"}
    BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "hr", "section", "article", "blockquote",
        "pre", "table", "ul", "ol", "dd", "dt",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # &amp; 之类的实体自动还原
        self.parts: list[str] = []
        self.title: str = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()
            return
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        # 逐行剥空白 + 折叠连续空行：HTML 源码里的缩进会变成大片空白
        lines = [line.strip() for line in raw.splitlines()]
        out: list[str] = []
        for line in lines:
            if line:
                out.append(line)
            elif out and out[-1] != "":
                out.append("")
        return "\n".join(out).strip()


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.title, parser.text()


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "抓取一个网页并返回正文纯文本。适合读文档、博客、新闻、API 说明。"
        "只支持 http/https 的公网地址，不能访问本机与内网。"
        "返回内容有长度上限，超长会截断。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整的网址，必须带 http:// 或 https://"},
        },
        "required": ["url"],
    }

    #: 只读操作，不改你的电脑，不必审批
    requires_approval = False

    async def run(self, url: str = "", **_: Any) -> ToolResult:
        target = (url or "").strip()
        parsed = urlparse(target)

        if parsed.scheme not in {"http", "https"}:
            return ToolResult.fail(f"只支持 http/https，收到的是：{parsed.scheme or '空'}")
        # 解析 DNS 是阻塞操作，丢到线程里，别卡住事件循环
        reason = await asyncio.to_thread(_check_host, parsed.hostname or "")
        if reason:
            return ToolResult.fail(reason)

        try:
            body, final_url, content_type = await self._download(target)
        except httpx.HTTPError as exc:
            return ToolResult.fail(f"抓取失败：{type(exc).__name__}: {exc}")

        if "html" not in content_type and "xml" not in content_type:
            # 纯文本（.txt / .md / JSON）不解析，原样给
            text = body[:MAX_CHARS]
            header = f"{final_url}\n类型：{content_type or '未知'}\n\n"
            return ToolResult.ok(header + text)

        title, text = html_to_text(body)
        if not text:
            return ToolResult.fail(f"页面没有可读正文（可能是纯 JS 渲染）：{final_url}")

        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n\n…[正文超过 {MAX_CHARS} 字符，已截断]"

        header = f"{final_url}\n标题：{title or '（无）'}\n\n"
        return ToolResult.ok(header + text)

    async def _download(self, url: str) -> tuple[str, str, str]:
        """流式下载：边收边数，到上限就断开。返回 (正文, 最终地址, 内容类型)。"""
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            headers={"User-Agent": "OhoPilot/0.1 (+local agent)"},
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "").lower()

                declared = resp.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > MAX_BYTES:
                    raise httpx.HTTPError(f"内容太大（{int(declared) // 1024} KB），已放弃")

                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes(8192):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        chunks.append("\n...[已超过 2MB 下载上限，提前结束]".encode("utf-8"))
                        break
                    chunks.append(chunk)

                raw = b"".join(chunks)
                encoding = resp.encoding or "utf-8"
                try:
                    text = raw.decode(encoding, errors="replace")
                except LookupError:
                    # 站点声明了不认识的编码，退回 UTF-8
                    text = raw.decode("utf-8", errors="replace")
                # 兜底：声明 utf-8 实际是 GBK 的国内站点不少，中文乱码时换一次编码重试
                if text.count("\ufffd") > 20:
                    try:
                        text = raw.decode("gb18030", errors="replace")
                    except (UnicodeDecodeError, LookupError):
                        pass
                return text, str(resp.url), content_type


def build_web_tools() -> list[Tool]:
    return [WebFetchTool()]
