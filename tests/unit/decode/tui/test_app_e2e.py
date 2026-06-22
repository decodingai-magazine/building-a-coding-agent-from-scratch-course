"""End-to-end regression tests for the real ``run_app`` mid-turn HITL flows (ADR-0002 §2-4,7).

These close the gap that let the concurrent-prompt deadlock ship: every other test uses a
stub/headless resolver against an idle session, so the collision between the resolver's input
and the main loop's in-flight ``prompt_async()`` never occurs. Here we drive the **real**
``run_app`` — real main input loop, real :func:`decode.tui.app._make_permission_resolver` /
:func:`decode.tui.app._make_user_question_resolver`, real
:class:`~decode.harness.decisions.DecisionChannel`, real renderer — through a mid-turn pause
with a programmatically-driven prompt_toolkit input (``create_pipe_input`` + ``DummyOutput``
inside a ``create_app_session``):

* a **gated tool** (task 005): a typed ``y`` approves and the turn resumes to completion; a
  typed ``n`` denies (the tool body never runs, the denial is fed back to the model);
* the **ask_user tool** (task 011): the model asks a question mid-turn, the question surfaces,
  and the next typed line *is* the free-text answer that becomes the tool result.

If a resolver opened a second ``prompt_async()`` on the live session, or if a permission and an
ask_user request could be pending at once, this would deadlock and the test would time out —
which is exactly the regression we are guarding.

No network: the agent is built directly on a streaming ``FunctionModel`` (no Gemini), so the
gated ``noop`` / blocking ``ask_user`` tools drive a real mid-turn pause/resume.
"""

import asyncio
import io
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from rich.console import Console
from support.noop_helper import register_noop

from decode.agent.deps import AgentDeps
from decode.tools.askuser import ask_user
from decode.tui import app as app_mod

# Marker the simple chat agent streams as its only reply (used by the memory write-back wiring).
_CHAT_REPLY = "WORK-DONE-THIS-SESSION"

# Marker the resume leg's text carries so the test can prove the turn resumed after approval.
_FINAL_TEXT = "FINAL-ANSWER-AFTER-APPROVE"
# Marker the model's second leg carries on a denial, proving the turn resumed after the deny.
_AFTER_DENY_TEXT = "OK-UNDERSTOOD-DENIED"
# The question the model asks and the line the scripted user types back as the free-text answer.
_ASK_USER_QUESTION = "which-environment-should-i-deploy-to"
_ASK_USER_ANSWER = "deploy-to-staging-please"
# Marker the resume leg carries after the answer, proving the answer reached the model.
_AFTER_ANSWER_TEXT = "ACK-ANSWER-RECEIVED"


@pytest.fixture(autouse=True)
def sessions_dir(tmp_path, monkeypatch):
    """Redirect the JSONL session log under a per-test tmp dir (ADR-0002 §9, task 014).

    ``run_app`` now opens a session log under ``settings.sessions_dir``; without this every e2e
    test would write into the repo's real ``.decode/sessions``. Returns the dir so the tests
    that assert on the persisted file can read it.
    """
    target = tmp_path / "sessions"
    monkeypatch.setattr(app_mod.settings, "sessions_dir", target, raising=False)
    return target


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


def _build_ask_user_agent(
    *, final_text: str, captured: list[list[ModelMessage]]
) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A real agent on a streaming ``FunctionModel`` that calls ``ask_user`` then returns text.

    First model request streams an ``ask_user`` tool call; ``ask_user`` is NOT gated — it blocks
    the run inside the tool body on the interactive ``resolve_user_question`` (the single decision
    channel), and the human's typed line becomes the tool result. Every later request (the resume
    leg) streams ``final_text``. Each leg's incoming messages are recorded into ``captured`` so a
    test can prove the typed answer reached the model.
    """
    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        captured.append(list(messages))
        if state["calls"] == 1:
            yield {
                0: DeltaToolCall(
                    name="ask_user", json_args=f'{{"question": "{_ASK_USER_QUESTION}"}}'
                )
            }
        else:
            yield final_text

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )
    agent.tool(ask_user)
    return agent


async def test_run_app_ask_user_surfaces_the_question_and_a_typed_line_answers_it(monkeypatch):
    """The headline regression for task 011: ask_user mid-turn through the *only* production wiring.

    The model calls ``ask_user`` mid-turn; the real ``run_app`` (real main input loop, real
    :func:`decode.tui.app._make_user_question_resolver`, real
    :class:`~decode.harness.decisions.DecisionChannel`) must surface the question and let the
    next typed line answer it — the raw line becomes the tool result and the turn resumes to
    completion. If the resolver opened a second ``prompt_async()`` on the live session this would
    deadlock and the test would time out, which is exactly the regression we are guarding. No
    network: the agent runs on a streaming ``FunctionModel``.
    """
    captured: list[list[ModelMessage]] = []
    agent = _build_ask_user_agent(final_text=_AFTER_ANSWER_TEXT, captured=captured)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("set up my deploy")
        # The question must surface and the resolver must be awaiting the typed answer.
        await _wait_for(buf, _ASK_USER_QUESTION)
        await _wait_for(buf, "type your answer")
        send(_ASK_USER_ANSWER)
        await _wait_for(buf, _AFTER_ANSWER_TEXT)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert _AFTER_ANSWER_TEXT in output  # the turn resumed and completed after the answer
    assert _ASK_USER_QUESTION in output  # the question was surfaced to the user
    # ask_user is NOT gated: no permission affordance is shown for it.
    assert "allow this tool call?" not in output

    # The typed line reached the model as the ask_user tool result on the resume leg.
    resume_leg = captured[-1]
    returns = [
        str(part.content)
        for message in resume_leg
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert any(_ASK_USER_ANSWER in r for r in returns), "the typed answer must reach the model"


def _build_chat_agent() -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A real agent on a streaming ``FunctionModel`` that just replies with text (no tools)."""

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        yield _CHAT_REPLY

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )
    return agent


