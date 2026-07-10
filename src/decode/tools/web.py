"""The gated ``web_fetch`` tool — GET a URL and hand the model readable text (ADR-0002 §7,10).

One async httpx GET (redirects followed; ``settings.web_fetch_timeout_s`` on every phase). HTML
is converted to Markdown (``<script>`` / ``<style>`` removed) to cut tokens; other textual
content passes through as-is. The decoded body is capped at :data:`_MAX_RESPONSE_BYTES`
**before** conversion — a raw UTF-8 byte cap (backed off to a valid character boundary), not a
line-snapped one, because web content often has no useful line structure. Gating raises
:class:`pydantic_ai.ApprovalRequired` *before* any connection is opened, so a denied call makes
no request. Every failure (non-2xx, timeout, connection error, non-text content) maps to a
model-readable :class:`pydantic_ai.ModelRetry` — errors never crash the REPL.
:data:`_TRANSPORT` is the test seam (``None`` in production; tests patch an
:class:`httpx.MockTransport` so the suite is hermetic).
"""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup
from markdownify import ATX, markdownify
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.tools.approval import needs_approval

logger = logging.getLogger(__name__)

WEB_FETCH_TOOL_NAME = "web_fetch"

# Cap on the decoded body in raw UTF-8 bytes (hard-truncated at a valid character boundary,
# before conversion); a byte cap, not a line-snapped one — web content has no useful line structure.
_MAX_RESPONSE_BYTES = 2_000_000

# Tags whose *content* must never reach the model — markdownify's own ``strip`` only drops the
# tags, not their text bodies, so these are removed wholesale before conversion.
_DROP_TAGS = ("script", "style")

# Content types treated as HTML (→ Markdown conversion).
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

# Textual (non-HTML) content types passed through verbatim; non-text content is refused.
_TEXT_CONTENT_PREFIXES = ("text/",)
_TEXT_CONTENT_TYPES = ("application/json", "application/xml")

# Test seam: ``None`` in production; tests patch an httpx.MockTransport (no real network).
_TRANSPORT: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None


async def web_fetch(ctx: RunContext[AgentDeps], url: str) -> str:
    """GET ``url`` and return its content as model-readable text (ADR-0002 §7).

    The page is fetched with an :class:`httpx.AsyncClient` under
    ``settings.web_fetch_timeout_s``, following redirects. An HTML response is converted to
    Markdown (``<script>`` / ``<style>`` removed) to cut tokens; text/plain and other textual
    content is returned as-is. An oversized body is hard-capped to :data:`_MAX_RESPONSE_BYTES`
    (with a clipped notice). The reply states the HTTP status and the final URL.

    Gated (ADR-0002 §3): raises :class:`pydantic_ai.ApprovalRequired` until the call is approved —
    and *before* any connection is opened — so a denied call makes no request. Returns a
    model-readable :class:`pydantic_ai.ModelRetry` for an empty URL, a non-2xx status, a timeout,
    a connection error, or non-text content, so the model can recover instead of crashing the REPL.
    """
    if needs_approval(ctx):
        logger.debug("web_fetch requires approval (url=%r)", url)
        raise ApprovalRequired

    target = url.strip()
    _validate_url(target)

    response = await _get(target)
    body = _decode_body(response)
    rendered = _render_body(response, body)
    logger.debug(
        "web_fetch ok (status=%d, final_url=%s, %d chars)",
        response.status_code,
        response.url,
        len(rendered),
    )
    return f"HTTP {response.status_code} {response.url}\n\n{rendered}"


def _validate_url(url: str) -> None:
    """Reject a non-http(s) URL with a ModelRetry *before* any connection is attempted.

    httpx mangles relative URLs deep inside redirect/cookie handling, so a model mistake is
    caught up front with a clear message instead of an opaque parse error.
    """
    if not url:
        raise ModelRetry("url is empty; provide an http(s) URL to fetch.")
    scheme = httpx.URL(url).scheme if _parseable(url) else ""
    if scheme not in ("http", "https"):
        raise ModelRetry(
            f"{url!r} is not an http(s) URL; provide an absolute http:// or https:// URL."
        )


def _parseable(url: str) -> bool:
    """Whether ``url`` parses as an :class:`httpx.URL` at all (a malformed URL does not)."""
    try:
        httpx.URL(url)
    except httpx.InvalidURL:
        return False
    return True


