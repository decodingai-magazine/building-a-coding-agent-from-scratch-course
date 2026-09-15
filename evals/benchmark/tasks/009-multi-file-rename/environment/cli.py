"""Tiny wrapper that totals a fixed basket."""

from __future__ import annotations

from billing import compute_total


def run() -> float:
    """Total a fixed basket of prices (no tax)."""
    return compute_total([4.0, 5.5, 0.5])
