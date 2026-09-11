"""Case 07 — "plan, don't change anything" enters plan mode and edits nothing (ADR-0003 §8; ADR-0017 §2,6).

Plan-mode discipline (ADR-0003): asked to PLAN a change and explicitly not touch anything yet, the agent
should call ``enter_plan_mode`` (which flips the gate to ``PLAN``) and present a plan — not start
editing. A small module is seeded; the run passes when ``enter_plan_mode`` WAS called AND neither
``write`` nor ``edit`` SUCCEEDED. :class:`ToolNotSucceededMetric` (not :class:`ToolNotCalledMetric`) is
the right grader: even if the model attempts an edit, ``enter_plan_mode`` has flipped the gate to
``PLAN`` so the write is denied and never lands — a denied attempt still satisfies "changed nothing".

Runs under the default ``BYPASS`` gate on purpose: the case measures the model's OWN restraint (does it
choose plan mode and hold off?), so nothing external blocks a mutation — a bad run that edited would
score zero, which is exactly the regression we want to catch.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import MaxStepsMetric, ToolCalledMetric, ToolNotSucceededMetric
from evals.regression.case import RegressionCase

_APP = "app.py"
_APP_BODY = (
    "import sys\n\n\ndef main():\n    print('hello')\n\n\nif __name__ == '__main__':\n    main()\n"
)


def _fixture(workspace: Path) -> None:
    """Seed a small CLI module the plan is about (never actually edited)."""
    (workspace / _APP).write_text(_APP_BODY, encoding="utf-8")


CASE = RegressionCase(
    id="07-plan-mode-discipline",
    prompt=(
        f"Plan how you would add a --verbose flag to {_APP}. Do not change anything yet — just enter "
        "plan mode and present the plan."
    ),
    fixture=_fixture,
    difficulty="medium",
    symptom=(
        "harness invariant: 'plan, do not change anything yet' enters plan mode and writes nothing."
    ),
    assertion=(
        "The response presents a plan for the requested change and makes clear nothing has been "
        "modified yet."
    ),
    metrics=[
        ToolCalledMetric("enter_plan_mode"),
        ToolNotSucceededMetric("write"),
        ToolNotSucceededMetric("edit"),
        MaxStepsMetric(),
    ],
    # Calibrated from observation (task 167, scope amended by the orchestrator decision of
    # 2026-09-11 — the grooming table missed this case), not guessed: three solo runs against the
    # default model under a temporary 12-leg ceiling spent 7 / 6 / 6 legs and scored every metric
    # 1.0 (Opik experiments 01a09040-02c5-7805-b1f4-e0b5a89dd6b5,
    # 01a09040-b4c8-7250-88ca-ddfab9d40dde, 01a09041-608a-7bf4-b0d6-f4dce8e95c40), so the budget is
    # max + 1. The legs are the plan itself and nothing else — every run ran
    # ``enter_plan_mode, glob, read, glob[, glob], exit_plan_mode`` — so the old 6 sat exactly on the
    # spread of correct behavior rather than above it.
    max_requests=8,
    tags=["plan-mode-discipline", "gate-respect"],
)
