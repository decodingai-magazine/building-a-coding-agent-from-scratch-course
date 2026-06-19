"""The canonical event union the harness emits and the TUI renders.

ADR-0002 §4-6: the runner streams *events* to the TUI (it never writes to the terminal
itself). This module is the single contract for those events — one frozen dataclass per
event kind, joined into a discriminated union (:data:`Event`) keyed on the ``kind``
literal. The TUI's :func:`decode.tui.render.render_event` exhaustively matches on it.

Kept deliberately narrow: only the events M1 actually produces. New event kinds are added
here (and in the renderer) as later milestones need them. Each event is frozen + slotted
so it is cheap, hashable, and safe to pass across the queue/stream boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class TurnStarted:
    """A turn has acquired the single-flight lock and begun (ADR-0002 §4).

    ``prompt`` is the user text that opened the turn; ``turn_id`` is a monotonic id the
    TUI can use to group the events that follow.
    """

    turn_id: int
    prompt: str
    kind: Literal["turn_started"] = "turn_started"


@dataclass(frozen=True, slots=True)
class TurnFinished:
    """A turn reached the would-stop boundary with no pending follow-up (ADR-0002 §4).

    ``aborted`` is ``True`` when the turn stopped early at a boundary because ``Esc`` set
    the cooperative-abort flag (ADR-0002 §5); completed history is still kept.
    """

    turn_id: int
    aborted: bool = False
    kind: Literal["turn_finished"] = "turn_finished"


@dataclass(frozen=True, slots=True)
class AssistantTextDelta:
    """A chunk of assistant-visible answer text, streamed as the model produces it."""

    text: str
    kind: Literal["assistant_text_delta"] = "assistant_text_delta"


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    """A chunk of model reasoning/thinking text, streamed separately from the answer."""

    text: str
    kind: Literal["thinking_delta"] = "thinking_delta"


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """A tool call the model requested, surfaced before execution.

    ``args`` is the (already-serialized) argument summary; rendering happens on completion
    via :class:`ToolResult` to avoid flicker (ADR-0002 §6).
    """

    tool_call_id: str
    name: str
    args: str
    kind: Literal["tool_call_started"] = "tool_call_started"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The outcome of a completed tool call, correlated by ``tool_call_id``.

    ``ok`` is ``False`` when the tool failed or was denied; ``output`` carries the textual
    result (or the error/denial reason).
    """

    tool_call_id: str
    name: str
    output: str
    ok: bool = True
    kind: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True, slots=True)
class PermissionRequested:
    """The permission gate is asking the user to approve a tool call (ADR-0002 §3).

    Surfaced at a deferred-tool boundary; the TUI prompts allow/deny. Wired to the real
    gate in task 005 — here it is part of the contract so the union is complete.
    """

    tool_call_id: str
    name: str
    args: str
    kind: Literal["permission_requested"] = "permission_requested"


@dataclass(frozen=True, slots=True)
class AskUserRequested:
    """The agent asked the user a free-form question via the AskUser tool (ADR-0002 §7).

    Surfaced at a deferred-tool boundary; the TUI collects the answer. Wired in task 011.
    """

    tool_call_id: str
    question: str
    kind: Literal["ask_user_requested"] = "ask_user_requested"


@dataclass(frozen=True, slots=True)
class TaskListUpdated:
    """The in-memory task list (TodoWrite) changed; the TUI redraws it (ADR-0002 §7).

    ``tasks`` is the full current list of short task descriptions. Wired in task 009.
    """

    tasks: tuple[str, ...] = field(default_factory=tuple)
    kind: Literal["task_list_updated"] = "task_list_updated"


@dataclass(frozen=True, slots=True)
class AgentError:
    """An error the harness surfaces to the user instead of crashing the REPL.

    The turn ends; the REPL stays alive. ``message`` is already user-facing.
    """

    message: str
    kind: Literal["agent_error"] = "agent_error"


# The discriminated union the harness emits and the TUI renders. Discriminated on the
# ``kind`` literal so a structural match (and future serialization) is exhaustive.
Event = (
    TurnStarted
    | TurnFinished
    | AssistantTextDelta
    | ThinkingDelta
    | ToolCallStarted
    | ToolResult
    | PermissionRequested
    | AskUserRequested
    | TaskListUpdated
    | AgentError
)
