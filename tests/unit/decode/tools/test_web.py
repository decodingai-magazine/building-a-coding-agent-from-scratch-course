"""Unit tests for the gated ``web_fetch`` tool (``decode.tools.web``).

ADR-0002 §3,7,10: ``web_fetch`` does a single ``httpx.AsyncClient`` GET under
``settings.web_fetch_timeout_s``, follows redirects, converts an HTML response to Markdown
(``<script>`` / ``<style>`` removed) to cut tokens, passes text/plain through as-is, caps the
response size, and gates on approval (raises :class:`pydantic_ai.ApprovalRequired` until
approved). Anything the network can throw at it — a non-2xx status, a timeout, a connection
error, or non-text content — becomes a model-readable :class:`pydantic_ai.ModelRetry` instead
of crashing the REPL.

**No real network.** Every test stubs HTTP via :class:`httpx.MockTransport` (built into httpx —
no extra test dependency) patched onto the tool's transport seam, so the suite is hermetic under
``filterwarnings=["error"]``. The tool functions are driven directly with a hand-built
:class:`RunContext` (mirroring ``test_bash.py``), plus one run **through a real agent** with a
``FunctionModel`` that streams a real-URL ``web_fetch`` call and an approving resolver.
"""

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponseStreamEvent,
    RetryPromptPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import TurnContext
from decode.permissions.gate import PermissionGate
from decode.tools import web as web_module
from decode.tools.askuser import deny_user_question_resolver


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(
    cwd: Path,
    *,
    approved: bool = True,
    resolve: Callable[[PermissionRequest], Awaitable[PermissionDecision]] = _deny_resolver,
) -> RunContext[AgentDeps]:
    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=resolve,
        resolve_user_question=deny_user_question_resolver,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=approved)  # type: ignore[arg-type]


def _mock_transport(handler: Callable[[httpx.Request], httpx.Response], mocker) -> None:
    """Patch the tool's transport seam with a MockTransport running ``handler`` (no network)."""
    mocker.patch.object(web_module, "_TRANSPORT", httpx.MockTransport(handler))


def _html_response(html: str, *, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, headers={"content-type": "text/html; charset=utf-8"}, text=html
        )

    return handler


# --- gating: web_fetch defers to the gate (raises ApprovalRequired until the run is approved) -


async def test_web_fetch_requires_approval_when_not_approved(tmp_path: Path, mocker):
    handler = mocker.Mock(side_effect=AssertionError("must not hit the network unapproved"))
    _mock_transport(handler, mocker)

    with pytest.raises(ApprovalRequired):
        await web_module.web_fetch(_ctx(tmp_path, approved=False), url="https://example.com")
    # Gated BEFORE the request: an unapproved call never opens a connection.
    handler.assert_not_called()


# --- HTML -> Markdown conversion ------------------------------------------------------------


async def test_web_fetch_converts_html_to_markdown(tmp_path: Path, mocker):
    html = (
        "<html><body><h1>Title</h1>"
        '<p>Hello <a href="https://x.example/page">link</a></p></body></html>'
    )
    _mock_transport(_html_response(html), mocker)

    out = await web_module.web_fetch(_ctx(tmp_path), url="https://example.com")

    # Heading and link are preserved as Markdown (not raw HTML tags).
    assert "# Title" in out
    assert "[link](https://x.example/page)" in out
    assert "<h1>" not in out
    assert "<a " not in out


async def test_web_fetch_strips_script_and_style_content(tmp_path: Path, mocker):
    html = (
        "<html><head><style>.secret{color:red}</style></head>"
        "<body><h1>Visible</h1><script>var leaked = 42;</script></body></html>"
    )
    _mock_transport(_html_response(html), mocker)

    out = await web_module.web_fetch(_ctx(tmp_path), url="https://example.com")

    assert "Visible" in out
    # Script / style bodies must not leak into the Markdown handed to the model.
    assert "leaked" not in out
    assert "color:red" not in out
    assert "secret" not in out


async def test_web_fetch_reports_status_and_final_url(tmp_path: Path, mocker):
    _mock_transport(_html_response("<p>ok</p>"), mocker)

    out = await web_module.web_fetch(_ctx(tmp_path), url="https://example.com/start")

    # The result is model-readable: it states the status and the (final) URL it fetched.
    assert "200" in out
    assert "https://example.com/start" in out


