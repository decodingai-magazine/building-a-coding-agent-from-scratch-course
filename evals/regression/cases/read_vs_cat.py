"""Case 01 — reading a file uses the ``read`` tool, not a ``bash cat`` shell-out (ADR-0017 §2,6).

Read-tool discipline (ADR-0002): when the user asks what a file says, the agent should ``read`` it, not
``bash cat`` it. One small notes file is seeded; the run passes when ``read`` WAS used and ``bash`` was
NOT. Runs under the default headless ``BYPASS`` gate — reading needs no approval.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import MaxStepsMetric, ToolCalledMetric, ToolNotCalledMetric
from evals.regression.case import RegressionCase

_NOTES = "notes.txt"
_NOTES_BODY = "The release train departs at 06:45 from platform 9.\n"


def _fixture(workspace: Path) -> None:
    """Seed one readable notes file the case asks the agent about."""
    (workspace / _NOTES).write_text(_NOTES_BODY, encoding="utf-8")


CASE = RegressionCase(
    id="01-read-vs-cat",
    prompt=f"Show me the contents of {_NOTES}.",
    fixture=_fixture,
    difficulty="easy",
    symptom=(
        "harness invariant: 'show me this file' is a read-tool call, not a `bash cat` shell-out."
    ),
    assertion=(
        "The response shows the contents of the file the user named, rather than telling the user "
        "to open it themselves."
    ),
    metrics=[
        ToolCalledMetric("read"),
        ToolNotCalledMetric("bash"),
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["read-discipline", "tool-discipline"],
)
