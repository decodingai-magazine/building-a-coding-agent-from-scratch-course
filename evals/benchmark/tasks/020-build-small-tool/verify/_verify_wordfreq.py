"""Hidden test module for 020-build-small-tool, injected only at grade time.

Drives the agent's wordfreq.py as a real CLI (subprocess) so the whole tool — argument parsing,
tokenizing, counting, ordering, output format — is exercised end to end. Named with a leading
underscore so it never clobbers an agent file; run by verify.sh via import-and-call (no pytest binary
in the slim sandbox image).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def _run(text: str, *args: str) -> list[str]:
    """Run ``wordfreq.py <tmpfile> <args>`` over ``text`` and return its stdout lines."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        path = handle.name
    try:
        proc = subprocess.run(
            [sys.executable, "wordfreq.py", path, *args],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.unlink(path)
    assert proc.returncode == 0, f"CLI exited {proc.returncode}: {proc.stderr}"
    return proc.stdout.strip().splitlines()


def test_basic_counts_and_top_n() -> None:
    assert _run("The cat, the dog. The CAT!", "--top", "2") == ["the 3", "cat 2"]


def test_surrounding_punctuation_stripped() -> None:
    assert _run("hello, world! hello.") == ["hello 2", "world 1"]


def test_ties_broken_alphabetically() -> None:
    assert _run("banana apple cherry", "--top", "3") == ["apple 1", "banana 1", "cherry 1"]


def test_case_insensitive() -> None:
    assert _run("Foo foo FOO") == ["foo 3"]


def test_top_defaults_to_ten() -> None:
    text = " ".join(f"word{index:02d}" for index in range(12))
    assert len(_run(text)) == 10