async def test_web_fetch_follows_redirects_to_the_final_url(tmp_path: Path, mocker):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "https://example.com/final"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<h1>Arrived</h1>")

    _mock_transport(handler, mocker)

    out = await web_module.web_fetch(_ctx(tmp_path), url="https://example.com/start")

    assert "Arrived" in out
    # The reply reports the *final* URL after the redirect chain, not the requested one.
    assert "https://example.com/final" in out


# --- text/plain passthrough -----------------------------------------------------------------


async def test_web_fetch_passes_plain_text_through_unchanged(tmp_path: Path, mocker):
    body = "line one\nline two\n# not a heading, just text"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text=body)

    _mock_transport(handler, mocker)

    out = await web_module.web_fetch(_ctx(tmp_path), url="https://example.com/raw.txt")

    # text/plain is returned verbatim (no Markdown conversion of the body).
    assert body in out


async def test_web_fetch_treats_other_text_content_as_text(tmp_path: Path, mocker):
    body = '{"key": "value"}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, text=body)

    _mock_transport(handler, mocker)

    out = await web_module.web_fetch(_ctx(tmp_path), url="https://example.com/data.json")

    # Non-HTML textual content (e.g. JSON) is returned as-is, not Markdown-converted.
    assert body in out


# --- size cap -------------------------------------------------------------------------------


async def test_web_fetch_caps_oversized_responses(tmp_path: Path, mocker):
    # Force a tiny cap so a small body overflows; the head must be kept and a notice appended.
    mocker.patch.object(web_module, "_MAX_RESPONSE_BYTES", 64)
    body = "text/plain content " + ("A" * 500)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text=body)

    _mock_transport(handler, mocker)

    out = await web_module.web_fetch(_ctx(tmp_path), url="https://example.com/big.txt")

    assert "truncated" in out.lower()
    # The full body never reaches the model: the kept text is bounded near the cap.
    assert out.count("A") < 500


# --- deeply nested HTML: conversion failure becomes a model-readable ModelRetry -------------


def _deeply_nested_html(depth: int = 600) -> str:
    """A tiny page of ``depth`` nested ``<div>`` — attacker-controlled HTML that, fed to
    markdownify, recurses once per nesting level and blows Python's recursion limit. Built
    programmatically (a few KB, well under the byte cap) so the cap gives no protection.
    """
    return "<div>" * depth + "x" + "</div>" * depth


async def test_web_fetch_deeply_nested_html_returns_model_retry(tmp_path: Path, mocker):
    # ~600 nested <div> overflows markdownify's recursion; the tool must map the resulting
    # RecursionError to a model-readable ModelRetry, not let it escape and crash the turn.
    html = _deeply_nested_html()
    assert len(html.encode("utf-8")) < web_module._MAX_RESPONSE_BYTES  # cap gives no protection
    _mock_transport(_html_response(html), mocker)

    with pytest.raises(ModelRetry) as excinfo:
        await web_module.web_fetch(_ctx(tmp_path), url="https://example.com/nested")
    # The message names the page and points the model at the cause it can act on.
    assert "https://example.com/nested" in str(excinfo.value)
    assert "parse" in str(excinfo.value).lower()


async def test_web_fetch_unexpected_conversion_error_returns_model_retry(tmp_path: Path, mocker):
    # Any other unexpected conversion failure (not just RecursionError) must also map to a
    # ModelRetry rather than escape — the guard covers the whole conversion.
    mocker.patch.object(web_module, "markdownify", side_effect=ValueError("boom"), create=False)
    _mock_transport(_html_response("<h1>ok</h1>"), mocker)

    with pytest.raises(ModelRetry):
        await web_module.web_fetch(_ctx(tmp_path), url="https://example.com/bad-html")


# --- error mapping: every failure becomes a model-readable ModelRetry -----------------------


@pytest.mark.parametrize("status", [404, 500])
async def test_web_fetch_http_error_status_returns_model_retry(tmp_path: Path, mocker, status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"content-type": "text/html"}, text="<p>nope</p>")

    _mock_transport(handler, mocker)

    with pytest.raises(ModelRetry) as excinfo:
        await web_module.web_fetch(_ctx(tmp_path), url="https://example.com/missing")
    assert str(status) in str(excinfo.value)


