"""A tiny statistics package with two seeded bugs (demo-2-bug-hunt).

``mean`` is correct; ``median`` has an off-by-one on odd-length inputs and ``variance`` has a
flipped sign. The committed ``test_stats.py`` pins the correct behaviour, so exactly two of its
tests fail until both bugs are fixed.
"""

from __future__ import annotations

from collections.abc import Sequence


def mean(values: Sequence[float]) -> float:
    """Return the arithmetic mean of a non-empty sequence."""
    if not values:
        raise ValueError("mean() requires at least one value")
    return sum(values) / len(values)


def median(values: Sequence[float]) -> float:
    """Return the median of a non-empty sequence.

    BUG: for an odd-length input this returns ``ordered[mid - 1]`` instead of ``ordered[mid]``,
    an off-by-one that yields the value just below the true middle element.
    """
    if not values:
        raise ValueError("median() requires at least one value")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid - 1]
    return (ordered[mid - 1] + ordered[mid]) / 2


def variance(values: Sequence[float]) -> float:
    """Return the population variance of a non-empty sequence.

    BUG: the squared deviations are negated, so this returns a non-positive number instead of the
    (always non-negative) variance.
    """
    if not values:
        raise ValueError("variance() requires at least one value")
    mu = mean(values)
    return sum(-((x - mu) ** 2) for x in values) / len(values)
