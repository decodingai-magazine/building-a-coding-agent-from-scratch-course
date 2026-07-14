"""Tests for the resolved greet.greet."""

from __future__ import annotations

from greet import greet


def test_greeting_wording_and_capitalization() -> None:
    assert greet("bob") == "Hi there, Bob!"


def test_capitalize_lowercases_the_rest() -> None:
    assert greet("ANNA") == "Hi there, Anna!"
