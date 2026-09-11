"""The trace fixtures under ``tests/unit/evals/fixtures/traces/`` carry nothing identifying.

Two of those fixtures are REAL ``decode_run`` exports from the live ``decode-prod`` project, kept
because hand-made spans do not reproduce the shapes ``evals mine`` reads. Anonymising them is a
manual pass (ids and threads re-minted, prompts and outputs replaced, ``created_by``/``filepath``
stripped) and a manual pass misses things — the first round shipped the real Modal serving
hostname on every LLM span, spotted in review, not by a test.

So this is the test that makes the pass unmissable: every file in the fixture directory is read as
raw text and scanned for a small deny-list — the workspace owner's name, Modal host domains, home
paths, e-mail addresses, and the two API-key prefixes the repo actually uses. The patterns are
bounded on purpose (``sk-`` alone matches ``task-163``); raw text, not parsed JSON, so a leak in a
key or in a nested JSON-encoded string cannot slip past.

Adding a fixture is therefore free: drop the file in and this test tells you if it needs scrubbing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "traces"

# (name, pattern) — what a leak looks like. Bounded forms only: a bare ``sk-`` matches ``task-163``
# and a bare ``@`` matches a decorator inside a captured traceback.
DENY_LIST: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("workspace owner's name", re.compile(r"iusztin", re.IGNORECASE)),
    ("Modal host domain", re.compile(r"modal\.(?:direct|run)", re.IGNORECASE)),
    ("home path", re.compile(r"/Users/|/home/[a-z]")),
    ("e-mail address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("OpenAI-style API key", re.compile(r"sk-[A-Za-z0-9]{8,}")),
    ("Google API key", re.compile(r"AIza[A-Za-z0-9_-]{10,}")),
)


def fixture_files() -> list[Path]:
    return sorted(path for path in FIXTURE_DIR.iterdir() if path.is_file())


def test_the_fixture_directory_is_not_empty_so_this_test_cannot_pass_vacuously() -> None:
    assert fixture_files(), f"no trace fixtures found under {FIXTURE_DIR}"


@pytest.mark.parametrize("path", fixture_files(), ids=lambda path: path.name)
def test_a_trace_fixture_carries_nothing_identifying(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    leaks = [
        f"{label}: {match.group(0)!r}"
        for label, pattern in DENY_LIST
        for match in [pattern.search(text)]
        if match is not None
    ]
    assert not leaks, (
        f"{path.name} leaks identifying data — anonymise it before committing: " + "; ".join(leaks)
    )


def test_the_scan_would_actually_catch_a_leak() -> None:
    """The deny-list is not vacuous: the exact string this test was written for still trips it."""
    leaked = "p-b-iusztin--ep-qwen3-6-35b-a3b-fp8-server.us-west.modal.direct"
    tripped = [label for label, pattern in DENY_LIST if pattern.search(leaked)]
    assert tripped == ["workspace owner's name", "Modal host domain"]


def test_a_bounded_key_pattern_does_not_fire_on_ordinary_fixture_text() -> None:
    """``sk-``/``AIza`` are bounded so ``task-163`` and prose never read as a credential."""
    ordinary = 'task-163 asked for "AIzaSyB" style prefixes to be bounded; sk-1 is not a key.'
    assert not [label for label, pattern in DENY_LIST if pattern.search(ordinary)]
