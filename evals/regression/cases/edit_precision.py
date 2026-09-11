"""Case 03 — a one-value change is a surgical ``edit``, not a file rewrite (ADR-0017 §2,6).

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
from evals.regression.case import RegressionCase

_CONFIG = "config.py"
_CONFIG_BODY = 'HOST = "localhost"\nPORT = 8000\nDEBUG = False\n'


def _fixture(workspace: Path) -> None:
    """Seed a small config module carrying a ``PORT = 8000`` line to change."""
    (workspace / _CONFIG).write_text(_CONFIG_BODY, encoding="utf-8")


CASE = RegressionCase(
    id="03-edit-precision",
    prompt=f"Change the port in {_CONFIG} to 9000.",
    fixture=_fixture,
    difficulty="easy",
    symptom="harness invariant: a one-value change is a surgical edit, not a whole-file rewrite.",
    assertion=(
        "The response confirms the single value the user named was changed in place, and never "
        "describes rewriting the whole file."
    ),
    metrics=[
        ToolCalledMetric("edit"),
        FileDiffLinesMetric(path=_CONFIG, baseline=_CONFIG_BODY, max_lines=2),
        MaxStepsMetric(),
    ],
    # Calibrated from observation (task 167), not guessed: three solo runs against the default
    # model spent 7 / 6 / 5 legs (Opik experiments 01a09015-6c72-7f67-8612-09114ad16b04,
    # 01a09016-be55-782a-b0af-7fd01dbb4e00, 01a09017-54d2-731b-a743-d2fd1fb8d58e), so the budget
    # is max + 1 — headroom for the spread, two legs over the easy tier's usual 6 because the model
    # reliably re-reads (``glob`` / ``read``) around the one ``edit`` the case grades.
    max_requests=8,
    tags=["edit-precision", "tool-discipline"],
)
