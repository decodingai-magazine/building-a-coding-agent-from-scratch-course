"""The GRADED suite for 019 — overlaid onto <checkout>/tests/ after the agent is done.

The same two assertions the seeded ``test_greet.py`` carries, so an agent that "resolved" the
conflict by rewriting its own copy is graded on these instead. They are the deterministic
replacement for v1's ``resolution_quality`` G-Eval judge (ADR-0022 §6): BOTH intents survive exactly
when the greeting is the patch's wording and punctuation around the tree's capitalized name. Run
FROM ``tests/``, the module puts the CHECKOUT ROOT on ``sys.path`` so ``greet`` is the agent's file.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greet import greet


class TestGreet(unittest.TestCase):
    def test_greeting_wording_and_capitalization(self) -> None:
        self.assertEqual(greet("bob"), "Hi there, Bob!")

    def test_capitalize_lowercases_the_rest(self) -> None:
        self.assertEqual(greet("ANNA"), "Hi there, Anna!")


if __name__ == "__main__":
    unittest.main()