async def test_web_fetch_timeout_returns_model_retry(tmp_path: Path, mocker):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    _mock_transport(handler, mocker)

    with pytest.raises(ModelRetry) as excinfo:
        await web_module.web_fetch(_ctx(tmp_path), url="https://slow.example")
    assert "time" in str(excinfo.value).lower()


async def test_web_fetch_connection_error_returns_model_retry(tmp_path: Path, mocker):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_transport(handler, mocker)

    with pytest.raises(ModelRetry):
        await web_module.web_fetch(_ctx(tmp_path), url="https://down.example")


async def test_web_fetch_non_text_content_type_returns_model_retry(tmp_path: Path, mocker):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=b"\x89PNG\r\n\x1a\n"
        )

    _mock_transport(handler, mocker)

    with pytest.raises(ModelRetry) as excinfo:
        await web_module.web_fetch(_ctx(tmp_path), url="https://example.com/logo.png")
    assert "image/png" in str(excinfo.value)


async def test_web_fetch_empty_url_returns_model_retry(tmp_path: Path, mocker):
    handler = mocker.Mock(side_effect=AssertionError("must not hit the network on empty url"))
    _mock_transport(handler, mocker)

    with pytest.raises(ModelRetry):
        await web_module.web_fetch(_ctx(tmp_path), url="   ")
    handler.assert_not_called()


@pytest.mark.parametrize("bad_url", ["a", "example.com", "file:///etc/passwd", "ftp://host/x"])
async def test_web_fetch_non_http_url_returns_model_retry(tmp_path: Path, mocker, bad_url: str):
    # A bare word, a schemeless host, or a non-http(s) scheme is a model mistake — caught up
    # front with a ModelRetry (and never sent to httpx, which mangles relative URLs internally).
    handler = mocker.Mock(side_effect=AssertionError("must not hit the network on a bad url"))
    _mock_transport(handler, mocker)

    with pytest.raises(ModelRetry):
        await web_module.web_fetch(_ctx(tmp_path), url=bad_url)
    handler.assert_not_called()


# --- timeout is the configured setting ------------------------------------------------------


async def test_web_fetch_uses_the_configured_timeout(tmp_path: Path, mocker):
    mocker.patch("decode.tools.web.settings.web_fetch_timeout_s", 12.5, create=False)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>ok</p>")

    _mock_transport(handler, mocker)

    await web_module.web_fetch(_ctx(tmp_path), url="https://example.com")

    # httpx records the per-request timeout in the request extensions; all phases use 12.5s.
    timeout = captured["timeout"]
    assert isinstance(timeout, dict)
    assert set(timeout.values()) == {12.5}


# --- through a real agent: forced web_fetch call, approved ----------------------------------


def _agent(mocker):
    """A real ``decode`` agent built with a dummy key (the model is overridden per test)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


def _already_fetched(messages: list[ModelMessage]) -> bool:
    """Whether a ``web_fetch`` tool return is already in the history (the resume leg)."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == "web_fetch"
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def _web_fetch_then_text() -> FunctionModel:
    """A FunctionModel that calls ``web_fetch`` with a real URL once, then returns final text.

    ``TestModel`` synthesises a *placeholder* (``"a"``) for a ``str`` argument, which is not a
    valid absolute URL — so we drive the exact call with a ``FunctionModel`` to pin a real
    ``https://`` URL and prove the gated fetch path end-to-end. The loop streams every model
    node, so a ``stream_function`` is supplied: the first leg streams the tool call, the resume
    leg (after the tool return is in history) streams the final text.
    """

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[ModelResponseStreamEvent]:
        if _already_fetched(messages):
            yield "done"
            return
        yield {
            0: DeltaToolCall(
                name="web_fetch", json_args='{"url": "https://example.com/page"}', tool_call_id="c1"
            )
        }

    return FunctionModel(stream_function=stream)


