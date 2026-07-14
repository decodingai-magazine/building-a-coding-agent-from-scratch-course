"""Small numeric helpers."""

from __future__ import annotations


def numbers_up_to(n: int) -> list[int]:
    """Return the list [1, 2, ..., n] inclusive of n."""
    return list(range(1, n + 1))
