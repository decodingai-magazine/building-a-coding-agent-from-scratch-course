"""The gated ``web_fetch`` tool — GET a URL and hand the model readable text (ADR-0002 §7,10).

``web_fetch`` does a single HTTP **GET** with an :class:`httpx.AsyncClient` and returns the page
as model-readable text. When the response is **HTML it is converted to Markdown** (with
``<script>`` / ``<style>`` removed) — the same trick claude-code's WebFetch uses to cut tokens,
since the model rarely needs the tag soup. text/plain (and other ``text/*`` content, plus JSON)
is returned **as-is**. The reply states the HTTP status and the final URL so the model knows
exactly what it read.

**Async-for-IO.** A network round-trip is I/O, so the tool is ``async`` and uses
``httpx.AsyncClient`` (per the AGENTS.md async-for-IO rule). Redirects are followed
(``follow_redirects=True``) and the reply reports the *final* URL after the redirect chain.

**Timeout.** Every phase uses ``settings.web_fetch_timeout_s`` (passed straight to the client),
so a hung server cannot wedge a turn.

**Size cap.** The decoded response is capped at :data:`_MAX_RESPONSE_BYTES` *before* conversion:
a huge page can blow up both the context window and memory. Unlike file / bash output (capped by
:mod:`decode.tools.truncate`, which snaps to a *line* boundary), web content often has no useful
line structure (minified HTML, single-line text), so a line-snapped cap would not actually bound
it — we cap by **raw UTF-8 bytes** instead (truncated to a valid character boundary) and append a
notice. Capping the *source* text (not just the Markdown) means the conversion itself never has
to chew through an unbounded document.

**Gating (ADR-0002 §3).** v1 asks on *every* tool call, so the function raises
:class:`pydantic_ai.ApprovalRequired` until ``ctx.tool_call_approved`` is set — *before* any
connection is opened, so a denied call makes no network request at all. A GET has **no local
side effect** (network egress only), so M3 may auto-allow it later — but in v1 it is **still
asked**, exactly like the read-only file tools.

**Errors never crash the REPL.** A non-2xx status, a timeout, a connection error, or non-text
content all map to a model-readable :class:`pydantic_ai.ModelRetry` so the model can correct
itself (try another URL, accept that the page is unreadable) instead of taking down the loop.

The :data:`_TRANSPORT` module attribute is the test seam: it is ``None`` in production (the
client opens a real connection) and tests patch it with an :class:`httpx.MockTransport` so the
suite is hermetic — no real network under ``filterwarnings=["error"]``.
"""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup
from markdownify import ATX, markdownify
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings

logger = logging.getLogger(__name__)

WEB_FETCH_TOOL_NAME = "web_fetch"

# The largest decoded response body we hand onward, in **raw UTF-8 bytes**. A page above this is
# hard-truncated to the cap (at a valid character boundary) before conversion so neither the
# context window nor memory is blown by one giant document. A byte cap (not a line-snapped one
# like decode.tools.truncate) is used because web content often has no useful line structure.
# 2 MB of text is already far more than a model needs from a single fetch.
_MAX_RESPONSE_BYTES = 2_000_000

# HTML element tags whose *content* must never reach the model: scripts and stylesheets are pure
# noise (and JS could even be hostile to quote back). They are removed wholesale before Markdown
# conversion — markdownify's own ``strip`` only drops the tags, not their text bodies.
_DROP_TAGS = ("script", "style")

# Content types we treat as HTML (→ convert to Markdown). Everything else that is still textual
# is passed through verbatim; non-text content is refused with a ModelRetry.
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

# Textual (non-HTML) content types we pass through as-is. ``text/*`` covers text/plain, csv, etc.;
# the explicit extras cover the common structured-text payloads a fetch legitimately returns.
_TEXT_CONTENT_PREFIXES = ("text/",)
_TEXT_CONTENT_TYPES = ("application/json", "application/xml")

