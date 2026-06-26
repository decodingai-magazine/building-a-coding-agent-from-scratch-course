"""Unit tests for the pure render functions in ``decode.tui.render``.

These map the canonical :mod:`decode.entities.events` union to Rich renderables. They are
pure (no I/O, no global state) so they are exhaustively unit-testable.
"""

import pytest
from rich.panel import Panel
from rich.text import Text

from decode.config.settings import settings
from decode.entities import events
from decode.tui import render


def _render_to_text(renderable) -> str:
    """Render a Rich renderable to plain text the way a terminal would show it."""
    from rich.console import Console

    console = Console(width=80, file=None, record=True)
    console.print(renderable)
    return console.export_text()


def test_render_assistant_text_delta_includes_the_text():
    renderable = render.render_event(events.AssistantTextDelta(text="hello world"))

    assert "hello world" in _render_to_text(renderable)
    assert isinstance(renderable, Text)


def test_render_assistant_text_delta_has_a_subtle_gray_background():
    # Fix 2: conversation text (assistant) carries a soft gray background for readability.
    renderable = render.render_event(events.AssistantTextDelta(text="hello world"))

    assert isinstance(renderable, Text)
    assert renderable.style.bgcolor is not None


def test_render_thinking_delta_includes_the_text():
    text = _render_to_text(render.render_event(events.ThinkingDelta(text="pondering")))

    assert "pondering" in text


def test_render_tool_call_started_is_a_line_not_a_panel():
    renderable = render.render_event(
        events.ToolCallStarted(tool_call_id="t1", name="bash", args="ls -la")
    )

    # The started notice is a one-liner; the panel arrives on the result (no flicker).
    assert isinstance(renderable, Text)
    text = _render_to_text(renderable)
    assert "bash" in text
    assert "ls -la" in text


def test_render_tool_result_is_a_panel_with_output():
    renderable = render.render_event(
        events.ToolResult(tool_call_id="t1", name="bash", output="file.txt")
    )

    # ADR-0002 §6: completed tool calls render as a panel.
    assert isinstance(renderable, Panel)
    text = _render_to_text(renderable)
    assert "bash" in text
    assert "file.txt" in text


def test_render_failed_tool_result_marks_the_failure():
    text = _render_to_text(
        render.render_event(
            events.ToolResult(tool_call_id="t1", name="bash", output="boom", ok=False)
        )
    )

    assert "bash" in text
    assert "failed" in text.lower()


def test_render_permission_requested_shows_the_tool():
    text = _render_to_text(
        render.render_event(
            events.PermissionRequested(tool_call_id="t1", name="write", args="a.txt")
        )
    )

    assert "write" in text
    assert "permission" in text.lower()


def test_render_ask_user_requested_shows_the_question():
    text = _render_to_text(
        render.render_event(events.AskUserRequested(tool_call_id="t1", question="which env?"))
    )

    assert "which env?" in text


def test_render_task_list_updated_lists_the_tasks():
    text = _render_to_text(
        render.render_event(events.TaskListUpdated(tasks=("plan", "code", "test")))
    )

    assert "plan" in text
    assert "code" in text
    assert "test" in text


def test_render_task_list_updated_shows_a_mixed_status_checklist():
    # The tasks tool emits already-status-marked lines ([x]/[~]/[ ]); the renderer shows them
    # as a checklist so a mixed-status list reads sensibly (ADR-0002 §7).
    text = _render_to_text(
        render.render_event(events.TaskListUpdated(tasks=("[x] design", "[~] build", "[ ] test")))
    )

    assert "[x] design" in text
    assert "[~] build" in text
    assert "[ ] test" in text


def test_render_empty_task_list_shows_a_placeholder():
    text = _render_to_text(render.render_event(events.TaskListUpdated()))

    assert "no tasks" in text.lower()


def test_render_turn_started_quotes_the_prompt_after_you():
    text = _render_to_text(render.render_event(events.TurnStarted(turn_id=0, prompt="do a thing")))

    # Fix 2: the user message renders as `you "<message>"` (double-quoted).
    assert 'you "do a thing"' in text


def test_render_turn_started_has_a_subtle_gray_background():
    # Fix 2: the user (`you "…"`) line carries a soft gray background for readability.
    renderable = render.render_event(events.TurnStarted(turn_id=0, prompt="do a thing"))

    assert isinstance(renderable, Text)
    assert renderable.style.bgcolor is not None


def test_render_turn_finished_marks_abort():
    text = _render_to_text(render.render_event(events.TurnFinished(turn_id=0, aborted=True)))

    assert "abort" in text.lower()


def test_render_context_compacted_is_a_dim_one_liner():
    event = events.ContextCompacted(before_tokens=900_123, kept_messages=4)
    renderable = render.render_event(event)

    assert isinstance(renderable, Text)
    assert "dim" in str(renderable.style)
    text = _render_to_text(renderable)
    assert "compacted context" in text
    assert "900123" in text  # the before-tokens count
    assert "4 recent messages" in text  # the kept-message count


