"""Arithmetic helpers (formerly ``calc.py`` — the module main.py still tries to import)."""

from __future__ import annotations


def factorial(n: int) -> int:
    """Return n! for a non-negative integer n."""
    result = 1
    for value in range(2, n + 1):
        result *= value
    return result
