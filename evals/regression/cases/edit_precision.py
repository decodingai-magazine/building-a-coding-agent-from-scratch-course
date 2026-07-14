"""Probe 03 — a one-value change is a surgical ``edit``, not a file rewrite (ADR-0017 §2,6).

Edit-precision (ADR-0002): "change the port to 9000" should touch exactly the ``PORT`` line via the
``edit`` tool, leaving the rest of ``config.py`` byte-for-byte. A small config module is seeded; the run
passes when ``edit`` WAS used AND the post-run diff of ``config.py`` is a single-line change (one ``-``
plus one ``+`` = two changed lines — :class:`FileDiffLinesMetric` with ``max_lines=2``). The metric
diffs the run's final ``file_state`` against the seeded baseline, since the regression payload records a
tree snapshot, not a diff. Runs under ``BYPASS`` so the edit lands without a prompt.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import FileDiffLinesMetric, MaxStepsMetric, ToolCalledMetric
from evals.regression.probe import RegressionProbe

_CONFIG = "config.py"
_CONFIG_BODY = 'HOST = "localhost"\nPORT = 8000\nDEBUG = False\n'


def _fixture(workspace: Path) -> None:
    """Seed a small config module carrying a ``PORT = 8000`` line to change."""
    (workspace / _CONFIG).write_text(_CONFIG_BODY, encoding="utf-8")


PROBE = RegressionProbe(
    id="03-edit-precision",
    prompt=f"Change the port in {_CONFIG} to 9000.",
    fixture=_fixture,
    metrics=[
        ToolCalledMetric("edit"),
        FileDiffLinesMetric(path=_CONFIG, baseline=_CONFIG_BODY, max_lines=2),
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["edit-precision", "tool-discipline"],
)
