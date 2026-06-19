"""Pure mappings from a minimal event contract to Rich renderables (append-style).

ADR-0002 §6: output is **append-style** above a persistent input line — no self-rewriting
live region — and **tool calls render on completion** (as a panel) to avoid flicker.

The full event union (``entities.events``) lands in task 003. This task defines only a
**minimal local contract** so the echo REPL has something concrete to render. Keep these
functions pure (no I/O, no global state): they take an event and return a Rich renderable,
nothing more. That purity is what makes them exhaustively unit-testable while the
interactive loop in ``app`` is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text


@dataclass(frozen=True, slots=True)
class EchoEvent:
    """A line the user typed, echoed straight back (the only behavior in task 002)."""

    text: str


@dataclass(frozen=True, slots=True)
class MessageEvent:
    """A chunk of agent-facing text. In task 002 it is only used for system notices."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """A completed tool call, rendered as a panel once the result is known.

    Rendered only on completion (never mid-execution) so the append-style output does not
    flicker; ``result`` is ``None`` when the tool produced no textual output.
    """

    name: str
    summary: str
    result: str | None = None


# The minimal local event union task 002 knows how to render. Task 003 replaces this with
# the canonical ``entities.events`` union.
Event = EchoEvent | MessageEvent | ToolCallEvent


def render_event(event: Event) -> RenderableType:
    """Map a single event to its Rich renderable.

    Raises ``TypeError`` for any event type outside the minimal contract, so an unhandled
    event surfaces loudly instead of silently rendering nothing.
    """
    if isinstance(event, EchoEvent):
        return _render_echo(event)
    if isinstance(event, MessageEvent):
        return _render_message(event)
    if isinstance(event, ToolCallEvent):
        return _render_tool_call(event)
    raise TypeError(f"render_event got an unsupported event type: {type(event).__name__!r}")


def _render_echo(event: EchoEvent) -> Text:
    """A dim echo line, distinct from agent output so the user can tell them apart."""
    return Text.assemble(("echo ", "dim cyan"), (event.text, "default"))


def _render_message(event: MessageEvent) -> Text:
    """Plain agent/system text appended above the prompt."""
    return Text(event.text)


def _render_tool_call(event: ToolCallEvent) -> Panel:
    """A bordered panel summarizing a completed tool call (name, args, result)."""
    body = Text()
    body.append(event.summary)
    if event.result is not None:
        body.append("\n")
        body.append(event.result, style="dim")
    return Panel(body, title=f"[bold]{event.name}[/bold]", title_align="left", expand=True)
