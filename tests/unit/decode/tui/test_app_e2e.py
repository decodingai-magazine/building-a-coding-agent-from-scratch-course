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
from pydantic_ai import Agent, ApprovalRequired, DeferredToolRequests, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from rich.console import Console
from support.noop_helper import register_noop

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.sandbox.executor import FileStat
from decode.tools import files
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
    **run_app_kwargs: object,
) -> str:
    """Run the real ``run_app`` against a piped prompt_toolkit input; return captured output.

    ``script`` is the user: it is handed the live output buffer and a ``send(line)`` callable
    and drives the conversation reactively (waiting for what it sees before typing the next
    line), exactly like a human at the terminal. The whole thing runs under a hard timeout so
    a regression of the concurrent-prompt deadlock fails fast instead of hanging the suite.
    ``run_app_kwargs`` are forwarded to ``run_app`` (e.g. ``mode="bypass"`` for the sandbox e2e).
    """
    monkeypatch.setattr(app_mod, "build_agent", lambda: agent)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):

        def send(line: str) -> None:
            pipe.send_text(f"{line}\r")

        app_task = asyncio.ensure_future(app_mod.run_app(console=console, **run_app_kwargs))  # type: ignore[arg-type]
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


async def test_run_app_shuts_down_lsp_servers_on_exit(monkeypatch):
    """On exit, ``run_app`` tears down any spawned Language Server (ADR-0007 §6, task 054).

    The lazy per-root ``ty`` child must not orphan: after the REPL loop ends, ``run_app`` calls the
    LSP Service shutdown entry (next to the memory write-back) so every spawned server is shut down.
    We patch the service seam so no real subprocess is touched, then assert it fired exactly once and
    the clean-exit line still rendered.
    """
    calls = {"count": 0}

    async def fake_shutdown() -> None:
        calls["count"] += 1

    monkeypatch.setattr(app_mod, "shutdown_lsp_servers", fake_shutdown)
    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("what can you do?")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert calls["count"] == 1, "LSP shutdown must run exactly once on the exit path"
    assert "Decode - bye." in output  # exit still completes cleanly


async def test_run_app_swallows_lsp_shutdown_failure_and_still_exits(monkeypatch):
    """A failing LSP shutdown is logged and swallowed — exit is never blocked (task 054).

    Mirrors ``extract_on_exit``'s "never raises, cannot block exit" contract: even when the teardown
    raises (a wedged server), ``run_app`` returns normally and still prints ``Decode - bye.``.
    """

    async def boom() -> None:
        raise RuntimeError("ty server is wedged")

    monkeypatch.setattr(app_mod, "shutdown_lsp_servers", boom)
    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("what can you do?")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert "Decode - bye." in output  # the failure did not block exit or mask the bye line


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
    # The startup banner names the active provider:model this session talks to.
    assert f"Decode - {settings.llm_provider}:" in output


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


# --- control surfaces: /agent, /mode, Shift+Tab cycle (ADR-0003 §9, task 022) -----------------

# Marker the gated ``write`` test tool echoes once it actually runs (proves the body executed —
# i.e. the call was auto-allowed, not just un-prompted).
_WROTE_TEXT = "WROTE-THE-FILE-OK"


