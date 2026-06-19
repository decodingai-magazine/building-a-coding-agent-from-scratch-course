"""Unit tests for the pure, decidable pieces of ``decode.tui.app``.

The interactive loop (``prompt_async`` inside ``patch_stdout``) reads real stdin and
cannot be driven from a unit test without a pseudo-terminal, so it is exercised by the
end-to-end smoke instead. Everything that has a decidable contract — quit-intent parsing,
the keybinding-intent enum, and the footer hint text — is extracted into pure functions
and tested here.
"""

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
