"""Unit tests for the REPL's Opik tracing wiring in ``run_app`` (ADR-0014 §1,4-5, task 092).

Covers: ``init_tracing()`` called once BEFORE the agent is built, the single startup line when
tracing is active (never when inactive), and the session-log id passed to the turn handler as the
Opik thread id. Driven through the real ``run_app`` on piped prompt_toolkit input, quitting
immediately. No network: ``build_agent`` is stubbed to a ``FunctionModel`` agent and
``init_tracing`` is a mock; the rootdir ``_no_opik_tracing`` fixture blanks the key.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from rich.console import Console

from decode.agent.deps import AgentDeps
from decode.agent.loop import AgentTurnHandler
from decode.tui import app as app_mod

# The project is DERIVED — ``decode-<DECODE_ENV>`` unless explicitly set (ADR-0015 §8) — so the line
# is built from the singleton rather than a literal, which would go stale per environment.
_TRACING_LINE = f"Decode - Opik tracing on (project '{app_mod.settings.opik_project_name}')."


@pytest.fixture(autouse=True)
def _sessions_dir(tmp_path, monkeypatch):
    """Redirect the JSONL session log under a per-test tmp dir so the repo's ``.decode`` is untouched."""
    monkeypatch.setattr(app_mod.settings, "sessions_dir", tmp_path / "sessions", raising=False)


def _chat_agent() -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A minimal agent on a streaming ``FunctionModel`` (no network); never actually run here."""

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "ok"

    return Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )


async def _run_until_quit(
    script: Callable[[io.StringIO, Callable[[str], None]], Awaitable[None]],
) -> str:
    """Run the real ``run_app`` against piped input; return the captured console output."""
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


async def _quit_immediately(buf: io.StringIO, send: Callable[[str], None]) -> None:
    # The startup line + banner render before the prompt loop, so quitting at once is enough.
    await asyncio.sleep(0.05)
    send("/quit")


async def test_run_app_calls_init_tracing_once_before_building_the_agent(monkeypatch):
    order: list[str] = []
    agent = _chat_agent()

    def fake_build() -> Agent[AgentDeps, str | DeferredToolRequests]:
        order.append("build_agent")
        return agent

    def fake_init() -> bool:
        order.append("init_tracing")
        return False

    monkeypatch.setattr(app_mod, "build_agent", fake_build)
    monkeypatch.setattr("decode.observability.init_tracing", fake_init)

    await _run_until_quit(_quit_immediately)

    assert order.count("init_tracing") == 1, order
    assert order.index("init_tracing") < order.index("build_agent"), order


async def test_run_app_prints_the_tracing_line_once_when_active(monkeypatch):
    monkeypatch.setattr(app_mod, "build_agent", _chat_agent)
    monkeypatch.setattr("decode.observability.init_tracing", lambda: True)

    output = await _run_until_quit(_quit_immediately)

    assert _TRACING_LINE in output
    assert output.count("Opik tracing on") == 1, "the tracing line must appear exactly once"


async def test_run_app_prints_no_tracing_line_when_inactive(monkeypatch):
    monkeypatch.setattr(app_mod, "build_agent", _chat_agent)
    # No init_tracing patch: the autouse ``_no_opik_tracing`` fixture blanks the key, so the real
    # ``init_tracing`` no-ops and returns False.

    output = await _run_until_quit(_quit_immediately)

    assert "Opik tracing on" not in output


async def test_run_app_passes_the_session_log_id_to_the_turn_handler(monkeypatch):
    monkeypatch.setattr(app_mod, "build_agent", _chat_agent)
    captured: dict[str, object] = {}

    real_cls = AgentTurnHandler

    def spy(*args: object, **kwargs: object) -> AgentTurnHandler:
        captured.update(kwargs)
        return real_cls(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(app_mod, "AgentTurnHandler", spy)

    await _run_until_quit(_quit_immediately)

    assert "session_id" in captured, "run_app must pass session_id to the handler"
    session_log = captured["session_log"]
    assert captured["session_id"] == session_log.session_id  # type: ignore[union-attr]
