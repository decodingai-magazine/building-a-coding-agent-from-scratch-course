"""Tests for ranges.numbers_up_to — run with: python3 -m unittest -v test_ranges."""

from __future__ import annotations

import unittest

from ranges import numbers_up_to


class TestRanges(unittest.TestCase):
    def test_starts_at_one(self) -> None:
        self.assertEqual(numbers_up_to(5)[0], 1)

    def test_contains_two(self) -> None:
        self.assertIn(2, numbers_up_to(5))

    def test_no_zero(self) -> None:
        self.assertNotIn(0, numbers_up_to(5))

    def test_inclusive_end(self) -> None:
        self.assertEqual(numbers_up_to(5), [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
