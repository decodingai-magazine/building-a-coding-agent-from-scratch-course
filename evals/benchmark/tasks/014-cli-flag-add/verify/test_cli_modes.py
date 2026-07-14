"""Hidden pytest-style tests for 014-cli-flag-add, injected into the Workspace only at grade time.

Exercises BOTH modes of cli.py by running it as a subprocess: the unchanged default text output and
the new ``--json`` object. Written as ``test_*`` functions so it is a genuine pytest file; verify.sh
executes it by importing and calling each ``test_`` function, so no pytest install is required in the
sandbox (only python3). The JSON case is compared by parsed content, so key order and whitespace do
not matter — only the data.
"""

from __future__ import annotations

import json
import subprocess
import sys


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_text_mode_repeats() -> None:
    proc = _run(["Ada", "--times", "2"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "Hello, Ada!\nHello, Ada!\n"


def test_text_mode_default_single() -> None:
    proc = _run(["Bob"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "Hello, Bob!\n"


def test_json_mode() -> None:
    proc = _run(["Ada", "--times", "3", "--json"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload == {"name": "Ada", "times": 3, "greeting": "Hello, Ada!"}
