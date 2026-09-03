"""The REPL's Kitaru Recording Seam wiring in ``run_app`` (ADR-0019 §3, task 135).

Same seam as the headless runner, second caller — so a real REPL conversation feeds the replay
corpus. Everything is driven through the REAL ``run_app`` on a piped prompt_toolkit input, with the
whole Kitaru stack faked at the ``sys.modules`` boundary (``support.kitaru_recording``): no
workspace, no credentials, no network. Three properties carry the design:

* **Unconfigured is today.** No agent id → the handler drives the very object ``build_agent()``
  returned, no kitaru module is touched, and not one character of new output appears.
* **Configured records under the decode session id.** ``session_name`` is ``session_log.session_id``
  — the id that already names the JSONL log, the Opik thread and the Hand-back Session Branch — so a
  multi-turn conversation groups under one name, and the turn really runs through the wrapper's
  ``iter()`` (the REPL surface, not ``run()``).
* **A dead workspace costs one line, not the REPL.** The degrade notice rides the SAME event/emit
  surface as the Opik startup line (never raw stderr, which fights prompt_toolkit's redraw), appears
  exactly once for the whole session, and every turn still answers on the bare agent.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from rich.console import Console
from support.kitaru_recording import install_fake_recording_stack

import decode.runtime.recording as rec
from decode.agent.deps import AgentDeps
from decode.agent.loop import AgentTurnHandler
from decode.tui import app as app_mod

# A syntactically valid Kitaru agent id — the recording opt-in the seam parses (ADR-0019 §3).
AGENT_ID = "6f1d6b6a-6f6f-4c0a-9c9a-0f0f0f0f0f0f"
API_URL = "https://f5ee9622-kitaru.example.invalid"

# The one marker the scripted model streams back, so a test can prove a turn actually completed.
_REPLY = "TURN-ANSWERED"


@pytest.fixture(autouse=True)
def _sessions_dir(tmp_path, monkeypatch):
    """Redirect the JSONL session log under a per-test tmp dir so the repo's ``.decode`` is untouched."""
    monkeypatch.setattr(app_mod.settings, "sessions_dir", tmp_path / "sessions", raising=False)


@pytest.fixture
def _configured(monkeypatch):
    """A fully configured user-launched recording setup: agent id + the adapter's connection env."""
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", AGENT_ID)
    monkeypatch.setenv("KITARU_API_URL", API_URL)


@pytest.fixture
def _unconfigured(monkeypatch):
    """The default posture: no agent id, so the seam must hand the built agent straight back."""
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", "")
    monkeypatch.delenv("KITARU_API_URL", raising=False)


def _chat_agent() -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A minimal agent on a streaming ``FunctionModel`` (no network) that answers with one marker."""

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield _REPLY

    return Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )


def _spy_on_the_handler(monkeypatch) -> dict[str, object]:
    """Capture what ``run_app`` hands :class:`AgentTurnHandler` — the agent it ends up driving."""
    captured: dict[str, object] = {}
    real_cls = AgentTurnHandler

    def spy(agent: object, **kwargs: object) -> AgentTurnHandler:
        captured["agent"] = agent
        captured.update(kwargs)
        return real_cls(agent, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(app_mod, "AgentTurnHandler", spy)
    return captured


async def _drive_run_app(
    monkeypatch,
    agent: Agent[AgentDeps, str | DeferredToolRequests],
    script: Callable[[io.StringIO, Callable[[str], None]], Awaitable[None]],
) -> str:
    """Run the real ``run_app`` against piped input; return the captured console output."""
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


def _visible(text: str) -> str:
    """Collapse whitespace runs so a needle survives Rich's soft-wrapping at the console width."""
    return " ".join(text.split())


async def _wait_for(buf: io.StringIO, needle: str) -> None:
    """Poll the output buffer until ``needle`` appears (or the surrounding timeout fires)."""
    target = _visible(needle)
    while target not in _visible(buf.getvalue()):
        await asyncio.sleep(0.005)


async def _quit_immediately(buf: io.StringIO, send: Callable[[str], None]) -> None:
    # Startup lines and the banner all render before the prompt loop, so quitting at once is enough.
    await asyncio.sleep(0.05)
    send("/quit")


def _one_turn_then_quit(
    prompts: tuple[str, ...] = ("say something",),
) -> Callable[[io.StringIO, Callable[[str], None]], Awaitable[None]]:
    """A scripted user: send each prompt, wait for its answer, then ``/quit``."""

    async def script(buf: io.StringIO, send: Callable[[str], None]) -> None:
        for index, prompt in enumerate(prompts, start=1):
            send(prompt)
            while _visible(buf.getvalue()).count(_REPLY) < index:
                await asyncio.sleep(0.005)
        send("/quit")

    return script


# --- unconfigured: the REPL is exactly what it was ------------------------------------------------


