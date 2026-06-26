"""Pure mappings from the canonical event union to Rich renderables (append-style).

ADR-0002 §6: output is **append-style** above a persistent input line -- no self-rewriting
live region -- and **tool calls render on completion** (as a panel) to avoid flicker.

The events come from :mod:`decode.entities.events` (the single contract the harness emits).
These functions are pure (no I/O, no global state): they take one event and return a Rich
renderable, nothing more. That purity is what makes them exhaustively unit-testable while
the interactive loop in :mod:`decode.tui.app` is not. :func:`render_event` matches the
whole union and raises loudly on an unknown event so a missed kind never renders nothing.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from decode.entities import events

# A subtle gray background applied to the conversation lines — the user (`you "…"`) line and the
# assistant (`Decode …`) stream — so the conversation text reads as visually distinct from the
# tool panels / errors (Fix 2). Kept deliberately soft (``grey15``); a true full-terminal
# background is not possible in append-style mode (ADR-0002 §6), so we style the renderables and
# pad them to the console width (:meth:`Text.pad_right` at print time would over-pad streamed
# deltas, so the background simply rides the text plus its leading label).
CONVERSATION_BG = Style(bgcolor="grey15")


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

    Carries the subtle gray conversation background (Fix 2). The ``Decode `` prefix is added
    once per turn by the app's event sink (before the first delta), not here — these functions
    stay pure and stateless, so a per-turn prefix cannot live in the renderer.
    """
    return Text(event.text, style=CONVERSATION_BG)


def _render_thinking_delta(event: events.ThinkingDelta) -> Text:
    """Model reasoning, dimmed so it reads as secondary to the answer."""
    return Text(event.text, style="dim italic")


def _render_tool_call_started(event: events.ToolCallStarted) -> Text:
    """A one-line notice that a tool call started (full panel lands on the result)."""
    return Text.assemble(("-> ", "dim cyan"), (event.name, "cyan"), (f" {event.args}", "dim"))


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

    ``event.tasks`` are already status-marked lines (``[x]`` done, ``[~]`` in progress,
    ``[ ]`` pending) produced by the ``todo_write`` tool, so the renderer shows them verbatim
    (one per line) and only supplies a placeholder when the list is empty.
    """
    body = Text("\n".join(event.tasks) or "(no tasks)")
    return Panel(body, title="[bold]tasks[/bold]", title_align="left", border_style="blue")


def _render_turn_started(event: events.TurnStarted) -> Text:
    """The user's message, echoed as ``you "<message>"`` on a subtle gray background (Fix 2).

    The prompt is double-quoted so the user's text reads as a quoted utterance, and the whole
    line carries the conversation background that distinguishes user/assistant lines from the
    tool panels / errors (ADR-0002 §6 — append-style, so we style the renderable).
    """
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
