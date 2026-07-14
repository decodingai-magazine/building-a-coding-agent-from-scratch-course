"""Pure mappings from the canonical event union to Rich renderables (append-style).

Output is append-style above a persistent input line — no self-rewriting live region — and
tool calls render on completion as a panel (ADR-0002 §6). These functions are pure (one
event in, one renderable out), which makes them exhaustively unit-testable;
:func:`render_event` raises loudly on an unknown event so a missed kind never renders nothing.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from decode.entities import events

# Subtle gray background distinguishing conversation lines (user echo + assistant stream) from
# tool panels/errors. A full-terminal background is impossible in append-style mode, and padding
# at print time would over-pad streamed deltas — so the background simply rides the text.
CONVERSATION_BG = Style(bgcolor="grey15")

# Context-gauge fill glyphs, empty → full; the index is ``round(fraction * 4)`` (ADR-0006 §9).
_GAUGE_GLYPHS = "○◔◑◕●"


def context_gauge(fraction: float, *, warn_at: float, danger_at: float) -> tuple[str, str]:
    """Map a context-window fill ``fraction`` to a ``(label, color)`` pair (ADR-0006 §9).

    Returns plain data (a label and a color name common to Rich and prompt_toolkit) so it stays
    toolkit-agnostic and unit-testable. ``fraction`` is clamped to ``[0.0, 1.0]``; the color
    tracks the two compaction tiers — red at/above ``danger_at``, yellow at/above ``warn_at``,
    else green — so the footer follows the actual cascade thresholds.
    """
    clamped = min(1.0, max(0.0, fraction))
    glyph = _GAUGE_GLYPHS[round(clamped * 4)]
    label = f"{glyph} {round(clamped * 100)}%"
    if clamped >= danger_at:
        color = "red"
    elif clamped >= warn_at:
        color = "yellow"
    else:
        color = "green"
    return label, color


# Braille frames for the "agent is working" footer spinner — indeterminate, one frame per tick.
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def spinner_frame(tick: int) -> str:
    """The braille spinner glyph for animation step ``tick`` — cycles and wraps (pure/testable)."""
    return _SPINNER_FRAMES[tick % len(_SPINNER_FRAMES)]


def render_event(event: events.Event) -> RenderableType:
    """Map a single canonical event to its Rich renderable.

    Raises ``TypeError`` for any event outside :data:`decode.entities.events.Event`, so an
    unhandled event kind surfaces loudly instead of silently rendering nothing.
    """
    if isinstance(event, events.AssistantTextDelta):
        return _render_assistant_delta(event)
    if isinstance(event, events.ThinkingDelta):
        return _render_thinking_delta(event)
    if isinstance(event, events.ToolCallStarted):
        return _render_tool_call_started(event)
    if isinstance(event, events.ToolResult):
        return _render_tool_result(event)
    if isinstance(event, events.PermissionRequested):
        return _render_permission_requested(event)
    if isinstance(event, events.AskUserRequested):
        return _render_ask_user_requested(event)
    if isinstance(event, events.TaskListUpdated):
        return _render_task_list_updated(event)
    if isinstance(event, events.TurnStarted):
        return _render_turn_started(event)
    if isinstance(event, events.TurnFinished):
        return _render_turn_finished(event)
    if isinstance(event, events.ContextCompacted):
        return _render_context_compacted(event)
    if isinstance(event, events.ContextMicrocompacted):
        return _render_context_microcompacted(event)
    if isinstance(event, events.AgentError):
        return _render_agent_error(event)
    raise TypeError(f"render_event got an unsupported event type: {type(event).__name__!r}")


def _render_assistant_delta(event: events.AssistantTextDelta) -> Text:
    """A chunk of the assistant's answer, appended above the prompt.

    The once-per-turn ``Decode `` prefix is added by the app's event sink, not here — the
    renderer stays pure and stateless.
    """
    return Text(event.text, style=CONVERSATION_BG)


def _render_thinking_delta(event: events.ThinkingDelta) -> Text:
    """Model reasoning, dimmed so it reads as secondary to the answer."""
    return Text(event.text, style="dim italic")


def _render_tool_call_started(event: events.ToolCallStarted) -> Text:
    """A one-line notice that a tool call started (full panel lands on the result).

    An Explore Subagent's call (Verbose Mode / Ctrl+O — ``child_index`` set) is indented under the
    parent's ``-> agent`` line and labelled ``[child N]`` with its 1-based prompt index, so it reads
    as a child's work and correlates with the ``## Subagent N`` section of the fold.
    """
    if event.child_index is None:
        return Text.assemble(("-> ", "dim cyan"), (event.name, "cyan"), (f" {event.args}", "dim"))
    return Text.assemble(
        ("  ", "dim"),
        (f"[child {event.child_index}] ", "dim magenta"),
        ("-> ", "dim cyan"),
        (event.name, "cyan"),
        (f" {event.args}", "dim"),
    )


def _render_tool_result(event: events.ToolResult) -> Panel:
    """A bordered panel summarizing a completed tool call (ADR-0002 §6: on completion)."""
    border = "green" if event.ok else "red"
    body = Text(event.output, style="default" if event.ok else "red")
    title = event.name if event.ok else f"{event.name} (failed)"
    return Panel(body, title=f"[bold]{title}[/bold]", title_align="left", border_style=border)


def _render_permission_requested(event: events.PermissionRequested) -> Text:
    """A prompt asking the user to approve a tool call (ADR-0002 §3)."""
    return Text.assemble(
        ("permission? ", "bold yellow"),
        (event.name, "yellow"),
        (f" {event.args}", "dim"),
    )


def _render_ask_user_requested(event: events.AskUserRequested) -> Text:
    """The agent's free-form question to the user (ADR-0002 §7)."""
    return Text.assemble(("ask: ", "bold magenta"), (event.question, "magenta"))


def _render_task_list_updated(event: events.TaskListUpdated) -> Panel:
    """The current in-memory task list (TodoWrite) as a small checklist panel.

    ``event.tasks`` arrive as already status-marked lines from ``todo_write``; rendered verbatim.
    """
    body = Text("\n".join(event.tasks) or "(no tasks)")
    return Panel(body, title="[bold]tasks[/bold]", title_align="left", border_style="blue")


def _render_turn_started(event: events.TurnStarted) -> Text:
    """The user's message, echoed as ``you "<message>"`` on the conversation background."""
    return Text.assemble(
        ("you ", "dim cyan"),
        (f'"{event.prompt}"', "default"),
        style=CONVERSATION_BG,
    )


def _render_turn_finished(event: events.TurnFinished) -> Text:
    """A dim marker that a turn ended; notes when it stopped early on abort."""
    if event.aborted:
        return Text("[aborted]", style="dim yellow")
    return Text("[done]", style="dim green")


def _render_context_compacted(event: events.ContextCompacted) -> Text:
    """A dim system line noting full compaction ran (ADR-0006 §4-6)."""
    return Text(
        f"Decode - compacted context (~{event.before_tokens} tokens → "
        f"summary + {event.kept_messages} recent messages).",
        style="dim",
    )


def _render_context_microcompacted(event: events.ContextMicrocompacted) -> Text:
    """A dim system line noting microcompaction blanked old tool outputs (ADR-0006 §3a)."""
    return Text(
        f"Decode - microcompacted context (elided {event.elided_count} old tool output(s), "
        f"~{event.before_tokens} tokens).",
        style="dim",
    )


def _render_agent_error(event: events.AgentError) -> Text:
    """A surfaced error; the turn ends but the REPL stays alive."""
    return Text.assemble(("error: ", "bold red"), (event.message, "red"))
