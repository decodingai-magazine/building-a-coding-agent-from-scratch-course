"""Hidden authoritative copy of the test suite for 017-flaky-test-hunt, injected at grade time.

The agent could edit the visible test_registry.py to cheat, so the oracle runs THIS copy (never seen
by the agent) against the agent's registry.py. Named with a leading underscore so it never clobbers
an agent file; run by verify.sh via import-and-call in a fresh process per run (no pytest binary in
the slim sandbox image).
"""

from __future__ import annotations

from registry import collect


def test_collect_apple() -> None:
    assert collect("apple") == ["apple"]


def test_collect_banana() -> None:
    assert collect("banana") == ["banana"]
