"""The pre-merge regression threshold gate — a pytest ritual OUTSIDE ``testpaths`` (ADR-0017 §6,9).

This module is DELIBERATELY not collected by plain ``pytest`` / ``make ci``: ``testpaths`` in
``pyproject.toml`` is ``tests/unit`` + ``tests/integration``, and this file lives under ``evals/``. It
runs only when invoked explicitly::

    make eval-regression          # -> uv run pytest evals/regression/test_thresholds.py
    uv run pytest evals/regression/test_thresholds.py

It costs real money and needs ``GEMINI_API_KEY`` + ``OPIK_API_KEY`` (the agent runs and Opik stores the
experiment), so with either key absent it SKIPS with a friendly reason and exits 0.

The module stays thin on purpose. It runs the probe suite ONCE (a session-scoped fixture over
:func:`evals.harness.regression.run_regression`) and then delegates every judgement to the pure,
offline-tested helpers in :mod:`evals.regression.thresholds`:

* the absolute per-metric threshold table is the HARD gate — :func:`evaluate_thresholds` fails the run
  when any metric falls below its floor (tool-discipline ≥ 0.8, judges ≥ 0.7);
* the baseline compare is a SOFT signal — it fetches the previous experiment by its stable name and
  WARNs on per-metric regressions, but never fails the gate (usable on day one with no baseline).
"""

from __future__ import annotations

import logging
import os
import warnings
from typing import TYPE_CHECKING

import pytest

from evals.regression.thresholds import (
    BaselineCandidate,
    baseline_scores_from_feedback,
    compare_to_baseline,
    evaluate_thresholds,
    format_deltas,
    format_failures,
    latest_baseline,
    scores_from_aggregation,
)

if TYPE_CHECKING:
    import opik
    from opik.evaluation.evaluation_result import EvaluationResult

logger = logging.getLogger(__name__)

# The keys the ritual needs to actually run (agent inference + Opik storage). Absent → skip, exit 0.
REQUIRED_KEYS = ("GEMINI_API_KEY", "OPIK_API_KEY")

# The stable experiment name every regression run shares, so ``get_experiments_by_name`` can find the
# previous run to compare against. Kept distinct from the dataset name (``decode-regression-v1``).
EXPERIMENT_NAME = "decode-regression-gate"

# Slack that absorbs judge noise before a per-metric dip is WARNed as a baseline regression.
BASELINE_TOLERANCE = 0.05


def _missing_keys() -> list[str]:
    """The required keys that are unset/empty — the friendly skip reason names them."""
    return [key for key in REQUIRED_KEYS if not os.environ.get(key)]


@pytest.fixture(scope="session")
def regression_result() -> EvaluationResult:
    """Run the whole probe suite ONCE and hand its Opik result to every gate test (ADR-0017 §6).

    Skips with a clear reason (exit 0) when a required key is absent, so the ritual is safe to invoke
    on a machine without eval credentials. Session-scoped so the money-costing agent runs happen once.
    """
    missing = _missing_keys()
    if missing:
        pytest.skip(f"regression gate needs {', '.join(missing)} — skipping (exit 0).")

    from evals.harness.regression import run_regression

    return run_regression(experiment_name=EXPERIMENT_NAME)


def test_regression_meets_absolute_thresholds(regression_result: EvaluationResult) -> None:
    """The hard gate: every graded metric clears its floor (tool-discipline ≥ 0.8, judges ≥ 0.7)."""
    aggregated = regression_result.aggregate_evaluation_scores()
    scores = scores_from_aggregation(aggregated.aggregated_scores)
    report = evaluate_thresholds(scores)

    assert not report.empty, (
        "no metrics were graded — the probe suite did not run any probe; "
        "check probe registration and that the run actually executed."
    )
    assert report.passed, format_failures(report)


def test_baseline_compare_surfaces_deltas(regression_result: EvaluationResult) -> None:
    """The soft signal: WARN on per-metric regressions vs the last experiment, never fail (§6)."""
    import opik

    scores = scores_from_aggregation(
        regression_result.aggregate_evaluation_scores().aggregated_scores
    )
    baseline = _load_baseline(
        opik.Opik(), experiment_name=EXPERIMENT_NAME, current_id=regression_result.experiment_id
    )

    if baseline is None:
        _warn(f"no prior '{EXPERIMENT_NAME}' experiment with scores — baseline compare skipped.")
        return

    deltas = compare_to_baseline(scores, baseline.scores, tolerance=BASELINE_TOLERANCE)
    _warn(format_deltas(deltas))
    regressions = [delta for delta in deltas if delta.regressed]
    if regressions:
        _warn(
            "per-metric regressions vs baseline (absolute thresholds remain the hard gate): "
            + ", ".join(f"{d.name} {d.delta:+.3f}" for d in regressions)
        )


def _load_baseline(
    client: opik.Opik, *, experiment_name: str, current_id: str
) -> BaselineCandidate | None:
    """Fetch prior experiments of ``experiment_name`` and pick the newest scored one (not this run).

    Thin Opik glue around the pure :func:`latest_baseline`: it excludes the current run by id, reads
    each candidate's creation time + feedback-score averages, and lets the pure helper choose. Any Opik
    hiccup is swallowed to a ``None`` baseline (a soft signal must never break the gate).
    """
    try:
        experiments = client.get_experiments_by_name(experiment_name)
    except Exception as exc:  # a baseline lookup failure must not fail the gate
        logger.warning("[eval] baseline lookup failed: %s", exc)
        return None

    candidates: list[BaselineCandidate] = []
    for experiment in experiments:
        if experiment.id == current_id:
            continue
        try:
            data = experiment.get_experiment_data()
            candidates.append(
                BaselineCandidate(
                    experiment_id=experiment.id,
                    created_at=data.created_at,
                    scores=baseline_scores_from_feedback(data.feedback_scores or []),
                )
            )
        except Exception as exc:  # skip a candidate we cannot read, keep comparing the rest
            logger.warning("[eval] could not read experiment %s: %s", experiment.id, exc)
    return latest_baseline(candidates)


def _warn(message: str) -> None:
    """Emit a soft WARN that shows in pytest's summary without failing (``filterwarnings=error`` safe).

    ``pyproject`` turns warnings into errors, so a plain ``warnings.warn`` would FAIL the gate. Resetting
    the filter to ``always`` inside a saved-and-restored block emits the warning (pytest records it in
    its warnings summary) instead of raising — exactly the "WARN, do not fail" the baseline compare wants.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn(message, stacklevel=2)
