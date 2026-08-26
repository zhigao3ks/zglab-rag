"""Deterministic content extraction & normalization (Phase 12B).

HTML -> clean text WITHOUT any LLM ("summarize this page" extraction is
forbidden in 12B). Boilerplate (script / style / nav / footer / forms /
iframes / SVG / cookie banners) is removed; main textual content is kept;
the result is whitespace-normalized and char-bounded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

# Tags whose content never becomes evidence text.
_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "iframe",
    "svg",
    "canvas",
    "video",
    "audio",
    "button",
)
# id/class substrings that mark obvious boilerplate (cookie banners etc.).
_BOILERPLATE_MARKERS = ("cookie", "consent", "gdpr", "newsletter", "advert")
_WHITESPACE_LINE = re.compile(r"[ \t\u00a0]+")


@dataclass(frozen=True, slots=True)
class ExtractedContent:
    title: str
    text: str


def extract_html(html: str, *, max_chars: int) -> ExtractedContent:
    """Deterministically reduce an HTML document to title + main text."""
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    title = _clean_line(title_tag.get_text(" ", strip=True)) if title_tag else ""

    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(attrs={"hidden": True}):
        tag.decompose()
    for tag in soup.find_all(attrs={"role": lambda value: value in ("navigation", "banner")}):
        tag.decompose()
    for tag in soup.find_all(attrs={"aria-hidden": "true"}):
        tag.decompose()
    for tag in soup.find_all(id=_matches_boilerplate):
        tag.decompose()
    for tag in soup.find_all(class_=_matches_boilerplate):
        tag.decompose()

    container = soup.find("article") or soup.find("main") or soup.body or soup
    lines = [
        _clean_line(block.get_text(" ", strip=True))
        for block in container.find_all(
            ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "td"]
        )
    ]
    if not any(lines):
        # Pages without block markup: fall back to the container's text.
        lines = [_clean_line(container.get_text("\n", strip=True))]
    text = _truncate("\n".join(line for line in lines if line), max_chars)
    return ExtractedContent(title=title, text=text)


def extract_plain_text(text: str, *, max_chars: int) -> ExtractedContent:
    """Normalize a text/plain body (no title channel)."""
    return ExtractedContent(title="", text=_truncate(_clean_line(text), max_chars))


def _matches_boilerplate(value) -> bool:
    if value is None:
        return False
    joined = " ".join(value).lower() if isinstance(value, list) else str(value).lower()
    return any(marker in joined for marker in _BOILERPLATE_MARKERS)


def _clean_line(text: str) -> str:
    collapsed = _WHITESPACE_LINE.sub(" ", text)
    lines = [line.strip() for line in collapsed.split("\n")]
    return "\n".join(line for line in lines if line)


def _truncate(text: str, max_chars: int) -> str:
    return text[:max_chars]
