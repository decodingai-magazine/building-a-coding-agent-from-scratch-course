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

from decode.entities.permissions import PermissionOutcome, PermissionRequest
from decode.harness.decisions import DecisionChannel
from decode.tui import app


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
