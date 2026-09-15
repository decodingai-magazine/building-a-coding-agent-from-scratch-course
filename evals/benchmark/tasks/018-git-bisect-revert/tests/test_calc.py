"""The GRADED suite for 018 — overlaid onto <checkout>/tests/ after the agent is done.

Identical assertions to the ``test_calc.py`` commit 1 seeded into the history, minus one detail: run
FROM ``tests/``, it puts the CHECKOUT ROOT on ``sys.path`` itself, so ``calc`` always resolves to the
module the agent's history produced. ``test_multiply`` is the one the breaking commit fails.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calc import add, divide, multiply, subtract


class TestCalc(unittest.TestCase):
    def test_add(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_subtract(self) -> None:
        self.assertEqual(subtract(5, 2), 3)

    def test_multiply(self) -> None:
        self.assertEqual(multiply(4, 3), 12)

    def test_divide(self) -> None:
        self.assertEqual(divide(10, 2), 5)


if __name__ == "__main__":
    unittest.main()