# The test seam: ``None`` in production (httpx opens a real connection). Tests patch this with an
# httpx.MockTransport so no real network call ever happens. M-later could swap a caching/proxy
# transport here without touching the tool.
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
    if not ctx.tool_call_approved:
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

    An empty string or a URL without an ``http``/``https`` scheme (a bare ``"a"``, a ``file://``
    path, a relative reference) is a model mistake, not a crash — and httpx mangles relative URLs
    deep inside redirect/cookie handling, so we catch it up front with a clear, model-facing
    message instead of letting an opaque parse error escape.
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
    """Perform the GET, mapping every transport/HTTP failure to a model-readable ModelRetry.

    A timeout, a connection failure, or a non-2xx status are all things the model can react to
    (pick a different URL, give up on this page) — never reasons to crash the loop — so each
    becomes a :class:`pydantic_ai.ModelRetry` with a short, model-facing explanation.
    """
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
    """Build the per-call :class:`httpx.AsyncClient` (timeout + redirects + the test seam).

    The timeout comes from ``settings.web_fetch_timeout_s`` and is applied to every phase;
    redirects are followed so the reply can report the final URL. ``_TRANSPORT`` is ``None`` in
    production (a real connection) and a :class:`httpx.MockTransport` in tests (no network).
    """
    return httpx.AsyncClient(
        timeout=settings.web_fetch_timeout_s,
        follow_redirects=True,
        transport=_TRANSPORT,  # type: ignore[arg-type]
    )


def _decode_body(response: httpx.Response) -> str:
    """Decode the response to text, refusing non-text content with a ModelRetry.

    HTML and other textual content types are decoded (httpx picks the charset from the headers).
    A non-text payload (an image, a binary download) is not something the model can read, so it
    is refused with a :class:`pydantic_ai.ModelRetry` naming the content type.
    """
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

    The body is hard-capped at :data:`_MAX_RESPONSE_BYTES` *first* so conversion never chews
    through an unbounded document; an HTML body is then converted to Markdown (``<script>`` /
    ``<style>`` removed), while any other textual body is returned as-is. When the body was
    truncated, a notice naming the cap is appended so the model knows the page was clipped.
    """
    capped, truncated = _cap(body)
    text = _html_to_markdown(capped, response.url) if _is_html(_content_type(response)) else capped
    if truncated:
        text += f"\n\n[response truncated to {_MAX_RESPONSE_BYTES} bytes; the page was clipped]"
    return text


def _cap(body: str) -> tuple[str, bool]:
    """Hard-cap ``body`` to :data:`_MAX_RESPONSE_BYTES` UTF-8 bytes; return ``(text, truncated)``.

    Web content has no useful line structure to preserve, so this is a plain byte cap (not the
    line-snapped :mod:`decode.tools.truncate`). The cut is backed off to the last valid UTF-8
    character boundary so a multi-byte character is never split. ``truncated`` is ``True`` only
    when bytes were actually dropped.
    """
    encoded = body.encode("utf-8")
    if len(encoded) <= _MAX_RESPONSE_BYTES:
        return body, False
    # Decode the capped prefix, ignoring a partial trailing character at the cut point.
    head = encoded[:_MAX_RESPONSE_BYTES].decode("utf-8", errors="ignore")
    return head, True


def _html_to_markdown(html: str, url: httpx.URL) -> str:
    """Convert ``html`` to Markdown, removing ``<script>`` / ``<style>`` content entirely.

    markdownify's own ``strip`` argument only drops the *tags*, leaving their text bodies in the
    output, so the script / style elements are decomposed with BeautifulSoup first (a hard
    dependency of markdownify — always present). ATX headings (``# Title``) keep the output
    compact and unambiguous; the result is whitespace-trimmed.

    **Hostile-input guard.** ``web_fetch`` ingests arbitrary remote HTML, and markdownify
    recurses once per element nesting level — a tiny page of deeply nested tags (a few hundred
    ``<div>``, far under the byte cap, so the cap gives no protection) overflows Python's
    recursion limit and raises :class:`RecursionError`. That, or any other unexpected conversion
    failure on malformed input, must **never** escape and crash the turn (the module's "errors
    never crash the REPL" contract), so the whole conversion is wrapped and mapped to a
    model-readable :class:`pydantic_ai.ModelRetry`. We catch :class:`RecursionError` at this
    boundary rather than bumping ``sys.setrecursionlimit`` (which only moves the cliff and risks
    a C-stack segfault). The BeautifulSoup ``decompose()`` loop is iterative and safe; the guard
    spans it anyway so the whole conversion is covered.
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
