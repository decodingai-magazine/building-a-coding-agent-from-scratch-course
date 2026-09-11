"""The GRADED suite for 014 — overlaid onto <checkout>/tests/ after the agent is done.

Exercises BOTH modes of cli.py as a subprocess: the unchanged default text output (PASS_TO_PASS) and
the new ``--json`` object (FAIL_TO_PASS — on the untouched seed argparse rejects the unknown flag).
Paths are resolved from this file, so the suite runs the agent's cli.py whatever the cwd. The JSON
case is compared by PARSED content, so key order and whitespace are free — only the data counts.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKOUT_ROOT / "cli.py"), *args],
        cwd=CHECKOUT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestCliModes(unittest.TestCase):
    def test_text_mode_repeats(self) -> None:
        proc = _run(["Ada", "--times", "2"])

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "Hello, Ada!\nHello, Ada!\n")

    def test_text_mode_default_single(self) -> None:
        proc = _run(["Bob"])

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "Hello, Bob!\n")

    def test_json_mode(self) -> None:
        proc = _run(["Ada", "--times", "3", "--json"])

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload, {"name": "Ada", "times": 3, "greeting": "Hello, Ada!"})


if __name__ == "__main__":
    unittest.main()
