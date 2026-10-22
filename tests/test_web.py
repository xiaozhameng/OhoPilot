"""web_fetch 的 SSRF 防线与 HTML 提取。

这里不发任何真实请求：DNS 解析和下载都用 monkeypatch 换掉。
真正要守住的是「模型编一个 URL 也摸不到本机和内网」。
"""

from __future__ import annotations

import socket

import pytest

from ohopilot.tools import web
from ohopilot.tools.web import WebFetchTool, _check_host, html_to_text


def _fake_dns(ip: str):
    """把任意域名解析成指定 IP，用来测「名字像公网、实际是内网」这类绕过。"""

    def _resolver(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return _resolver


def test_本地名被拒绝():
    for host in ["localhost", "LOCALHOST", "app.local", "svc.internal", "db.localhost"]:
        assert _check_host(host) != ""


def test_空主机名被拒绝():
    assert _check_host("") != ""


def test_公网_IP_放行(monkeypatch):
    monkeypatch.setattr(web.socket, "getaddrinfo", _fake_dns("1.1.1.1"))
    assert _check_host("example.com") == ""


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",       # 回环
        "10.0.0.5",        # 内网 A 段
        "192.168.1.1",     # 内网 C 段
        "172.16.0.1",      # 内网 B 段
        "169.254.169.254",  # 云厂商元数据地址（能拿到临时凭据）
        "0.0.0.0",
    ],
)
def test_解析到内网地址被拒绝(ip, monkeypatch):
    monkeypatch.setattr(web.socket, "getaddrinfo", _fake_dns(ip))
    assert "内网" in _check_host("evil.example.com")


def test_域名解析失败要报错而不是放行(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(web.socket, "getaddrinfo", boom)
    assert "解析失败" in _check_host("nope.invalid")


async def test_非_http_scheme_被拒绝():
    result = await WebFetchTool().run(url="file:///etc/passwd")
    assert result.is_error
    assert "只支持 http/https" in result.content


async def test_内网地址在下载之前就被拦住(monkeypatch):
    """校验必须在发请求前完成，否则已经连上去了。"""
    called = {"download": False}

    async def fake_download(self, url):
        called["download"] = True
        return "", url, "text/html"

    monkeypatch.setattr(web.socket, "getaddrinfo", _fake_dns("127.0.0.1"))
    monkeypatch.setattr(WebFetchTool, "_download", fake_download)

    result = await WebFetchTool().run(url="http://127.0.0.1.nip.io/admin")
    assert result.is_error
    assert called["download"] is False


# ---------- HTML 提取 ----------

def test_提取标题与正文():
    html = """
    <html><head><title>我的文章</title><style>p{color:red}</style></head>
    <body><h1>标题</h1><p>第一段</p><script>alert(1)</script><p>第二段</p></body></html>
    """
    title, text = html_to_text(html)

    assert title == "我的文章"
    assert "第一段" in text and "第二段" in text
    assert "alert(1)" not in text
    assert "color:red" not in text


def test_块级元素之间会断行():
    _, text = html_to_text("<div>甲</div><div>乙</div>")
    # 起始标签和结束标签各补一个换行，所以中间会留一个空行；正文行必须分开
    assert [line for line in text.splitlines() if line] == ["甲", "乙"]


def test_实体被还原():
    _, text = html_to_text("<p>a &amp; b &lt;c&gt;</p>")
    assert "a & b <c>" in text


async def test_纯文本内容原样返回(monkeypatch):
    async def fake_download(self, url):
        return "hello\nworld", "https://example.com/a.txt", "text/plain"

    monkeypatch.setattr(web.socket, "getaddrinfo", _fake_dns("1.1.1.1"))
    monkeypatch.setattr(WebFetchTool, "_download", fake_download)

    result = await WebFetchTool().run(url="https://example.com/a.txt")
    assert not result.is_error
    assert "hello" in result.content


async def test_没有正文的页面算失败(monkeypatch):
    async def fake_download(self, url):
        return "<html><body><script>x</script></body></html>", url, "text/html"

    monkeypatch.setattr(web.socket, "getaddrinfo", _fake_dns("1.1.1.1"))
    monkeypatch.setattr(WebFetchTool, "_download", fake_download)

    result = await WebFetchTool().run(url="https://example.com/spa")
    assert result.is_error
    assert "没有可读正文" in result.content


def test_抓网页不需要审批():
    """只读操作，不改用户电脑；弹窗只会让人烦。"""
    assert WebFetchTool().needs_approval({"url": "https://example.com"}) == (False, "")
