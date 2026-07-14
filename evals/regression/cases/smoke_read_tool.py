"""Reference probe: reading a file uses the ``read`` tool, not ``bash cat`` (ADR-0017 §6).

The template every behavior probe copies (the real suite lands in tasks 112-114). It seeds one small
file, asks the agent what the file says, and grades read-tool discipline: the ``read`` tool WAS used
and ``bash`` was NOT (a `cat` shell-out is the anti-pattern). ``max_requests`` caps a runaway run.
Runs under the default headless ``BYPASS`` gate — reading needs no approval.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import MaxStepsMetric, ToolCalledMetric, ToolNotCalledMetric
from evals.regression.probe import RegressionProbe

_NOTES = "notes.txt"
_NOTES_BODY = "The launch code is 4127."


def _fixture(workspace: Path) -> None:
    """Seed one readable notes file the probe asks the agent about."""
    (workspace / _NOTES).write_text(_NOTES_BODY, encoding="utf-8")


PROBE = RegressionProbe(
    id="smoke-read-tool",
    prompt=f"What does the file {_NOTES} say? Read it and tell me.",
    fixture=_fixture,
    metrics=[
        ToolCalledMetric("read"),
        ToolNotCalledMetric("bash"),
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["read-discipline", "reference"],
)
