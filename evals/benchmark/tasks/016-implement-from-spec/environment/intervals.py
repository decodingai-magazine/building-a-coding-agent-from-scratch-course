"""Interval helpers."""

from __future__ import annotations


def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    """Merge all overlapping or touching intervals and return them sorted by start.

    Each interval is a two-element ``[start, end]`` list with ``start <= end``. Two intervals
    overlap when one's start is less than or equal to the other's end; intervals that merely touch
    (for example ``[1, 2]`` and ``[2, 3]``) also count as overlapping and merge into a single
    interval (``[1, 3]``).

    Return a NEW list of merged ``[start, end]`` lists sorted by start. The input list must not be
    mutated. An empty input returns an empty list.

    Examples:
        merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]
        merge_intervals([[1, 4], [4, 5]]) == [[1, 5]]
        merge_intervals([]) == []
    """
    raise NotImplementedError
