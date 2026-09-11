"""Run 017's graded suite in several orders, each in a FRESH process (the order IS the measurement).

A flake that lives in a shared mutable default only shows up when one test runs after another, so a
single ``python3 -m unittest test_registry`` proves nothing: the leak might be hiding behind the
alphabetical order. This driver runs BOTH orders of the two node ids, twice each, every run a new
interpreter — so a leak across tests (order) and a leak across runs (module-level state) are both
observed. ``python3 -m unittest a b`` executes ``a`` then ``b``, in the order given.

Named with a leading underscore so it can never clobber a file the agent created; run by ``test.sh``
as ``python3 tests/_verify_suite.py``. Exits 0 only when every run is green.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

APPLE = "test_registry.TestRegistry.test_collect_apple"
BANANA = "test_registry.TestRegistry.test_collect_banana"

# Both orders, twice each: the seeded flake fails the (apple, banana) order on the very first run.
ORDERS = [(APPLE, BANANA), (BANANA, APPLE)] * 2


def main() -> int:
    for attempt, order in enumerate(ORDERS, start=1):
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", *order],
            cwd=TESTS_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            print(f"FAIL: run {attempt} of {len(ORDERS)} was not green in order {order}")
            print(completed.stdout)
            print(completed.stderr)
            return 1
    print(f"PASS: the suite is green in every order, {len(ORDERS)} fresh runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
