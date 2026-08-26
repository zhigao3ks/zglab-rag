"""Phase 12B tests: SafeFetcher behavior & deterministic extraction.

Offline via httpx.MockTransport + fake DNS. Covers content types, status
codes, timeouts, oversize, redirect chains (incl. redirect-to-private),
content-type allowlist and the HTML extraction fixture assertions.
"""

from __future__ import annotations

import httpx
import pytest

from tests.test_research_safety import PUBLIC, FakeResolver
from zglab_rag.research.contracts import FetchFailureReason, ResearchBudget
from zglab_rag.research.extract import extract_html, extract_plain_text
from zglab_rag.research.fetch import SafeFetcher

ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>测试文章标题</title>
  <style>.x { color: red; }</style>
  <script>var secret = "should vanish";</script>
</head>
<body>
  <nav>首页 链接 导航栏</nav>
  <header>站点头部</header>
  <div id="cookie-banner">我们使用 Cookie，请同意</div>
  <article>
    <h1>主标题</h1>
    <p>第一段正文内容，关于检索增强生成的介绍。</p>
    <h2>小节</h2>
    <p>第二段正文内容，引用验证的说明。</p>
  </article>
  <footer>版权信息 页脚</footer>
  <form><input name="email"/></form>
</body>
</html>
"""


def _fetcher(handler, budget: ResearchBudget | None = None, resolver=None) -> SafeFetcher:
    budget = budget or ResearchBudget()
    return SafeFetcher(budget, resolver=resolver, transport=httpx.MockTransport(handler))


def _resolver(**entries) -> FakeResolver:
    return FakeResolver(dict(entries))


# ---------------------------------------------------------------------------
# Fetch scenarios
# ---------------------------------------------------------------------------


def test_fetch_200_html_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ARTICLE_HTML, headers={"content-type": "text/html"})

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/article")
    assert outcome.ok
    assert outcome.page is not None
    assert "第一段正文内容" in outcome.page.text
    assert outcome.page.content_type == "text/html"


def test_fetch_200_text_plain_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="plain body", headers={"content-type": "text/plain"})

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/notes.txt")
    assert outcome.ok
    assert outcome.page is not None
    assert outcome.page.text == "plain body"


@pytest.mark.parametrize("status", [404, 500])
def test_fetch_http_errors(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content="x")

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/missing")
    assert not outcome.ok
    assert outcome.reason == FetchFailureReason.HTTP_ERROR


def test_fetch_unsupported_content_type_from_header_not_extension() -> None:
    # A .html-looking path serving application/pdf must be refused based on
    # the Content-Type header, never the file extension.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/page.html")
    assert outcome.reason == FetchFailureReason.UNSUPPORTED_CONTENT_TYPE


def test_fetch_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out")

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/slow")
    assert outcome.reason == FetchFailureReason.TIMEOUT


def test_fetch_oversize_aborts() -> None:
    big = b"a" * 5_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big, headers={"content-type": "text/plain"})

    budget = ResearchBudget(max_response_bytes=1_000)
    fetcher = _fetcher(handler, budget, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/huge")
    assert outcome.reason == FetchFailureReason.OVERSIZE


def test_fetch_redirect_success_records_chain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(200, content="final body", headers={"content-type": "text/plain"})

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/start")
    assert outcome.ok
    assert outcome.page is not None
    assert outcome.page.final_url == "https://example.com/final"
    assert outcome.page.redirect_chain == ("https://example.com/start",)


def test_fetch_redirect_to_private_rejected() -> None:
    # Public first hop, then a redirect to loopback: the second hop must be
    # re-validated and refused (no blind follow).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/hop")
    assert outcome.reason == FetchFailureReason.SSRF_REJECTED


def test_fetch_redirect_to_private_hostname_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.example/x"})

    resolver = FakeResolver({"example.com": [PUBLIC], "internal.example": ["10.1.2.3"]})
    fetcher = _fetcher(handler, resolver=resolver)
    outcome = fetcher.fetch("https://example.com/hop")
    assert outcome.reason == FetchFailureReason.SSRF_REJECTED


def test_fetch_redirect_loop_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        other = "/b" if request.url.path == "/a" else "/a"
        return httpx.Response(302, headers={"location": f"https://example.com{other}"})

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/a")
    assert outcome.reason == FetchFailureReason.TOO_MANY_REDIRECTS


def test_fetch_first_hop_private_rejected_without_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content="x")

    fetcher = _fetcher(handler, resolver=_resolver())
    outcome = fetcher.fetch("http://127.0.0.1/admin")
    assert outcome.reason == FetchFailureReason.SSRF_REJECTED
    assert seen == []  # validation happens before any HTTP request


def test_fetch_no_cookies_no_auth_sent() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200, content="ok", headers={"content-type": "text/plain"})

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    assert fetcher.fetch("https://example.com/").ok
    assert "cookie" not in captured
    assert "authorization" not in captured
    assert captured.get("user-agent", "").startswith("zglab-rag-research/")


def test_fetch_invalid_encoding_falls_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\xff\xfe raw", headers={"content-type": "text/plain; charset=utf-8"}
        )

    fetcher = _fetcher(handler, resolver=_resolver(**{"example.com": [PUBLIC]}))
    outcome = fetcher.fetch("https://example.com/bad")
    assert outcome.ok  # decoded with errors=replace, never crashes


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_extract_html_keeps_article_drops_boilerplate() -> None:
    extracted = extract_html(ARTICLE_HTML, max_chars=8_000)
    assert extracted.title == "测试文章标题"
    assert "第一段正文内容" in extracted.text
    assert "第二段正文内容" in extracted.text
    assert "主标题" in extracted.text
    # Script / style / nav / footer / cookie banner never enter evidence.
    for forbidden in ("should vanish", "color: red", "导航栏", "页脚", "版权信息", "Cookie"):
        assert forbidden not in extracted.text


def test_extract_html_char_limit() -> None:
    html = "<html><body><article><p>" + "长" * 500 + "</p></article></body></html>"
    extracted = extract_html(html, max_chars=100)
    assert len(extracted.text) <= 100


def test_extract_html_empty_page_yields_empty_text() -> None:
    extracted = extract_html("<html><body><nav>只有导航</nav></body></html>", max_chars=1000)
    assert extracted.text.strip() == ""


def test_extract_plain_text_normalized() -> None:
    extracted = extract_plain_text("  a   b \n\n\n c ", max_chars=100)
    assert extracted.text == "a b\nc"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
