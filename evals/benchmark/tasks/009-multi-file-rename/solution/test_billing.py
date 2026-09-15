"""Tests for the billing math — run with: python3 -m unittest -v test_billing."""

from __future__ import annotations

import unittest

from billing import calculate_total


class TestBilling(unittest.TestCase):
    def test_sum_without_tax(self) -> None:
        self.assertEqual(calculate_total([1.0, 2.0, 3.0]), 6.0)

    def test_sum_with_tax(self) -> None:
        self.assertEqual(calculate_total([100.0], 0.1), 110.0)


if __name__ == "__main__":
    unittest.main()