async def test_web_fetch_auto_allows_and_runs_through_the_agent(tmp_path: Path, mocker):
    """A forced ``web_fetch`` call is auto-allowed by the gate, then actually fetched.

    ``web_fetch`` is READ_ONLY (ADR-0003 §2): under DEFAULT mode the gate auto-allows it, so the
    call runs with **no** ``PermissionRequested`` event and **without** calling the human
    resolver. Proves the whole path end to end: the model calls ``web_fetch`` → it raises
    ``ApprovalRequired`` → the leg resolves to ``DeferredToolRequests`` → the gate auto-allows by
    mode x kind → ``web_fetch`` actually runs (against a MockTransport, no network) on the resume
    leg and its Markdown result is fed back to the model as a tool return.
    """
    _mock_transport(_html_response("<h1>From The Agent</h1>"), mocker)

    emitted: list[events.Event] = []
    resolver_calls: list[PermissionRequest] = []

    async def guard_resolver(request: PermissionRequest) -> PermissionDecision:
        resolver_calls.append(request)  # pragma: no cover - must never run on an auto-allow
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=tmp_path,
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=guard_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    agent = _agent(mocker)
    handler = AgentTurnHandler(agent, deps=deps)
    model = _web_fetch_then_text()

    async def _run() -> None:
        agen = handler(TurnContext(0, "fetch a page", emitted.append))
        with contextlib.suppress(StopAsyncIteration):
            await agen.asend(None)
            while True:
                await agen.asend([])
        await agen.aclose()

    with agent.override(model=model):
        await _run()

    # The read-only web_fetch call auto-allowed: no prompt was surfaced, the resolver never ran.
    perms = [e for e in emitted if isinstance(e, events.PermissionRequested)]
    assert not perms, "a read-only tool must auto-allow with no PermissionRequested"
    assert resolver_calls == [], "an auto-allowed call must not reach the human resolver"

    # web_fetch actually executed on the resume leg: its Markdown result reached the model as a
    # tool return — the deferred gate did not short-circuit execution.
    returns = [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "web_fetch"
    ]
    assert returns, "the web_fetch result must reach the model as a tool return"
    assert any("From The Agent" in r for r in returns)


def _web_fetch_then_settle_on_retry() -> FunctionModel:
    """A FunctionModel that calls ``web_fetch`` once, then — seeing the tool's ModelRetry come
    back as a ``RetryPromptPart`` — settles with final text instead of re-calling.

    When ``web_fetch`` raises a :class:`pydantic_ai.ModelRetry`, the loop feeds it to the model as
    a ``RetryPromptPart`` (not a ``ToolReturnPart``). A model that reacts to that retry proves the
    failure surfaced as something actionable — the turn does NOT crash with a RecursionError.
    """

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[ModelResponseStreamEvent]:
        if _web_fetch_retried(messages):
            yield "gave up on that page"
            return
        yield {
            0: DeltaToolCall(
                name="web_fetch", json_args='{"url": "https://example.com/page"}', tool_call_id="c1"
            )
        }

    return FunctionModel(stream_function=stream)


def _web_fetch_retried(messages: list[ModelMessage]) -> bool:
    """Whether a ``web_fetch`` ModelRetry (a ``RetryPromptPart``) is already in the history."""
    return any(
        isinstance(part, RetryPromptPart) and part.tool_name == "web_fetch"
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


async def test_web_fetch_deeply_nested_html_does_not_crash_the_turn(tmp_path: Path, mocker):
    """A deeply-nested HTML page fetched through the real agent loop must NOT crash the turn.

    The page overflows markdownify's recursion; instead of a RecursionError escaping and taking
    down the whole turn, the tool maps it to a ModelRetry. The loop feeds that back as a
    ``RetryPromptPart`` the model can act on, and the turn completes — no crash.
    """
    _mock_transport(_html_response(_deeply_nested_html()), mocker)

    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=tmp_path,
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=approving_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    agent = _agent(mocker)
    handler = AgentTurnHandler(agent, deps=deps)
    model = _web_fetch_then_settle_on_retry()

    async def _run() -> None:
        agen = handler(TurnContext(0, "fetch a nested page", emitted.append))
        with contextlib.suppress(StopAsyncIteration):
            await agen.asend(None)
            while True:
                await agen.asend([])
        await agen.aclose()

    # The turn must complete without a RecursionError escaping the loop.
    with agent.override(model=model):
        await _run()

    # The fetch surfaced a model-readable retry (not a crash): the recursion failure reached the
    # model as a RetryPromptPart naming the parse problem, so the model could pick a different page.
    retries = [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart) and part.tool_name == "web_fetch"
    ]
    assert retries, "the web_fetch ModelRetry must reach the model as a RetryPromptPart"
    assert any("parse" in r.lower() for r in retries)
