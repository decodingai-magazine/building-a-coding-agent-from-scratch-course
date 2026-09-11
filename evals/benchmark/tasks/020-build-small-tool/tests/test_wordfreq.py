"""The GRADED suite for 020 — overlaid onto <checkout>/tests/ after the agent is done.

Drives the agent's ``wordfreq.py`` as a REAL CLI (subprocess), so the whole tool — argument parsing,
tokenizing, counting, ordering, output format — is exercised end to end, in ``unittest`` style for
the sandbox image's bare python3. Paths are resolved from this file, so the suite runs the agent's
tool whatever the cwd. Every case is FAIL_TO_PASS: the seed is a stub that raises
``NotImplementedError``.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]


def _run(text: str, *args: str) -> list[str]:
    """Run ``wordfreq.py <tmpfile> <args>`` over ``text`` and return its stdout lines."""
    with tempfile.TemporaryDirectory() as directory:
        corpus = Path(directory) / "corpus.txt"
        corpus.write_text(text, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(CHECKOUT_ROOT / "wordfreq.py"), str(corpus), *args],
            cwd=CHECKOUT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    assert proc.returncode == 0, f"the CLI exited {proc.returncode}: {proc.stderr}"
    return proc.stdout.strip().splitlines()


class TestWordfreq(unittest.TestCase):
    def test_basic_counts_and_top_n(self) -> None:
        self.assertEqual(_run("The cat, the dog. The CAT!", "--top", "2"), ["the 3", "cat 2"])

    def test_surrounding_punctuation_stripped(self) -> None:
        self.assertEqual(_run("hello, world! hello."), ["hello 2", "world 1"])

    def test_ties_broken_alphabetically(self) -> None:
        self.assertEqual(
            _run("banana apple cherry", "--top", "3"), ["apple 1", "banana 1", "cherry 1"]
        )

    def test_case_insensitive(self) -> None:
        self.assertEqual(_run("Foo foo FOO"), ["foo 3"])

    def test_top_defaults_to_ten(self) -> None:
        text = " ".join(f"word{index:02d}" for index in range(12))

        self.assertEqual(len(_run(text)), 10)


if __name__ == "__main__":
    unittest.main()
