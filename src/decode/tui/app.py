"""The interactive REPL: a persistent input line + append-style Rich output.

ADR-0002 §6: input is a concurrent ``prompt_async()`` wrapped in ``patch_stdout()`` so any
output written while the user is typing scrolls *above* the prompt instead of corrupting
it (the prompt stays pinned). Output is append-style — no full-screen renderer — and tool
calls render on completion via :mod:`decode.tui.render`.

Task 002 has **no harness and no agent**: a typed line is echoed back. The ``Alt+Enter``
(follow-up) and ``Esc`` (abort) keybindings are registered now but only *record intent*
(an :class:`InputIntent`); task 003 wires that intent into the two-queue harness. ``Ctrl-D``
or typing ``/quit`` exits cleanly.

The interactive loop reads real stdin, so it is not unit-tested; the decidable pieces
(:func:`is_quit_command`, :func:`footer_hint`, :class:`InputIntent`) are pure and tested.
"""

from __future__ import annotations

import enum
import logging

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from decode.tui import render

logger = logging.getLogger(__name__)

_QUIT_COMMAND = "/quit"
_PROMPT = "> "


class InputIntent(enum.Enum):
    """What the user signalled with the line they just submitted.

    Task 002 only records this; task 003 routes ``STEER``/``FOLLOW_UP`` into the harness
    queues and ``ABORT`` into the cooperative-abort flag (ADR-0002 sections 4-5).
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

    For task 002 these only *record intent* on the running app, then accept/exit the
    prompt so the loop can observe it. ``Alt+Enter`` arrives as the ``escape, enter``
    sequence in prompt_toolkit.
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
    """Run the echo REPL until ``Ctrl-D`` or ``/quit``.

    ``console`` is injectable so callers/tests can capture output; defaults to a real
    stdout-backed :class:`rich.console.Console`.
    """
    console = console or Console()
    session: PromptSession[object] = PromptSession(
        key_bindings=_build_key_bindings(),
        bottom_toolbar=_bottom_toolbar,
    )

    console.print(
        render.render_event(render.MessageEvent(text="decode - type a line; /quit exits."))
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
                logger.debug("abort intent recorded (no harness until task 003)")
                console.print(render.render_event(render.MessageEvent(text="[abort]")))
                continue

            if not text.strip():
                continue

            # No agent yet: echo the line back. Follow-up intent is recorded only.
            logger.debug("input intent=%s text=%r", intent.value, text)
            console.print(render.render_event(render.EchoEvent(text=text)))

    console.print(render.render_event(render.MessageEvent(text="decode - bye.")))
