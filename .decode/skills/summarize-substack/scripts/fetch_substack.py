# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "beautifulsoup4>=4.12", "markdownify>=0.13"]
# ///
"""Fetch a Substack post and write a clean Markdown copy of just the article body.

Usage:
    uv run fetch_substack.py <substack-url> [out.md]
    uv run fetch_substack.py --selftest        # offline check, no network

Substack renders the article inside ``<div class="available-content">`` with the prose in
``<div class="body markup">``. We pull that, drop subscribe/share/comment chrome, convert it to
Markdown, and write ``<title> / <subtitle> / <source meta> / <body>`` to ``<out>`` (default
``./<slug>.cleaned.md``). Prints the path + metadata + a short preview so the agent reads the file next.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# A real browser UA — Substack returns a stub page to obvious bots.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Chrome inside the article body to strip before converting to Markdown.
_JUNK_SELECTORS = [
    "script",
    "style",
    "noscript",
    ".subscribe-widget",
    ".subscription-widget-wrap",
    ".subscribe-dialog",
    ".button-wrapper",
    ".image-link-expand",
    ".share",
    ".post-ufi",
    ".like-button-container",
    ".comments",
    ".paywall",
    ".modal",
    "nav",
    "footer",
]


def fetch_html(url: str) -> str:
    """GET the page with a browser UA, following redirects (custom domains)."""
    try:
        resp = httpx.get(url, headers={"User-Agent": _UA}, follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise SystemExit(f"fetch failed: {e}") from None
    return resp.text


def extract(html: str) -> dict[str, str]:
    """Pull title/subtitle/author/date + the cleaned body Markdown out of a Substack page."""
    soup = BeautifulSoup(html, "html.parser")
    body_el = (
        soup.select_one("div.available-content")
        or soup.select_one("div.body.markup")
        or soup.find("article")
        or soup.body
    )
    if body_el is None:
        raise SystemExit("could not locate an article body in the page")
    for sel in _JUNK_SELECTORS:
        for el in body_el.select(sel):
            el.decompose()
    body_md = _tidy(md(str(body_el), heading_style="ATX", strip=["img", "button"]))
    return {
        "title": _first_text(soup, ["h1.post-title", 'meta[property="og:title"]', "title"]),
        "subtitle": _first_text(soup, ["h3.subtitle", 'meta[name="description"]']),
        "author": _first_text(soup, ['meta[name="author"]', 'a[rel="author"]']),
        "date": _attr(soup, 'meta[property="article:published_time"]', "content")
        or _attr(soup, "time", "datetime"),
        "body": body_md,
    }


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    """First non-empty text across ``selectors`` — ``content`` for <meta>, text otherwise."""
    for sel in selectors:
        el = soup.select_one(sel)
        if not el:
            continue
        val = (el.get("content", "") if el.name == "meta" else el.get_text(strip=True)).strip()
        if val:
            return val
    return ""


def _attr(soup: BeautifulSoup, selector: str, attr: str) -> str:
    el = soup.select_one(selector)
    return (el.get(attr) or "").strip() if el else ""


def _tidy(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _slugify(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1] or "article"
    return re.sub(r"[^a-z0-9-]+", "-", tail.lower()).strip("-") or "article"


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("usage: fetch_substack.py <substack-url> [out.md]")
    url = argv[0]
    out = Path(argv[1]) if len(argv) > 1 else Path(f"./{_slugify(url)}.cleaned.md")
    data = extract(fetch_html(url))
    words = len(data["body"].split())

    header = f"# {data['title'] or 'Untitled'}\n"
    if data["subtitle"]:
        header += f"\n_{data['subtitle']}_\n"
    meta_line = " · ".join(p for p in [data["author"], data["date"], url] if p)
    header += f"\n> Source: {meta_line}\n"
    out.write_text(f"{header}\n{data['body']}\n", encoding="utf-8")

    print(f"wrote: {out}")
    print(f"title: {data['title']}")
    print(f"author: {data['author']}  date: {data['date']}")
    print(f"words: {words}")
    print("--- preview (first 25 lines) ---")
    print("\n".join(data["body"].splitlines()[:25]))
    if words < 120:
        print(
            "\nNOTE: body is short — the post is likely paywalled or login-gated; "
            "summarize only the preview you have and flag that in the output."
        )


_SAMPLE = """<html><head><title>T</title>
<meta property="og:title" content="Test Post">
<meta name="author" content="Jane Doe">
<meta property="article:published_time" content="2026-01-02"></head>
<body><h1 class="post-title">Test Post</h1><h3 class="subtitle">A subtitle</h3>
<div class="available-content"><div class="body markup">
<p>Real paragraph one.</p><h2>Heading</h2><p>Real paragraph two.</p>
<div class="subscribe-widget"><button>Subscribe now</button></div>
</div></div></body></html>"""


def selftest() -> None:
    data = extract(_SAMPLE)
    assert data["title"] == "Test Post", data["title"]
    assert data["subtitle"] == "A subtitle", data["subtitle"]
    assert data["author"] == "Jane Doe", data["author"]
    assert data["date"] == "2026-01-02", data["date"]
    assert "Real paragraph one." in data["body"]
    assert "Real paragraph two." in data["body"]
    assert "## Heading" in data["body"], "markdown headings lost"
    assert "Subscribe now" not in data["body"], "junk widget not stripped"
    print("selftest OK")


if __name__ == "__main__":
    _args = sys.argv[1:]
    if _args and _args[0] == "--selftest":
        selftest()
    else:
        main(_args)
