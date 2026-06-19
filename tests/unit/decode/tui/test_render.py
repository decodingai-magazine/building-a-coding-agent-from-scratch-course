"""Unit tests for the pure render functions in ``decode.tui.render``.

These functions map a minimal local event contract to Rich renderables. They are
pure (no I/O, no global state) so they are exhaustively unit-testable. The full
``entities.events`` union lands in task 003; this module renders the minimal local
contract defined alongside it.
"""

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from decode.tui import render


def _render_to_text(renderable) -> str:
    """Render a Rich renderable to plain text the way a terminal would show it."""
    console = Console(width=80, file=None, record=True)
    console.print(renderable)
    return console.export_text()


def test_render_echo_event_includes_the_text():
    event = render.EchoEvent(text="hello world")

    renderable = render.render_event(event)

    assert "hello world" in _render_to_text(renderable)


def test_render_echo_event_returns_a_rich_renderable():
    event = render.EchoEvent(text="anything")

    renderable = render.render_event(event)

    # Echo lines are plain text appended above the prompt (no panel chrome).
    assert isinstance(renderable, Text)


def test_render_message_event_includes_the_text():
    event = render.MessageEvent(text="the agent says hi")

    renderable = render.render_event(event)

    assert "the agent says hi" in _render_to_text(renderable)


def test_render_tool_call_event_is_a_panel():
    event = render.ToolCallEvent(name="bash", summary="ls -la", result="ok")

    renderable = render.render_event(event)

    # ADR-0002 §6: tool calls render on completion as a panel (no flicker).
    assert isinstance(renderable, Panel)


def test_render_tool_call_event_shows_name_summary_and_result():
    event = render.ToolCallEvent(name="bash", summary="ls -la", result="file.txt")

    text = _render_to_text(render.render_event(event))

    assert "bash" in text
    assert "ls -la" in text
    assert "file.txt" in text


def test_render_tool_call_event_without_result_still_renders():
    event = render.ToolCallEvent(name="read", summary="src/app.py", result=None)

    text = _render_to_text(render.render_event(event))

    assert "read" in text
    assert "src/app.py" in text


def test_render_event_rejects_unknown_event_type():
    class Bogus:
        pass

    try:
        render.render_event(Bogus())
    except TypeError:
        return
    raise AssertionError("expected TypeError for an unknown event type")