def _build_write_agent(
    *, final_text: str, captured: list[list[ModelMessage]]
) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A real agent on a streaming ``FunctionModel`` that calls a gated ``write`` then returns text.

    The tool is named ``write`` so the loop classifies it as ``FILE_EDIT`` (the registry's kind
    map keys off the tool name): edit mode auto-allows it, plan mode denies it, default asks. It
    raises :class:`pydantic_ai.ApprovalRequired` until approved (mirroring the production gated
    tools), so the first leg defers to the gate; on the approved resume it echoes ``_WROTE_TEXT``.
    Every later request streams ``final_text``.
    """
    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        captured.append(list(messages))
        if state["calls"] == 1:
            yield {
                0: DeltaToolCall(name="write", json_args='{"path": "hello.txt", "content": "hi"}')
            }
        else:
            yield final_text

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )

    def write(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired
        return f"{_WROTE_TEXT}: {path}"

    agent.tool(write)
    return agent


async def _drive_run_app_with_keys(
    monkeypatch: pytest.MonkeyPatch,
    agent: Agent[AgentDeps, str | DeferredToolRequests],
    *,
    script: Callable[[io.StringIO, Callable[[str], None], Callable[[str], None]], Awaitable[None]],
) -> str:
    """Like :func:`_drive_run_app` but the script also gets a ``send_keys(seq)``.

    ``send_keys`` writes a raw key sequence with **no** trailing carriage return, so a test can
    deliver the Shift+Tab key (``\\x1b[Z`` → ``s-tab`` / ``Keys.BackTab``) the mode-cycle keybind
    listens for, exactly as a real terminal would.
    """
    monkeypatch.setattr(app_mod, "build_agent", lambda: agent)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):

        def send(line: str) -> None:
            pipe.send_text(f"{line}\r")

        def send_keys(seq: str) -> None:
            pipe.send_text(seq)

        app_task = asyncio.ensure_future(app_mod.run_app(console=console))
        try:
            await asyncio.wait_for(script(buf, send, send_keys), timeout=5.0)
            await asyncio.wait_for(app_task, timeout=5.0)
        finally:
            if not app_task.done():
                app_task.cancel()
    return buf.getvalue()


async def test_run_app_agent_slash_switches_and_rejections_stay_alive(monkeypatch):
    """``/agent <primary>`` switches + confirms; a subagent / unknown name are friendly inline errors.

    Driven through the real ``run_app`` (single input surface — no second ``prompt_async``): the
    slash command is parsed in the main loop before submit, switches to a primary and renders one
    confirmation; ``/agent explore`` (a subagent — ADR-0013 §3) and ``/agent nope`` (unknown) each
    render an inline error and the REPL keeps going (a later chat still works).
    """
    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("/agent plan")
        await _wait_for(buf, "agent: plan")
        send("/agent explore")  # a subagent — cannot be selected as the main agent
        await _wait_for(buf, "subagent")
        send("/agent nope")  # an unknown name
        await _wait_for(buf, "no such agent")
        send("still here?")  # the session survived both rejected commands
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert "agent: plan" in output  # the primary switch rendered a confirmation
    assert "subagent" in output  # /agent explore rejected: explore is a subagent
    assert "no such agent" in output  # the unknown name rendered a friendly inline error
    assert _CHAT_REPLY in output  # the REPL kept running after the errors


async def test_run_app_mode_slash_switches_and_an_unknown_mode_stays_alive(monkeypatch):
    """``/mode bypass`` switches + confirms; ``/mode nope`` is a friendly inline error."""
    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("/mode bypass")
        await _wait_for(buf, "mode: bypass")
        send("/mode nope")
        await _wait_for(buf, "unknown mode")
        send("still here?")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert "mode: bypass" in output
    assert "unknown mode" in output
    assert _CHAT_REPLY in output


async def test_run_app_mode_bypass_lets_a_mutating_tool_run_without_a_prompt(monkeypatch):
    """ADR-0003 §9: after ``/mode bypass`` the next mutating tool runs with no permission prompt.

    Bypass allows everything: the gated ``write`` is auto-allowed, so its body runs (echoing
    ``_WROTE_TEXT``) and the turn completes without any allow/deny affordance.
    """
    captured: list[list[ModelMessage]] = []
    agent = _build_write_agent(final_text=_FINAL_TEXT, captured=captured)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("/mode bypass")
        await _wait_for(buf, "mode: bypass")
        send("write the file please")
        await _wait_for(buf, _FINAL_TEXT)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert _WROTE_TEXT in output  # the write body ran (auto-allowed under bypass)
    assert _FINAL_TEXT in output
    assert "allow this tool call?" not in output  # no prompt
    assert "permission? write" not in output


async def test_run_app_shift_tab_cycles_through_all_four_modes(monkeypatch):
    """Shift+Tab cycles ``default → edit → plan → bypass → default``, rendering each new mode.

    Each ``\\x1b[Z`` (BackTab) press fires the mode-cycle keybind on the single input surface and
    renders a confirmation; four presses from the build agent's ``default`` walk the full ring and
    wrap back to ``default``.
    """
    agent = _build_chat_agent()

    async def script(
        buf: io.StringIO, send: Callable[[str], None], send_keys: Callable[[str], None]
    ) -> None:
        send_keys("\x1b[Z")
        await _wait_for(buf, "mode: edit")
        send_keys("\x1b[Z")
        await _wait_for(buf, "mode: plan")
        send_keys("\x1b[Z")
        await _wait_for(buf, "mode: bypass")
        send_keys("\x1b[Z")
        await _wait_for(buf, "mode: default")  # wrapped back round
        send("/quit")

    output = await _drive_run_app_with_keys(monkeypatch, agent, script=script)

    assert "mode: edit" in output
    assert "mode: plan" in output
    assert "mode: bypass" in output
    assert "mode: default" in output


async def test_run_app_shift_tab_to_edit_lets_a_write_run_without_a_prompt(monkeypatch):
    """Working-looks-like (ADR-0003 §9): Shift+Tab → edit, then a ``write`` runs with no prompt.

    The build agent starts in ``default`` (a write would ASK). One Shift+Tab flips the gate to
    ``edit``, where a ``FILE_EDIT`` tool auto-allows: the write body runs (echoing ``_WROTE_TEXT``)
    with no permission affordance and the turn completes.
    """
    captured: list[list[ModelMessage]] = []
    agent = _build_write_agent(final_text=_FINAL_TEXT, captured=captured)

    async def script(
        buf: io.StringIO, send: Callable[[str], None], send_keys: Callable[[str], None]
    ) -> None:
        send_keys("\x1b[Z")  # default -> edit
        await _wait_for(buf, "mode: edit")
        send("write the file please")
        await _wait_for(buf, _FINAL_TEXT)
        send("/quit")

    output = await _drive_run_app_with_keys(monkeypatch, agent, script=script)

    assert "mode: edit" in output  # Shift+Tab rendered the new mode
    assert _WROTE_TEXT in output  # the write body actually ran (auto-allowed)
    assert _FINAL_TEXT in output  # the turn completed
    assert "allow this tool call?" not in output  # no human prompt was shown
    assert "permission? write" not in output


def _build_capturing_chat_agent(
    captured: list[list[ModelMessage]],
) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A chat agent that records each leg's incoming messages, then replies with text.

    Lets a test inspect exactly what user prompt the model saw — used to prove a ``/<skill>``
    command submits the skill *body* (not the literal ``/commit``) as the turn input.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        captured.append(list(messages))
        yield _CHAT_REPLY

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )
    return agent


def _user_prompts(captured: list[list[ModelMessage]]) -> list[str]:
    """Flatten every ``UserPromptPart`` text the model saw across all captured legs."""
    return [
        str(part.content)
        for leg in captured
        for message in leg
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


async def test_run_app_skill_slash_injects_the_body_and_runs_a_turn(monkeypatch):
    """ADR-0004 §5, task 028: ``/commit`` submits the built-in skill *body* as the turn input.

    Driven through the real ``run_app`` (single input surface): the slash command is parsed in
    the main loop, resolved via ``load_skills(cwd)``, and the body — not the literal ``/commit`` —
    is what the model receives as the user prompt; a turn actually runs.
    """
    captured: list[list[ModelMessage]] = []
    agent = _build_capturing_chat_agent(captured)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("/commit")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert _CHAT_REPLY in output  # a turn actually ran off the slash command
    prompts = _user_prompts(captured)
    # The commit skill body (not the literal `/commit`) was submitted as the turn input.
    assert any("Conventional Commits" in p for p in prompts)
    assert not any(p.strip() == "/commit" for p in prompts)


async def test_run_app_skill_slash_appends_trailing_text(monkeypatch):
    """Trailing text after ``/commit`` is appended to the body and submitted with it."""
    captured: list[list[ModelMessage]] = []
    agent = _build_capturing_chat_agent(captured)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("/commit ship the parser fix")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    await _drive_run_app(monkeypatch, agent, script=script)

    prompts = _user_prompts(captured)
    assert any("Conventional Commits" in p and "ship the parser fix" in p for p in prompts)


async def test_run_app_unknown_slash_is_intercepted_and_runs_no_turn(monkeypatch):
    """Behavior change (task 028): an unknown ``/<x>`` emits the available-skills line, no turn.

    Previously a stray ``/foo`` fell through to ``runner.submit("/foo", …)`` and reached the
    model; now it is intercepted with a discovery line and no turn runs. The REPL stays alive (a
    later normal line still runs a turn).
    """
    captured: list[list[ModelMessage]] = []
    agent = _build_capturing_chat_agent(captured)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("/definitely-not-a-skill")
        await _wait_for(buf, "available skills")
        send("still here?")  # the session survived the unknown command
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert "available skills" in output  # the discovery line rendered
    prompts = _user_prompts(captured)
    assert not any("definitely-not-a-skill" in p for p in prompts)  # never reached the model
    assert any("still here?" in p for p in prompts)  # the REPL kept running


# --- the manual /compact command (ADR-0006 §7, task 045) --------------------------------------

# A huge, distinctive old turn that full compaction folds into the summary (so its marker must
# be ABSENT from the post-compaction history), plus a small recent turn kept verbatim.
_OLD_TURN_MARKER = "OLD-TURN-COMPACTED-AWAY " * 100
_RECENT_TURN_MARKER = "RECENT-TURN-KEPT-VERBATIM"
# The skeleton the patched summarizer returns; build_summary_message frames it as the head.
_COMPACT_SKELETON = "# Conversation summary\n\n## Goal\nE2E-COMPACTED-SUMMARY-MARKER\n"


def _seed_over_budget_session(sessions_dir: Path) -> None:
    """Write a prior session whose history is over the recent-tail budget (ADR-0006 §5).

    A huge old user turn (folded into the summary) followed by a small recent user turn (kept
    verbatim), so ``run_app(resume="latest")`` seeds the handler with a history that ``/compact``
    can actually compact to ``[summary, recent_turn]``.
    """
    from datetime import UTC, datetime
    from uuid import UUID

    from decode.context.session_log import SessionLog

    prior = SessionLog.create(
        sessions_dir,
        cwd=sessions_dir,
        now=datetime(2026, 6, 18, 8, 0, tzinfo=UTC),
        session_id=UUID("00000000-0000-0000-0000-0000000000dd"),
    )
    prior.append_turn(
        [
            ModelRequest(parts=[UserPromptPart(content=_OLD_TURN_MARKER)]),
            ModelRequest(parts=[UserPromptPart(content=_RECENT_TURN_MARKER)]),
        ]
    )


async def test_run_app_compact_while_idle_compacts_the_over_budget_history(
    monkeypatch, sessions_dir
):
    """Headline of task 045: typing ``/compact`` while idle forces a full compaction (ADR-0006 §7).

    Driven through the real ``run_app`` (single input surface): the seeded over-budget history is
    replaced with ``[summary, *tail]`` and a ``ContextCompacted`` line renders. No network — the
    summarizer is patched to a fixed skeleton, so ``handler.compact()`` makes no Gemini call. We
    then run a follow-up turn and assert what the model sees proves the new shape: the framed
    summary + the recent tail, with the huge old turn gone.
    """
    from decode.agent import loop as agent_loop

    async def fake_summarize(messages, *, model_or_settings):
        return _COMPACT_SKELETON

    monkeypatch.setattr(agent_loop, "summarize_for_compaction", fake_summarize)
    # A tiny recent-tail budget so the seeded huge old turn is "old" and the small recent turn is
    # the kept tail (split_tail != 0 → compact() actually fires).
    monkeypatch.setattr(agent_loop.settings, "compaction_keep_recent_tokens", 20)
    _seed_over_budget_session(sessions_dir)

    captured: list[list[ModelMessage]] = []
    agent = _build_capturing_chat_agent(captured)
    monkeypatch.setattr(app_mod, "build_agent", lambda: agent)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):

        def send(line: str) -> None:
            pipe.send_text(f"{line}\r")

        app_task = asyncio.ensure_future(app_mod.run_app(console=console, resume="latest"))

        async def script() -> None:
            send("/compact")
            await _wait_for(buf, "compacted context")
            send("trigger-a-follow-up-turn")
            await _wait_for(buf, _CHAT_REPLY)
            send("/quit")

        try:
            await asyncio.wait_for(script(), timeout=5.0)
            await asyncio.wait_for(app_task, timeout=5.0)
        finally:
            if not app_task.done():
                app_task.cancel()

    output = buf.getvalue()
    assert "compacted context" in output  # the ContextCompacted line rendered

    # The follow-up turn's leg proves the history became [summary, *tail]: the framed summary +
    # the recent tail are present; the huge old turn was folded away.
    prompts = _user_prompts(captured)
    flat = " ".join(prompts)
    assert "E2E-COMPACTED-SUMMARY-MARKER" in flat  # the summary head replaced the old turns
    assert "Summary of the earlier conversation" in flat  # framed as a summary, not an instruction
    assert _RECENT_TURN_MARKER in flat  # the recent tail was kept verbatim
    assert "OLD-TURN-COMPACTED-AWAY" not in flat  # the huge old turn is gone from the history
    assert any("trigger-a-follow-up-turn" in p for p in prompts)  # the follow-up turn ran


async def test_run_app_compact_with_nothing_to_compact_is_a_friendly_line(monkeypatch):
    """``/compact`` on a fresh (empty) session renders the friendly no-op line, REPL stays alive.

    A fresh ``run_app`` has an empty history, so ``handler.compact()`` returns ``False`` (no
    transcript → no summarizer call, no network) and the loop renders the friendly line instead of
    a compaction event. A later normal line still runs a turn — the REPL kept going.
    """
    captured: list[list[ModelMessage]] = []
    agent = _build_capturing_chat_agent(captured)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("/compact")
        await _wait_for(buf, "nothing to compact yet")
        send("still here?")  # the session survived the no-op command
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert "Decode - nothing to compact yet." in output  # the friendly no-op line rendered
    assert "compacted context" not in output  # no compaction event fired
    prompts = _user_prompts(captured)
    assert any("still here?" in p for p in prompts)  # the REPL kept running


async def test_run_app_mode_plan_denies_a_write_without_asking(monkeypatch):
    """Working-looks-like (ADR-0003 §9): ``/mode plan`` → a ``write`` is denied, never asked.

    ``/mode plan`` flips the gate to plan; a ``FILE_EDIT`` write is then DENIED outright (the model
    is told to present a plan and call ``exit_plan_mode``) — the tool body never runs and no human
    prompt appears.
    """
    captured: list[list[ModelMessage]] = []
    agent = _build_write_agent(final_text=_AFTER_DENY_TEXT, captured=captured)

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("/mode plan")
        await _wait_for(buf, "mode: plan")
        send("write the file please")
        await _wait_for(buf, _AFTER_DENY_TEXT)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert _AFTER_DENY_TEXT in output  # the turn resumed after the denial
    assert _WROTE_TEXT not in output  # the write body never ran (denied, not asked)
    assert "allow this tool call?" not in output  # plan denies without prompting

    # The denial reached the model as a tool result on the resume leg (plan-mode reason).
    resume_leg = captured[-1]
    returns = [
        str(part.content)
        for message in resume_leg
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert returns, "the denial must reach the model as a tool result"
    assert any("plan mode" in r.lower() for r in returns)


async def test_run_app_reaps_the_sandbox_executor_on_exit(monkeypatch):
    """On exit, ``run_app`` reaps the session's sandbox executor via ``close_executor`` (ADR-0011 §4).

    A fake executor with an ``aclose`` spy is injected at the ``bash`` seam; after ``/quit`` the real
    exit path must ``await`` its ``aclose`` (next to the LSP shutdown + memory write-back) and still
    print the clean-exit line — proving the sandbox teardown is wired into the interactive shutdown.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from decode.tools import bash as bash_mod

    aclose = AsyncMock()
    monkeypatch.setattr(bash_mod, "_EXECUTOR", SimpleNamespace(aclose=aclose))
    monkeypatch.setattr(bash_mod, "_executor_selected", True)
    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("what can you do?")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    aclose.assert_awaited_once()  # the sandbox executor was reaped on the exit path
    assert "Decode - bye." in output  # exit still completes cleanly


def _build_bash_agent(*, final_text: str) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A real agent on a streaming ``FunctionModel`` whose first leg calls the REAL ``bash`` tool.

    Mirrors :func:`_build_gated_agent`, but registers :func:`decode.tools.bash.bash` itself so the
    approved call routes through the live executor seam (``_get_executor``) — the interactive
    docker-mode path the sandbox e2e tests below exercise.
    """
    from decode.tools import bash as bash_mod

    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        if state["calls"] == 1:
            yield {0: DeltaToolCall(name="bash", json_args='{"command": "echo sandboxed"}')}
        else:
            yield final_text

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )
    agent.tool(bash_mod.bash)
    return agent


async def test_run_app_docker_mode_warms_the_sandbox_at_launch_and_routes_bash(monkeypatch):
    """docker mode: ``run_app`` warms the sandbox at launch, then an approved ``bash`` reuses it.

    Closes the untested interactive+docker gap: the REPL must (a) show the ``sandbox:docker``
    banner, (b) await the selected executor's ``start`` BEFORE any turn runs (the eager warm-up —
    the container is up from launch, not invisibly mid-first-turn), (c) route the approved ``bash``
    through the SAME warmed executor instance, and (d) reap it on ``/quit``. The executor is a fake
    at the ``select_executor`` seam — no real daemon.
    """
    from unittest.mock import AsyncMock

    from decode.tools import bash as bash_mod
    from decode.tools.exec import ExecResult

    monkeypatch.setattr(app_mod.settings, "sandbox_mode", "docker")
    start = AsyncMock()
    aclose = AsyncMock()
    ran: list[str] = []

    class _FakeSandboxExecutor:
        async def start(self, cwd: Path) -> None:
            await start(cwd)

        async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
            ran.append(command)
            return ExecResult("sandboxed-echo-out", "", 0, timed_out=False)

        async def aclose(self) -> None:
            await aclose()

    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: _FakeSandboxExecutor())
    bash_mod.reset_executor()
    agent = _build_bash_agent(final_text="BASH-TURN-DONE")

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        await _wait_for(buf, "sandbox:docker")  # the banner names the active sandbox
        assert start.await_count == 1  # warmed at launch, BEFORE any turn ran
        send("run echo for me")
        await _wait_for(buf, "allow this tool call?")
        send("y")
        await _wait_for(buf, "BASH-TURN-DONE")
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert "starting docker sandbox" in output  # the progress line printed before the warm await
    assert ran == ["echo sandboxed"]  # the approved bash routed through the warmed executor
    assert "sandboxed-echo-out" in output  # and its result rendered
    aclose.assert_awaited_once()  # /quit reaped the session sandbox


async def test_run_app_docker_mode_degrades_to_lazy_when_the_warm_up_fails(monkeypatch):
    """A failed warm-up renders one friendly line and the session still works (lazy fallback).

    The degrade path: ``start`` raising must NOT kill the launch or reset the executor memo — the
    banner still shows, the friendly retry line renders, and the first approved ``bash`` routes
    through the same memoized executor (whose ``run`` works), proving the lazy fallback is intact.
    """
    from decode.tools import bash as bash_mod
    from decode.tools.exec import ExecResult

    monkeypatch.setattr(app_mod.settings, "sandbox_mode", "docker")
    ran: list[str] = []

    class _FailingStartExecutor:
        async def start(self, cwd: Path) -> None:
            raise RuntimeError("image pull failed")

        async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
            ran.append(command)
            return ExecResult("late-but-fine", "", 0, timed_out=False)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: _FailingStartExecutor())
    bash_mod.reset_executor()
    agent = _build_bash_agent(final_text="TURN-STILL-WORKS")

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        await _wait_for(buf, "sandbox startup failed")
        await _wait_for(buf, "sandbox:docker")  # the banner still renders after the failure
        send("run echo for me")
        await _wait_for(buf, "allow this tool call?")
        send("y")
        await _wait_for(buf, "TURN-STILL-WORKS")
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert "will retry on the first bash command" in output  # the friendly degrade line
    assert ran == ["echo sandboxed"]  # the SAME memoized executor served the turn lazily


async def test_run_app_clear_wipes_the_history_and_summarizes_first(monkeypatch, sessions_dir):
    """``/clear`` mid-session: summarize-before-wipe, the next turn starts fresh, the marker persists.

    The full interactive wiring in one pass: (a) the PRE-clear history feeds the same memory
    write-back the quit path runs (captured — no network), (b) the confirmation line renders,
    (c) the next turn's model request carries NOTHING from before the clear (the model genuinely
    starts fresh), (d) the session log gained a ``clear`` marker so ``--resume`` replays to the
    post-clear state, and (e) the ``/quit`` write-back then sees ONLY the post-clear segment
    (the wiped turns are never double-summarized).
    """
    import json

    extract_calls: list[list[ModelMessage]] = []

    async def fake_extract(messages, cwd):
        extract_calls.append(list(messages))

    monkeypatch.setattr(app_mod, "extract_on_exit", fake_extract)

    captured: list[list[ModelMessage]] = []
    replies = iter(["FIRST-TURN-REPLY", "SECOND-TURN-REPLY"])

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        captured.append(list(messages))
        yield next(replies)

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("pre-clear-secret-prompt")
        await _wait_for(buf, "FIRST-TURN-REPLY")
        send("/clear")
        await _wait_for(buf, "conversation cleared")
        send("post-clear prompt")
        await _wait_for(buf, "SECOND-TURN-REPLY")
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert "Decode - conversation cleared." in output  # (b)
    # (c) the post-clear turn's model request carries nothing from before the clear.
    second_leg = captured[-1]
    texts = [str(part.content) for message in second_leg for part in getattr(message, "parts", [])]
    assert not any("pre-clear-secret-prompt" in t for t in texts)
    assert any("post-clear prompt" in t for t in texts)
    # (a)+(e) summarize-before-wipe: /clear saw the pre-clear segment; /quit only the post-clear one.
    assert len(extract_calls) == 2
    assert any("pre-clear-secret-prompt" in str(m) for m in extract_calls[0])
    assert not any("pre-clear-secret-prompt" in str(m) for m in extract_calls[1])
    # (d) the clear marker rode the append-only session log (resume honesty).
    session_file = next(sessions_dir.glob("*.jsonl"))
    types = [
        json.loads(line)["type"]
        for line in session_file.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert "clear" in types


async def test_run_app_clear_on_an_empty_session_is_a_friendly_line(monkeypatch):
    """``/clear`` with nothing said yet: one friendly line, no crash, the session stays usable."""
    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("/clear")
        await _wait_for(buf, "nothing to clear")
        send("what can you do?")
        await _wait_for(buf, _CHAT_REPLY)
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script)

    assert "Decode - nothing to clear yet." in output
    assert _CHAT_REPLY in output  # the REPL kept working after the no-op clear


# --- Harness-Home split end-to-end (ADR-0012 §6): sandbox writes vs harness artifacts -----------


class _WorkspaceBackend:
    """A minimal sandbox backend for the write path — pathlib ``stat`` / ``write_bytes`` on a host dir.

    Stands in for the docker bind-mount (no container) so an app-level ``write`` in a sandbox mode lands
    in the Workspace dir it is bound to, letting the test prove the tool scope really moved there.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    async def stat(self, rel: str) -> FileStat | None:
        path = self._root / rel
        try:
            st = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        return FileStat(path=rel, is_dir=path.is_dir(), size=st.st_size)

    async def write_bytes(self, rel: str, data: bytes) -> None:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _build_sandbox_write_agent(*, path: str, content: str, final_text: str):
    """A ``FunctionModel`` agent that streams one ``write`` tool call, then final text (real write tool)."""
    import json

    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        if state["calls"] == 1:
            yield {
                0: DeltaToolCall(
                    name="write", json_args=json.dumps({"path": path, "content": content})
                )
            }
        else:
            yield final_text

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )
    agent.tool(files.write)
    return agent


