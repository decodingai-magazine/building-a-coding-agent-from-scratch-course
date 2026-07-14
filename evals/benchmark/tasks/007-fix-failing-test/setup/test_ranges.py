"""Tests for ranges.numbers_up_to."""

from __future__ import annotations

from ranges import numbers_up_to


def test_starts_at_one() -> None:
    assert numbers_up_to(5)[0] == 1


def test_contains_two() -> None:
    assert 2 in numbers_up_to(5)


def test_no_zero() -> None:
    assert 0 not in numbers_up_to(5)


def test_inclusive_end() -> None:
    assert numbers_up_to(5) == [1, 2, 3, 4, 5]