def test_render_context_microcompacted_is_a_dim_one_liner():
    event = events.ContextMicrocompacted(elided_count=3, before_tokens=700_456)
    renderable = render.render_event(event)

    assert isinstance(renderable, Text)
    assert "dim" in str(renderable.style)
    text = _render_to_text(renderable)
    assert "microcompacted context" in text
    assert "elided 3" in text  # how many old tool outputs were blanked
    assert "700456" in text  # the before-tokens count


def test_render_agent_error_shows_the_message():
    text = _render_to_text(render.render_event(events.AgentError(message="kaboom")))

    assert "kaboom" in text
    assert "error" in text.lower()


def test_render_event_rejects_unknown_event_type():
    class Bogus:
        pass

    try:
        render.render_event(Bogus())
    except TypeError:
        return
    raise AssertionError("expected TypeError for an unknown event type")


# --- context_gauge: the pure footer fill gauge (task 047 / ADR-0006 §9) ------------------------

# Default tier fill lines (warn / danger), used by the color tests below. Kept as plain numbers
# here; the single-source-of-truth test derives them from the Settings defaults instead.
_WARN_AT = 0.60
_DANGER_AT = 0.80


@pytest.mark.parametrize(
    ("fraction", "glyph"),
    [
        (0.0, "○"),  # empty
        (0.25, "◔"),  # round(1.0) -> 1
        (0.5, "◑"),  # round(2.0) -> 2
        (0.75, "◕"),  # round(3.0) -> 3
        (1.0, "●"),  # full
        (0.78, "◕"),  # round(3.12) -> 3, NOT the full glyph
    ],
)
def test_context_gauge_glyph_buckets(fraction, glyph):
    label, _ = render.context_gauge(fraction, warn_at=_WARN_AT, danger_at=_DANGER_AT)

    assert label.startswith(glyph)


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [
        (0.0, "○ 0%"),
        (0.25, "◔ 25%"),
        (0.5, "◑ 50%"),
        (0.75, "◕ 75%"),
        (0.78, "◕ 78%"),
        (1.0, "● 100%"),
    ],
)
def test_context_gauge_label_is_glyph_space_percent(fraction, expected):
    # `label` formats as "{glyph} {pct}%" with pct the rounded clamped percentage.
    label, _ = render.context_gauge(fraction, warn_at=_WARN_AT, danger_at=_DANGER_AT)

    assert label == expected


@pytest.mark.parametrize(
    ("fraction", "color"),
    [
        (0.0, "green"),
        (0.59, "green"),  # just below warn_at
        (0.60, "yellow"),  # exact warn boundary -> yellow (>= warn_at)
        (0.70, "yellow"),  # inside [warn_at, danger_at)
        (0.79, "yellow"),  # just below danger_at
        (0.80, "red"),  # exact danger boundary -> red (>= danger_at)
        (0.95, "red"),
    ],
)
def test_context_gauge_colors_track_the_tier_lines(fraction, color):
    # green below warn_at, yellow in [warn_at, danger_at), red at/above danger_at -- boundaries
    # are inclusive on the upper tier (>=), asserted at the default 0.60 / 0.80 fill lines.
    _, got = render.context_gauge(fraction, warn_at=_WARN_AT, danger_at=_DANGER_AT)

    assert got == color


def test_context_gauge_clamps_overflow_to_full_red():
    # A fraction above 1.0 clamps to 1.0 -> "● 100%", red (>= danger_at).
    label, color = render.context_gauge(1.4, warn_at=_WARN_AT, danger_at=_DANGER_AT)

    assert label == "● 100%"
    assert color == "red"


def test_context_gauge_clamps_underflow_to_empty_green():
    # A negative fraction clamps to 0.0 -> "○ 0%", green (below warn_at).
    label, color = render.context_gauge(-0.1, warn_at=_WARN_AT, danger_at=_DANGER_AT)

    assert label == "○ 0%"
    assert color == "green"


def test_context_gauge_tiers_derive_from_the_reserve_settings():
    # Single source of truth (ADR-0006 §9): the call site computes the fill lines from the same
    # reserve fractions the compaction cascade uses, so the gauge colors track the actual tiers.
    warn_at = 1 - settings.microcompaction_reserve_fraction  # 0.60 on the defaults
    danger_at = 1 - settings.compaction_reserve_fraction  # 0.80 on the defaults
    assert (warn_at, danger_at) == pytest.approx((0.60, 0.80))

    # green just under the (derived) warn line, yellow exactly on it, red exactly on the danger line.
    assert render.context_gauge(warn_at - 0.01, warn_at=warn_at, danger_at=danger_at)[1] == "green"
    assert render.context_gauge(warn_at, warn_at=warn_at, danger_at=danger_at)[1] == "yellow"
    assert render.context_gauge(danger_at, warn_at=warn_at, danger_at=danger_at)[1] == "red"