async def test_run_app_sandbox_write_lands_in_workspace_and_memory_at_harness_home(
    monkeypatch, tmp_path, sessions_dir
):
    """ADR-0012 §6 end-to-end: a sandbox-mode ``write`` lands in the Workspace while harness artifacts
    (the ``/quit`` MEMORY.md write-back, the session log) anchor at Harness Home (the launch cwd).

    Drives the REAL ``run_app`` in docker mode with a fake backend at the file-tool seam (no container),
    ``mode="bypass"`` so the ``write`` runs inline (no prompt). The proof is the split: the file the model
    writes appears INSIDE the Workspace and NOT at Harness Home, while the exit memory write-back and the
    session-log header both resolve to Harness Home.
    """
    from unittest.mock import AsyncMock

    monkeypatch.chdir(tmp_path)  # Harness Home = the launch cwd = tmp_path
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(app_mod.settings, "sandbox_mode", "docker")
    # deps.cwd resolves via ``workspace_dir`` (the path) and the clone via ``prepare_workspace`` — patch
    # both to the test Workspace so the split is exercised without a real ``.decode/sandbox`` (082 kwargs).
    monkeypatch.setattr("decode.sandbox.workspace.workspace_dir", lambda home: workspace)
    monkeypatch.setattr(
        "decode.sandbox.workspace.prepare_workspace",
        lambda home, *, repo=None, local=False: workspace,
    )
    monkeypatch.setattr(app_mod, "warm_executor", AsyncMock())  # no real container
    monkeypatch.setattr(app_mod, "close_executor", AsyncMock())
    monkeypatch.setattr(
        "decode.tools.files._active_backend", lambda cwd: _WorkspaceBackend(workspace)
    )
    extract_spy = AsyncMock()
    monkeypatch.setattr(
        app_mod, "extract_on_exit", extract_spy
    )  # spy the memory write-back's anchor

    agent = _build_sandbox_write_agent(
        path="out.txt", content="WORKSPACE-FILE", final_text="WROTE-IT"
    )

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        send("create out.txt")
        await _wait_for(buf, "WROTE-IT")
        send("/quit")

    await _drive_run_app(monkeypatch, agent, script=script, mode="bypass")

    # (a) the write landed INSIDE the Workspace — the tool scope moved there ...
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "WORKSPACE-FILE"
    # ... and NOT at Harness Home (the launch cwd).
    assert not (tmp_path / "out.txt").exists()
    # (b) the /quit memory write-back anchored at Harness Home, NOT the Workspace (ADR-0012 §6).
    extract_spy.assert_awaited_once()
    assert extract_spy.await_args.args[1] == tmp_path
    # (c) the session log is a harness artifact: its header records Harness Home (the launch cwd).
    import json

    session_file = next(sessions_dir.glob("*.jsonl"))
    header = json.loads(session_file.read_text(encoding="utf-8").splitlines()[0])
    assert header["cwd"] == str(tmp_path)


