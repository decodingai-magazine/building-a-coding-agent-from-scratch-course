"""Tests for registry.collect.

Each test registers one item into a fresh bucket and expects to get back only that item. They pass
in isolation but leak state into one another when the shared mutable default bucket is reused.
"""

from __future__ import annotations

from registry import collect


def test_collect_apple() -> None:
    assert collect("apple") == ["apple"]


def test_collect_banana() -> None:
    assert collect("banana") == ["banana"]
