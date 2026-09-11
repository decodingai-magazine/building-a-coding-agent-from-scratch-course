"""The GRADED copy of 007's suite — overlaid onto <checkout>/tests/ after the agent is done.

Identical in content to the seeded ``test_ranges.py`` the agent sees, minus one detail: it is run
from ``tests/`` (``cd tests && python3 -m unittest ...``), so it puts the CHECKOUT ROOT on
``sys.path`` itself. That import order is the isolation — ``ranges`` always resolves to the module
the agent edited, never to a same-named file the agent could drop next to this one.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
