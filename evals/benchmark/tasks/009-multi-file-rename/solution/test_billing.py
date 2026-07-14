"""Tests for the billing math."""

from __future__ import annotations

from billing import calculate_total


def test_sum_without_tax() -> None:
    assert calculate_total([1.0, 2.0, 3.0]) == 6.0


def test_sum_with_tax() -> None:
    assert calculate_total([100.0], 0.1) == 110.0
