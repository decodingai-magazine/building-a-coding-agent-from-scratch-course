"""Unit tests for the pure render functions in ``decode.tui.render``.

These map the canonical :mod:`decode.entities.events` union to Rich renderables. They are
pure (no I/O, no global state) so they are exhaustively unit-testable.
"""

from rich.panel import Panel
from rich.text import Text

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
