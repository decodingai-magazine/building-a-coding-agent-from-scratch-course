"""End-to-end regression tests for the real ``run_app`` permission flow (ADR-0002 §3-4).

These close the gap that let the concurrent-prompt deadlock ship: every other test uses a
stub/headless resolver against an idle session, so the collision between the resolver's input
and the main loop's in-flight ``prompt_async()`` never occurs. Here we drive the **real**
``run_app`` — real main input loop, real :func:`decode.tui.app._make_permission_resolver`,
real :class:`~decode.harness.decisions.DecisionChannel`, real renderer — through a gated tool
with a programmatically-driven prompt_toolkit input (``create_pipe_input`` + ``DummyOutput``
inside a ``create_app_session``). A typed ``y`` approves and the turn resumes to completion;
a typed ``n`` denies (the tool body never runs, the denial is fed back to the model). If the
resolver opened a second ``prompt_async()`` on the live session this would deadlock and the
test would time out — which is exactly the regression we are guarding.

No network: the agent is built directly on a streaming ``FunctionModel`` (no Gemini), so the
gated ``noop`` tool drives a real ``DeferredToolRequests`` pause/resume.
"""

import asyncio
import io
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from rich.console import Console

from decode.agent.deps import AgentDeps
from decode.tools.noop import register_noop
from decode.tui import app as app_mod

# Marker the resume leg's text carries so the test can prove the turn resumed after approval.
_FINAL_TEXT = "FINAL-ANSWER-AFTER-APPROVE"
# Marker the model's second leg carries on a denial, proving the turn resumed after the deny.
_AFTER_DENY_TEXT = "OK-UNDERSTOOD-DENIED"


def _build_gated_agent(
    *, final_text: str, captured: list[list[ModelMessage]]
) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A real agent on a streaming ``FunctionModel`` that calls ``noop`` then returns text.

    First model request streams a ``noop`` tool call (``noop`` raises ``ApprovalRequired``
    until approved, so the leg resolves to ``DeferredToolRequests``); every later request
    (the resume leg) streams ``final_text``. Each leg's incoming messages are recorded into
    ``captured`` so a test can inspect what the model saw on the resume leg.
    """
    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        captured.append(list(messages))
        if state["calls"] == 1:
            yield {0: DeltaToolCall(name="noop", json_args='{"text": "secret-payload"}')}
        else:
            yield final_text

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )
    register_noop(agent)
    return agent


async def _drive_run_app(
    monkeypatch: pytest.MonkeyPatch,
    agent: Agent[AgentDeps, str | DeferredToolRequests],
    *,
    script: Callable[[io.StringIO, Callable[[str], None]], Awaitable[None]],
) -> str:
    """Run the real ``run_app`` against a piped prompt_toolkit input; return captured output.

    ``script`` is the user: it is handed the live output buffer and a ``send(line)`` callable
    and drives the conversation reactively (waiting for what it sees before typing the next
    line), exactly like a human at the terminal. The whole thing runs under a hard timeout so
    a regression of the concurrent-prompt deadlock fails fast instead of hanging the suite.
    """
    monkeypatch.setattr(app_mod, "build_agent", lambda: agent)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):

        def send(line: str) -> None:
            pipe.send_text(f"{line}\r")

        app_task = asyncio.ensure_future(app_mod.run_app(console=console))
        try:
            await asyncio.wait_for(script(buf, send), timeout=5.0)
            await asyncio.wait_for(app_task, timeout=5.0)
        finally:
            if not app_task.done():
                app_task.cancel()
    return buf.getvalue()


async def _wait_for(buf: io.StringIO, needle: str) -> None:
    """Poll the output buffer until ``needle`` appears (or the surrounding timeout fires)."""
    while needle not in buf.getvalue():
        await asyncio.sleep(0.005)


async def test_run_app_approves_a_gated_tool_and_resumes_to_completion(monkeypatch):
    """A typed ``y`` approves the gated call; the turn resumes and finishes with text.

    This is the headline capability of task 005, driven through the *only* production wiring.
    The resolver awaits the decision channel (not a second ``prompt_async()``), so the approval
    actually lands and the turn completes — no deadlock.
    """
    captured: list[list[ModelMessage]] = []
    agent = _build_gated_agent(final_text=_FINAL_TEXT, captured=captured)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("please run noop")
        # The permission prompt must surface and the resolver must be awaiting a decision.
        await _wait_for(buf, "allow this tool call?")
        send("y")
        await _wait_for(buf, _FINAL_TEXT)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert _FINAL_TEXT in output  # the turn resumed and completed after approval
    assert "permission? noop" in output  # the request was surfaced
    # Single render path: the gated request is rendered exactly once (no resolver re-print).
    assert output.count("permission? noop") == 1


async def test_run_app_denies_a_gated_tool_and_feeds_the_denial_back(monkeypatch):
    """A typed ``n`` denies the gated call; the tool body never runs, the denial is fed back."""
    captured: list[list[ModelMessage]] = []
    agent = _build_gated_agent(final_text=_AFTER_DENY_TEXT, captured=captured)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("please run noop")
        await _wait_for(buf, "allow this tool call?")
        send("n")
        await _wait_for(buf, _AFTER_DENY_TEXT)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert _AFTER_DENY_TEXT in output  # the turn resumed after the denial
    # The resume leg saw a denial tool-result; the tool body did NOT run (no echo).
    resume_leg = captured[-1]
    returns = [
        str(part.content)
        for message in resume_leg
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert returns, "the denial must reach the model as a tool result"
    assert any("denied" in r.lower() for r in returns)
    assert not any("noop: secret-payload" in r for r in returns)