async def _get(url: str) -> httpx.Response:
    """Perform the GET, mapping every transport/HTTP failure to a model-readable ModelRetry."""
    try:
        async with _client() as client:
            response = await client.get(url)
            response.raise_for_status()
            return response
    except httpx.TimeoutException as exc:
        logger.debug("web_fetch timed out (url=%r): %s", url, exc)
        raise ModelRetry(
            f"Fetching {url} timed out after {settings.web_fetch_timeout_s:g}s."
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.debug("web_fetch got HTTP %d (url=%r)", status, url)
        raise ModelRetry(
            f"Fetching {url} returned HTTP {status}; the page is not retrievable."
        ) from exc
    except httpx.RequestError as exc:
        logger.debug("web_fetch connection error (url=%r): %s", url, exc)
        raise ModelRetry(f"Could not connect to {url}: {exc}.") from exc


def _client() -> httpx.AsyncClient:
    """Build the per-call :class:`httpx.AsyncClient` (settings timeout, redirects, the test seam)."""
    return httpx.AsyncClient(
        timeout=settings.web_fetch_timeout_s,
        follow_redirects=True,
        transport=_TRANSPORT,  # type: ignore[arg-type]
    )


def _decode_body(response: httpx.Response) -> str:
    """Decode the response to text, refusing non-text content with a ModelRetry naming the type."""
    content_type = _content_type(response)
    if not _is_textual(content_type):
        logger.debug("web_fetch refused non-text content (%s, url=%s)", content_type, response.url)
        raise ModelRetry(
            f"{response.url} returned non-text content ({content_type or 'unknown'}); "
            "web_fetch only reads text and HTML pages."
        )
    return response.text


def _render_body(response: httpx.Response, body: str) -> str:
    """Cap, then (for HTML) Markdown-convert the decoded body into the model-facing text.

    Capped at :data:`_MAX_RESPONSE_BYTES` *first* so conversion never chews through an unbounded
    document; a truncation notice is appended when the body was clipped.
    """
    capped, truncated = _cap(body)
    text = _html_to_markdown(capped, response.url) if _is_html(_content_type(response)) else capped
    if truncated:
        text += f"\n\n[response truncated to {_MAX_RESPONSE_BYTES} bytes; the page was clipped]"
    return text


def _cap(body: str) -> tuple[str, bool]:
    """Hard-cap ``body`` to :data:`_MAX_RESPONSE_BYTES` UTF-8 bytes; return ``(text, truncated)``.

    A plain byte cap (no line structure to preserve), backed off to the last valid UTF-8
    character boundary so a multi-byte character is never split.
    """
    encoded = body.encode("utf-8")
    if len(encoded) <= _MAX_RESPONSE_BYTES:
        return body, False
    # Decode the capped prefix, ignoring a partial trailing character at the cut point.
    head = encoded[:_MAX_RESPONSE_BYTES].decode("utf-8", errors="ignore")
    return head, True


def _html_to_markdown(html: str, url: httpx.URL) -> str:
    """Convert ``html`` to Markdown, removing ``<script>`` / ``<style>`` content entirely.

    The script / style elements are decomposed with BeautifulSoup first (markdownify's ``strip``
    would keep their text bodies); ATX headings keep the output compact.

    **Hostile-input guard.** markdownify recurses per nesting level, so a tiny page of deeply
    nested tags (far under the byte cap) raises :class:`RecursionError`. That — or any other
    conversion failure on malformed input — must never crash the turn, so the whole conversion
    maps to a model-readable :class:`pydantic_ai.ModelRetry`; catching at this boundary beats
    bumping ``sys.setrecursionlimit``, which only moves the cliff and risks a C-stack segfault.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(_DROP_TAGS):
            tag.decompose()
        return markdownify(str(soup), heading_style=ATX).strip()
    except RecursionError as exc:
        logger.debug("web_fetch hit recursion limit converting HTML (url=%s)", url)
        raise ModelRetry(
            f"Could not parse the HTML at {url} (too deeply nested); try a different page."
        ) from exc
    except Exception as exc:
        logger.debug("web_fetch failed converting HTML (url=%s): %s", url, exc)
        raise ModelRetry(
            f"Could not parse the HTML at {url} (malformed); try a different page."
        ) from exc


def _content_type(response: httpx.Response) -> str:
    """The lower-cased media type from the ``Content-Type`` header (parameters dropped)."""
    raw = response.headers.get("content-type", "")
    return raw.split(";", 1)[0].strip().lower()


def _is_html(content_type: str) -> bool:
    """Whether ``content_type`` denotes HTML (→ convert to Markdown)."""
    return content_type in _HTML_CONTENT_TYPES


def _is_textual(content_type: str) -> bool:
    """Whether ``content_type`` is something the model can read (HTML or any other text)."""
    if _is_html(content_type):
        return True
    if content_type in _TEXT_CONTENT_TYPES:
        return True
    return content_type.startswith(_TEXT_CONTENT_PREFIXES)
