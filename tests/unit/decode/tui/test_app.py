"""Unit tests for the pure, decidable pieces of ``decode.tui.app``.

The interactive loop (``prompt_async`` inside ``patch_stdout``) reads real stdin and
cannot be driven from a unit test without a pseudo-terminal, so it is exercised by the
end-to-end smoke instead. Everything that has a decidable contract — quit-intent parsing,
the keybinding-intent enum, and the footer hint text — is extracted into pure functions
and tested here.
"""

import asyncio

import pytest
from rich.console import Console

from decode.entities import events
from decode.entities.permissions import PermissionOutcome, PermissionRequest
from decode.harness.decisions import DecisionChannel
from decode.tui import app


def _record_console() -> Console:
    """A console that records its output so the event-sink prefix can be asserted (Fix 2)."""
    return Console(width=100, record=True)


def test_event_sink_prefixes_the_assistant_answer_with_decode_once_per_turn():
    # Fix 2: `Decode ` is added once, before the first AssistantTextDelta of a turn.
    console = _record_console()
    sink = app._make_event_sink(console)

    sink(events.TurnStarted(turn_id=0, prompt="hi"))
    sink(events.AssistantTextDelta(text="hello "))
    sink(events.AssistantTextDelta(text="world"))
    sink(events.TurnFinished(turn_id=0))

    out = console.export_text()
    assert "Decode " in out
    assert out.count("Decode ") == 1  # exactly once for the turn


def test_event_sink_resets_the_decode_prefix_each_turn():
    # A new turn re-emits the `Decode ` prefix before its first delta.
    console = _record_console()
    sink = app._make_event_sink(console)

    sink(events.TurnStarted(turn_id=0, prompt="first"))
    sink(events.AssistantTextDelta(text="one"))
    sink(events.TurnStarted(turn_id=1, prompt="second"))
    sink(events.AssistantTextDelta(text="two"))

    assert console.export_text().count("Decode ") == 2


def test_event_sink_does_not_prefix_non_assistant_events():
    # Tool/permission/etc. events never get the `Decode ` prefix.
    console = _record_console()
    sink = app._make_event_sink(console)

    sink(events.TurnStarted(turn_id=0, prompt="hi"))
    sink(events.ToolResult(tool_call_id="t1", name="bash", output="ok"))

    assert "Decode " not in console.export_text()


def test_is_quit_command_matches_slash_quit():
    assert app.is_quit_command("/quit") is True


def test_is_quit_command_ignores_surrounding_whitespace():
    assert app.is_quit_command("  /quit  ") is True


def test_is_quit_command_is_false_for_other_input():
    assert app.is_quit_command("hello") is False
    assert app.is_quit_command("/quitter") is False
    assert app.is_quit_command("") is False


def test_footer_hint_mentions_steer_followup_and_abort():
    hint = app.footer_hint()

    assert "steer" in hint.lower()
    assert "follow-up" in hint.lower()
    assert "Alt+Enter" in hint
    assert "abort" in hint.lower()
    assert "Esc" in hint


def test_footer_hint_mentions_quit():
    hint = app.footer_hint()

    assert "/quit" in hint


def test_input_intent_enum_has_steer_followup_and_abort():
    # The keybindings record/emit intent for now (no harness until task 003).
    assert app.InputIntent.STEER.value == "steer"
    assert app.InputIntent.FOLLOW_UP.value == "follow-up"
    assert app.InputIntent.ABORT.value == "abort"


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "allow", "a", "  yes  ", "ALLOW"])
def test_parse_permission_answer_allows_on_affirmative(answer):
    decision = app.parse_permission_answer(answer)

    assert decision.outcome is PermissionOutcome.ALLOW


@pytest.mark.parametrize("answer", ["n", "no", "deny", "d", "", "anything else", "maybe"])
def test_parse_permission_answer_denies_on_anything_else(answer):
    # ADR-0002 §3 + the safe default: anything that is not a clear "yes" denies.
    decision = app.parse_permission_answer(answer)

    assert decision.outcome is PermissionOutcome.DENY
    assert decision.reason  # a human-facing reason is fed back to the model


def test_deny_permission_resolver_is_the_safe_headless_default():
    # Headless / no-TUI callers get a resolver that always denies (safe default).
    request = PermissionRequest(tool_name="noop", args="{}")
    decision = asyncio.run(app.deny_permission_resolver(request))

    assert decision.outcome is PermissionOutcome.DENY


def _quiet_console() -> Console:
    """A throwaway console that swallows the resolver's affordance print."""
    import io

    return Console(file=io.StringIO(), force_terminal=False)


async def test_interactive_resolver_awaits_the_channel_then_parses_the_answer():
    # The interactive resolver collects the verdict from the single decision channel (no
    # second prompt): the next resolved line is parsed into the allow/deny decision.
    channel = DecisionChannel()
    resolver = app._make_permission_resolver(channel, _quiet_console())
    request = PermissionRequest(tool_name="noop", args="{}")

    task = asyncio.ensure_future(resolver(request))
    await asyncio.sleep(0)  # let the resolver register the pending decision
    assert channel.pending is True

    channel.resolve("y")
    decision = await task
    assert decision.outcome is PermissionOutcome.ALLOW


async def test_interactive_resolver_denies_when_the_decision_is_cancelled():
    # Turn aborted / REPL shutting down cancels the pending request; the resolver denies
    # (the safe default) instead of hanging.
    channel = DecisionChannel()
    resolver = app._make_permission_resolver(channel, _quiet_console())
    request = PermissionRequest(tool_name="noop", args="{}")

    task = asyncio.ensure_future(resolver(request))
    await asyncio.sleep(0)
    channel.cancel()

    decision = await task
    assert decision.outcome is PermissionOutcome.DENY
    assert decision.reason


async def test_ask_user_resolver_returns_the_typed_line_verbatim():
    # The ask_user resolver awaits the SAME single decision channel and returns the raw line as
    # the free-text answer (no y/N parsing, unlike the permission resolver).
    channel = DecisionChannel()
    resolver = app._make_user_question_resolver(channel, _quiet_console())

    task = asyncio.ensure_future(resolver("which file?"))
    await asyncio.sleep(0)  # let the resolver register the pending decision
    assert channel.pending is True

    channel.resolve("src/main.py")
    answer = await task
    assert answer == "src/main.py"


async def test_ask_user_resolver_propagates_cancellation():
    # A cancelled request (abort / shutdown) must propagate CancelledError out of the resolver
    # so decode.tools.askuser.ask_user maps it to a clean ModelRetry (never a hang).
    channel = DecisionChannel()
    resolver = app._make_user_question_resolver(channel, _quiet_console())

    task = asyncio.ensure_future(resolver("still there?"))
    await asyncio.sleep(0)
    channel.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
