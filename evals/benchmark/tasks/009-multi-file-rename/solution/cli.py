"""Tiny wrapper that totals a fixed basket."""

from __future__ import annotations

from billing import calculate_total


def run() -> float:
    """Total a fixed basket of prices (no tax)."""
    return calculate_total([4.0, 5.5, 0.5])
