"""Tests for the resolved greet.greet — run with: python3 -m unittest -v test_greet.

Both cases fail on the unresolved tree: they pin the greeting AFTER the patch's wording and
punctuation have been combined with the tree's capitalization of the name.
"""

from __future__ import annotations

import unittest

from greet import greet


class TestGreet(unittest.TestCase):
    def test_greeting_wording_and_capitalization(self) -> None:
        self.assertEqual(greet("bob"), "Hi there, Bob!")

    def test_capitalize_lowercases_the_rest(self) -> None:
        self.assertEqual(greet("ANNA"), "Hi there, Anna!")


if __name__ == "__main__":
    unittest.main()