def _make_local_git_repo(path: Path) -> Path:
    """Create a local git repo at ``path`` with one committed file (hermetic --repo fixture)."""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ("init", "-q"),
        ("config", "user.email", "t@decode.local"),
        ("config", "user.name", "t"),
        ("config", "commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / "README.md").write_text("cloned-hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True, capture_output=True
    )
    return path


async def test_run_app_repo_clones_into_the_workspace_and_shows_progress(
    monkeypatch, tmp_path, sessions_dir
):
    """ADR-0012 §3 REPL wiring: ``run_app(repo=…)`` clones the repo into the Workspace + shows progress.

    Drives the REAL ``run_app`` in docker mode with a hermetic LOCAL git repo as ``--repo`` (no network,
    no daemon — ``warm_executor`` is stubbed). The proof: the "cloning" progress line renders, the
    "starting docker sandbox" line renders, and the repo actually lands as a REAL clone at the host
    ``.decode/sandbox`` (README present, a working ``.git``) — the substrate task 083 ships from.
    """
    from unittest.mock import AsyncMock

    from decode.sandbox.workspace import workspace_dir

    monkeypatch.chdir(tmp_path)  # Harness Home = the launch cwd = tmp_path
    source = _make_local_git_repo(tmp_path / "source")
    monkeypatch.setattr(app_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(app_mod, "warm_executor", AsyncMock())  # no real container
    monkeypatch.setattr(app_mod, "close_executor", AsyncMock())

    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        await _wait_for(buf, "cloning")
        await _wait_for(buf, "sandbox:docker")  # the banner names the active sandbox
        send("/quit")

    output = await _drive_run_app(monkeypatch, agent, script=script, repo=str(source), local=True)

    # The progress lines rendered (a repo clone is slow, so the user sees it happening) — the repo
    # path may soft-wrap under the console width, so assert the stable prefix + the starting line.
    assert "cloning" in output
    assert "starting docker sandbox" in output
    # ... and the repo actually cloned into the host-visible Workspace at .decode/sandbox.
    workspace = workspace_dir(tmp_path)
    assert (workspace / "README.md").read_text(encoding="utf-8") == "cloned-hello\n"
    assert (workspace / ".git").is_dir()


async def test_run_app_repo_clone_failure_degrades_to_empty_workspace(
    monkeypatch, tmp_path, sessions_dir
):
    """A bad ``--repo`` never crashes the launch — one friendly line, and an empty Workspace (§3)."""
    from unittest.mock import AsyncMock

    from decode.sandbox.workspace import workspace_dir

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(app_mod, "warm_executor", AsyncMock())
    monkeypatch.setattr(app_mod, "close_executor", AsyncMock())

    agent = _build_chat_agent()

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        await _wait_for(buf, "empty workspace")  # the friendly degrade line
        await _wait_for(buf, "sandbox:docker")  # ...and the session still starts
        send("/quit")

    output = await _drive_run_app(
        monkeypatch, agent, script=script, repo=str(tmp_path / "nope"), local=True
    )

    assert "could not clone" in output  # the friendly degrade line, not a traceback
    assert "empty workspace" in output
    workspace = workspace_dir(tmp_path)
    assert list(workspace.iterdir()) == []  # degraded to a valid empty scratch