async def test_run_app_runs_memory_write_back_on_exit_with_the_session_history(monkeypatch):
    """On exit, ``run_app`` hands the accumulated history + cwd to the memory write-back (§8).

    This is the headline wiring of task 013: after the REPL loop ends, ``extract_on_exit`` must
    fire with the conversation the handler accumulated (so next session's ``MEMORY.md`` reflects
    *this* session) and ``deps.cwd`` (the project root). We capture the call instead of letting
    the real summarizer run, so no network request is made.
    """
    captured: dict[str, object] = {}

    async def fake_extract(messages, cwd):
        captured["messages"] = messages
        captured["cwd"] = cwd

    monkeypatch.setattr(app_mod, "extract_on_exit", fake_extract)
    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("summarize my project for me")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    await _drive_run_app(monkeypatch, agent, script=script)

    # The write-back fired with the accumulated history (carrying this session's user prompt)
    # and the launch cwd (the project root).
    assert "messages" in captured, "extract_on_exit must run on the shutdown path"
    history = captured["messages"]
    assert isinstance(history, list) and history, "the accumulated history must be passed in"
    user_text = [
        str(part.content)
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert any("summarize my project for me" in t for t in user_text)
    assert captured["cwd"] == Path.cwd()


async def test_run_app_renders_you_quote_decode_prefix_and_capital_goodbye(monkeypatch):
    """Fix 2/4 through the real wiring: `you "…"`, one `Decode ` answer prefix, capital goodbye.

    Drives the real ``run_app`` (real ``_make_event_sink``, real renderer) with a one-turn chat
    and asserts the rendered conversation: the user line is double-quoted after ``you``, the
    streamed answer is prefixed with ``Decode `` exactly once for the turn, and the goodbye prose
    is capitalized.
    """
    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("hello world")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert 'you "hello world"' in output  # Fix 2: user message double-quoted after `you`
    # Fix 2: the answer carries the `Decode ` lead-in, immediately before the streamed reply.
    assert f"Decode {_CHAT_REPLY}" in output
    assert "Decode - bye." in output  # Fix 4: capitalized goodbye prose


async def test_run_app_persists_the_session_to_a_jsonl_log(monkeypatch, sessions_dir):
    """ADR-0002 §9: ``run_app`` opens a session log and persists the turn's messages.

    A fresh ``run_app`` opens a new ``.jsonl`` file under ``settings.sessions_dir``; after a
    turn finishes, replaying that file yields the conversation (the user prompt is in it). No
    network: the agent runs on a streaming ``FunctionModel``.
    """
    from decode.context import session_log

    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("persist-this-prompt")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    await _drive_run_app(monkeypatch, agent, script=script)

    files = list(sessions_dir.glob("*.jsonl"))
    assert len(files) == 1, "run_app must open exactly one session file"
    replayed = session_log.load(files[0])
    user_text = [
        str(part.content)
        for message in replayed
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert any("persist-this-prompt" in t for t in user_text)


async def test_run_app_resume_seeds_history_from_the_prior_session(monkeypatch, sessions_dir):
    """``run_app(resume="latest")`` replays the latest session into the new turn handler (§9).

    A prior session file in ``sessions_dir`` carries an earlier user turn; resuming seeds the
    handler's ``message_history`` with it, so the model on the next turn sees the replayed
    prefix. We assert the replayed prompt reaches the model on the resumed turn.
    """
    from datetime import UTC, datetime
    from uuid import UUID

    from decode.context.session_log import SessionLog
    from decode.tui import app as app_module

    prior = SessionLog.create(
        sessions_dir,
        cwd=sessions_dir,
        now=datetime(2026, 6, 19, 8, 0, tzinfo=UTC),
        session_id=UUID("00000000-0000-0000-0000-0000000000cc"),
    )
    prior.append_turn(
        [
            ModelRequest(parts=[UserPromptPart(content="EARLIER-RESUMED-TURN")]),
        ]
    )

    captured: list[list[ModelMessage]] = []

    async def stream_function(messages, info):
        captured.append(list(messages))
        yield _CHAT_REPLY

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )

    monkeypatch.setattr(app_module, "build_agent", lambda: agent)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("new-turn-after-resume")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):

        def send(line: str) -> None:
            pipe.send_text(f"{line}\r")

        app_task = asyncio.ensure_future(app_module.run_app(console=console, resume="latest"))
        try:
            await asyncio.wait_for(script(buf, send), timeout=5.0)
            await asyncio.wait_for(app_task, timeout=5.0)
        finally:
            if not app_task.done():
                app_task.cancel()

    # The model saw the replayed earlier turn on the resumed conversation.
    flat = " ".join(
        str(getattr(part, "content", ""))
        for messages in captured
        for message in messages
        for part in message.parts
    )
    assert "EARLIER-RESUMED-TURN" in flat
