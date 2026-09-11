"""The GRADED suite for 017 — overlaid onto <checkout>/tests/ after the agent is done.

Same two assertions as the seeded ``test_registry.py`` the agent sees, so an agent that "fixed" the
flake by weakening its own copy is graded on this one instead. Run FROM ``tests/`` by
``_verify_suite.py``, in several method orders and a fresh process each time; the module puts the
CHECKOUT ROOT on ``sys.path`` so ``registry`` is always the agent's source.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from registry import collect


class TestRegistry(unittest.TestCase):
    def test_collect_apple(self) -> None:
        self.assertEqual(collect("apple"), ["apple"])

    def test_collect_banana(self) -> None:
        self.assertEqual(collect("banana"), ["banana"])


if __name__ == "__main__":
    unittest.main()
