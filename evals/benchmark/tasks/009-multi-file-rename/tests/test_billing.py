"""The GRADED copy of 009's suite — overlaid onto <checkout>/tests/ after the agent is done.

Same two cases as the seeded ``test_billing.py``, written against the NEW name: on the untouched
seed both fail (``calculate_total`` does not exist yet), which is what makes them FAIL_TO_PASS. Run
from ``tests/``, it puts the checkout root on ``sys.path`` itself so ``billing`` resolves to the
module the agent edited.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestBilling(unittest.TestCase):
    def test_sum_without_tax(self) -> None:
        from billing import calculate_total

        self.assertEqual(calculate_total([1.0, 2.0, 3.0]), 6.0)

    def test_sum_with_tax(self) -> None:
        from billing import calculate_total

        self.assertEqual(calculate_total([100.0], 0.1), 110.0)


if __name__ == "__main__":
    unittest.main()
