"""Probe 11 — a trivial ask is done in a few steps, no needless questions (ADR-0002; ADR-0017 §2,6).

Step-efficiency discipline: "create hello.txt containing exactly 'hi'" is a one-write task, so an
efficient agent writes the file and stops — it does not burn extra model requests or stop to ask the
user a clarifying question when the instruction is already unambiguous. An empty Workspace is seeded.
Three graders:

* :class:`MaxStepsMetric` — the run stayed within an HONEST request budget (a single ``write`` plus a
  finish leg is two model requests; the cap allows a little slack, not a runaway);
* :class:`ToolNotCalledMetric` on ``ask_user`` — the agent did not stall on a needless question;
* :class:`FileEqualsMetric` — ``hello.txt`` contains EXACTLY ``hi`` (a trailing newline or extra prose
  is a fail, byte-for-byte).

Runs under ``BYPASS`` so the write lands without a prompt — the discipline measured is efficiency, not
gate handling.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import FileEqualsMetric, MaxStepsMetric, ToolNotCalledMetric
from evals.regression.probe import RegressionProbe

_FILE = "hello.txt"
_EXPECTED = "hi"


def _fixture(_workspace: Path) -> None:
    """No seed — the agent creates the single file from an empty Workspace."""


PROBE = RegressionProbe(
    id="11-step-efficiency",
    prompt=f"Create a file named {_FILE} containing exactly {_EXPECTED!r} — no newline, nothing else.",
    fixture=_fixture,
    metrics=[
        FileEqualsMetric(path=_FILE, expected=_EXPECTED),
        ToolNotCalledMetric("ask_user"),
        MaxStepsMetric(),
    ],
    max_requests=4,
    tags=["step-efficiency", "no-needless-questions"],
)
