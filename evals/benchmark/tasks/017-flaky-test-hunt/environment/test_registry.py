"""Tests for registry.collect — run with: python3 -m unittest -v test_registry.

Each test registers one item into a fresh bucket and expects to get back only that item. They pass
in isolation but leak state into one another when the shared mutable default bucket is reused, so
the suite's verdict depends on the order the two run in.
"""

from __future__ import annotations

import unittest

from registry import collect


class TestRegistry(unittest.TestCase):
    def test_collect_apple(self) -> None:
        self.assertEqual(collect("apple"), ["apple"])

    def test_collect_banana(self) -> None:
        self.assertEqual(collect("banana"), ["banana"])


if __name__ == "__main__":
    unittest.main()
