"""The GRADED suite for 016 — overlaid onto <checkout>/tests/ after the agent is done.

One clause of ``intervals.merge_intervals``'s docstring contract per test, in ``unittest`` style so
the sandbox image's bare python3 can run it (there is no pytest). Run FROM ``tests/``, so the module
puts the CHECKOUT ROOT on ``sys.path`` itself: ``intervals`` then always resolves to the module the
agent implemented, never to a same-named file dropped next to this one. Every case is FAIL_TO_PASS —
the seed raises ``NotImplementedError``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intervals import merge_intervals


class TestMergeIntervals(unittest.TestCase):
    def test_basic_overlap(self) -> None:
        self.assertEqual(
            merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]), [[1, 6], [8, 10], [15, 18]]
        )

    def test_touching_intervals_merge(self) -> None:
        self.assertEqual(merge_intervals([[1, 4], [4, 5]]), [[1, 5]])

    def test_unsorted_input_is_sorted(self) -> None:
        self.assertEqual(merge_intervals([[5, 6], [1, 3], [2, 4]]), [[1, 4], [5, 6]])

    def test_fully_nested_interval(self) -> None:
        self.assertEqual(merge_intervals([[1, 10], [2, 3], [4, 5]]), [[1, 10]])

    def test_disjoint_intervals_unchanged(self) -> None:
        self.assertEqual(merge_intervals([[1, 2], [5, 6]]), [[1, 2], [5, 6]])

    def test_empty_input(self) -> None:
        self.assertEqual(merge_intervals([]), [])

    def test_input_not_mutated(self) -> None:
        data = [[3, 4], [1, 2]]
        snapshot = [list(pair) for pair in data]

        merge_intervals(data)

        self.assertEqual(data, snapshot)

    def test_returns_lists_not_tuples(self) -> None:
        result = merge_intervals([[1, 2]])

        self.assertEqual(result, [[1, 2]])
        self.assertTrue(all(isinstance(pair, list) for pair in result))


if __name__ == "__main__":
    unittest.main()
