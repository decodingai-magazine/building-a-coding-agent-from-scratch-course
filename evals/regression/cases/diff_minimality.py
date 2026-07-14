"""Probe 04 — a small refactor stays a small diff (ADR-0017 §2,6,7).

Minimal-diff discipline (ADR-0002): a targeted rename should change only the lines that mention the
symbol, not reflow the whole module. A tiny module is seeded and the agent is asked to rename one helper
throughout ``calc.py``. Two graders, deliberately mixed (the teaching contrast of ADR-0017 §6):

* :class:`FileDiffLinesMetric` — the mechanical floor: the changed-line count between the seeded
  baseline and the run's final ``calc.py`` is ``<= max_lines`` (the rename touches two lines → four
  changed lines; the threshold leaves headroom without licensing a rewrite);
* a G-Eval minimal-diff judge — the qualitative call code cannot make: was the change focused on the
  rename, or did it drag in unrelated edits?

Runs under ``BYPASS`` so the edit lands. The suite-level pass/fail threshold is task 115's; the
``max_lines`` here is the probe's own honest footprint budget.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.judges import make_judge
from evals.harness.metrics import FileDiffLinesMetric, MaxStepsMetric, ToolCalledMetric
from evals.regression.probe import RegressionProbe

_MODULE = "calc.py"
_MODULE_BODY = """\
def _helper(value):
    return value * 2


def run(values):
    return [_helper(v) for v in values]
"""

_MINIMAL_DIFF_JUDGE = make_judge(
    task_introduction=(
        "You are grading whether a coding agent made a MINIMAL, targeted change. The agent was asked "
        "to rename the helper function `_helper` to `_doubled` throughout a small module."
    ),
    evaluation_criteria=(
        # Phrased qualitatively — NOT as "Score 1.0/0.0" — because those numeric anchors collide with
        # Opik G-Eval's internal 0-10 scale and yield garbage (a perfect answer scored 0.1 in QA).
        "The change is fully minimal when the final file renames `_helper` to `_doubled` at its "
        "definition and every call site and changes nothing else — no reordering, reformatting, or "
        "unrelated edits. It is less minimal the further the change grows beyond that rename (extra "
        "edits, reflowed lines, or touched-but-unrelated code). The final file content is in "
        "`file_state`."
    ),
)


def _fixture(workspace: Path) -> None:
    """Seed the small module the refactor targets."""
    (workspace / _MODULE).write_text(_MODULE_BODY, encoding="utf-8")


PROBE = RegressionProbe(
    id="04-diff-minimality",
    prompt=f"Rename the helper function `_helper` to `_doubled` throughout {_MODULE}. Keep the change minimal.",
    fixture=_fixture,
    metrics=[
        ToolCalledMetric("edit"),
        FileDiffLinesMetric(path=_MODULE, baseline=_MODULE_BODY, max_lines=6),
        _MINIMAL_DIFF_JUDGE,
        MaxStepsMetric(),
    ],
    max_requests=8,
    tags=["diff-minimality", "refactor", "judge"],
)
