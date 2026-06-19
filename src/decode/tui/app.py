"""The interactive REPL: a persistent input line + append-style Rich output.

ADR-0002 §6: input is a concurrent ``prompt_async()`` wrapped in ``patch_stdout()`` so any
output written while the user is typing scrolls *above* the prompt instead of corrupting
it (the prompt stays pinned). Output is append-style -- no full-screen renderer -- and the
harness streams :mod:`decode.entities.events` to :func:`decode.tui.render.render_event`.

Input routing (ADR-0002 §4-5), via :meth:`decode.harness.runner.Runner.submit`:

* idle ``Enter`` -> starts a new turn;
* plain ``Enter`` while busy -> **steering** (injected at the next model-request boundary);
* ``Alt+Enter`` -> **follow-up** (drained only at the would-stop boundary);
* ``Esc`` -> cooperative **abort** (the turn stops at the next boundary).

The harness keeps the turn off the input coroutine (it runs as its own task), so the REPL
stays responsive while a turn streams. ``Ctrl-D`` or typing ``/quit`` exits cleanly.

The interactive loop reads real stdin, so it is not unit-tested; the decidable pieces
(:func:`is_quit_command`, :func:`footer_hint`, :class:`InputIntent`) are pure and tested.
"""

from __future__ import annotations

import enum
import logging
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.harness.runner import Runner
from decode.tui import render

logger = logging.getLogger(__name__)

_QUIT_COMMAND = "/quit"
_PROMPT = "> "


class InputIntent(enum.Enum):
    """What the user signalled with the line they just submitted (ADR-0002 §4-5).

    ``STEER`` is plain ``Enter`` (start a turn when idle, steer when busy); ``FOLLOW_UP`` is
    ``Alt+Enter``; ``ABORT`` is ``Esc``. The runner maps ``STEER``/``FOLLOW_UP`` onto the two
    queues and ``ABORT`` onto the cooperative-abort flag.
    """

    STEER = "steer"
    FOLLOW_UP = "follow-up"
    ABORT = "abort"


def is_quit_command(line: str) -> bool:
    """True when ``line`` is the ``/quit`` command (ignoring surrounding whitespace)."""
    return line.strip() == _QUIT_COMMAND


def footer_hint() -> str:
    """The bottom-toolbar hint listing the interaction keys (plain text).

    Kept pure and string-returning so it is unit-testable; :func:`_bottom_toolbar` wraps it
    for prompt_toolkit. Mentions steer (plain Enter), follow-up (Alt+Enter), abort (Esc),
    and how to quit.
    """
    return "Enter steer | Alt+Enter follow-up | Esc abort | Ctrl-D or /quit to exit"


def _bottom_toolbar() -> HTML:
    """The footer hint as prompt_toolkit formatted text."""
    return HTML(f"<b>{footer_hint()}</b>")


def _build_key_bindings() -> KeyBindings:
    """Register the follow-up (Alt+Enter) and abort (Esc) keybindings.

    Each accepts the prompt with an explicit ``(intent, text)`` result so the loop can route
    it. ``Alt+Enter`` arrives as the ``escape, enter`` sequence in prompt_toolkit.
    """
    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _follow_up(event: KeyPressEvent) -> None:
        event.app.exit(result=(InputIntent.FOLLOW_UP, event.app.current_buffer.text))

    @bindings.add("escape")
    def _abort(event: KeyPressEvent) -> None:
        event.app.exit(result=(InputIntent.ABORT, event.app.current_buffer.text))

    return bindings


def _interpret(submitted: object) -> tuple[InputIntent, str]:
    """Normalize ``prompt_async`` results into an ``(intent, text)`` pair.

    A plain ``Enter`` returns the line as a ``str`` (intent ``STEER``); the keybindings
    return an explicit ``(intent, text)`` tuple.
    """
    if isinstance(submitted, tuple):
        intent, text = submitted
        return intent, text
    return InputIntent.STEER, str(submitted)


async def run_app(console: Console | None = None) -> None:
    """Run the REPL until ``Ctrl-D`` or ``/quit``, routing input into the harness.

    ``console`` is injectable so callers/tests can capture output; defaults to a real
    stdout-backed :class:`rich.console.Console`. The harness drives the real Pydantic AI
    chat loop (task 004) behind the turn-handler seam; tools land in task 005+.
    """
    console = console or Console()

    def _on_event(event: events.Event) -> None:
        # The harness streams events here; render append-style above the pinned prompt.
        console.print(render.render_event(event))

    # The agent loop is the turn handler now: build the Gemini agent and bind the event sink
    # so streamed deltas reach the renderer. One handler per session carries history (§1).
    agent = build_agent()
    deps = AgentDeps(cwd=Path.cwd(), emit=_on_event)
    runner = Runner(AgentTurnHandler(agent, deps=deps), on_event=_on_event)
    session: PromptSession[object] = PromptSession(
        key_bindings=_build_key_bindings(),
        bottom_toolbar=_bottom_toolbar,
    )

    console.print(
        render.render_event(events.AssistantTextDelta(text="decode - type a line; /quit exits."))
    )

    with patch_stdout(raw=True):
        while True:
            try:
                submitted = await session.prompt_async(_PROMPT)
            except EOFError:  # Ctrl-D
                break

            intent, text = _interpret(submitted)
            if is_quit_command(text):
                break

            if intent is InputIntent.ABORT:
                logger.debug("abort intent: setting cooperative-abort flag")
                runner.abort()
                continue

            if not text.strip():
                continue

            logger.debug(
                "submit intent=%s phase=%s text=%r", intent.value, runner.phase.value, text
            )
            await runner.submit(text, intent)

    await runner.wait_idle()
    console.print(render.render_event(events.AssistantTextDelta(text="decode - bye.")))