async def test_an_unconfigured_repl_drives_the_bare_built_agent(monkeypatch, _unconfigured):
    """AC1: the handler gets the very object ``build_agent()`` returned — no wrapper in between."""
    agent = _chat_agent()
    captured = _spy_on_the_handler(monkeypatch)

    await _drive_run_app(monkeypatch, agent, _quit_immediately)

    assert captured["agent"] is agent


async def test_an_unconfigured_repl_touches_no_kitaru_module_and_prints_nothing_new(
    monkeypatch, _unconfigured
):
    """AC1: byte-identical to today — no adapter, no probe client, no extra output line."""
    stack = install_fake_recording_stack(monkeypatch)

    output = await _drive_run_app(monkeypatch, _chat_agent(), _quit_immediately)

    assert stack.wrapped == []
    assert stack.opened == 0
    assert "kitaru" not in output.lower()


# --- configured + reachable: wrapped, named by the decode session id ------------------------------


async def test_a_configured_repl_wraps_the_agent_with_the_session_log_id_as_session_name(
    monkeypatch, _configured
):
    """AC2: ``session_name`` is the decode session id, so REPL turns group under one Kitaru Session."""
    stack = install_fake_recording_stack(monkeypatch)
    captured = _spy_on_the_handler(monkeypatch)

    await _drive_run_app(monkeypatch, _chat_agent(), _quit_immediately)

    assert len(stack.wrapped) == 1
    session_log = captured["session_log"]
    assert stack.wrapped[0].session_name == session_log.session_id  # type: ignore[union-attr]
    assert stack.wrapped[0].agent_id == UUID(AGENT_ID)


async def test_a_configured_repl_drives_its_turns_through_the_kitaru_wrapper(
    monkeypatch, _configured
):
    """AC2: the handler drives the WRAPPER's ``iter()`` — the REPL surface — not the bare agent."""
    stack = install_fake_recording_stack(monkeypatch)
    captured = _spy_on_the_handler(monkeypatch)
    agent = _chat_agent()

    output = await _drive_run_app(monkeypatch, agent, _one_turn_then_quit())

    assert captured["agent"] is stack.wrapped[0]
    assert stack.wrapped[0].wrapped is agent
    assert stack.wrapped[0].iters == ["say something"]
    assert _REPLY in output  # ...and the answer still comes back through the wrapper


async def test_a_multi_turn_conversation_records_under_one_session_name(monkeypatch, _configured):
    """One wrap per session, not per turn: both turns ride the same Kitaru Session name."""
    stack = install_fake_recording_stack(monkeypatch)

    await _drive_run_app(monkeypatch, _chat_agent(), _one_turn_then_quit(("first", "second")))

    assert len(stack.wrapped) == 1
    assert stack.wrapped[0].iters == ["first", "second"]


async def test_a_working_recording_adds_no_line_to_the_repl(monkeypatch, _configured):
    """Only a LOSS is news: a healthy recording is silent, so the startup output is unchanged."""
    install_fake_recording_stack(monkeypatch)

    output = await _drive_run_app(monkeypatch, _chat_agent(), _quit_immediately)

    assert "kitaru" not in output.lower()


# --- configured + unreachable: ONE line, and the REPL keeps working -------------------------------


async def test_an_unreachable_workspace_costs_exactly_one_line_in_the_repl(
    monkeypatch, _configured
):
    """AC3: ONE degrade line for the whole session, on the TUI's own surface — never per turn."""
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("connection refused"))

    output = await _drive_run_app(
        monkeypatch, _chat_agent(), _one_turn_then_quit(("first", "second"))
    )

    assert _visible(output).count("not recording this run") == 1
    assert API_URL in _visible(output)


async def test_the_repl_still_answers_every_turn_after_a_degrade(monkeypatch, _configured):
    """AC3: recording is an observer — a dead workspace must not cost a single turn."""
    stack = install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("refused"))
    captured = _spy_on_the_handler(monkeypatch)
    agent = _chat_agent()

    output = await _drive_run_app(monkeypatch, agent, _one_turn_then_quit(("first", "second")))

    assert captured["agent"] is agent  # the bare agent, not a half-wrapped one
    assert stack.wrapped == []
    assert _visible(output).count(_REPLY) == 2
    assert "Decode - bye." in _visible(output)  # ...and the REPL shut down cleanly


async def test_the_degrade_line_rides_the_tui_event_surface_not_stderr(
    monkeypatch, _configured, capsys
):
    """The notice must render through the console the TUI owns; a raw stderr write fights prompt_toolkit."""
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("connection refused"))

    output = await _drive_run_app(monkeypatch, _chat_agent(), _quit_immediately)

    assert "not recording this run" in _visible(output)
    assert "not recording this run" not in capsys.readouterr().err
