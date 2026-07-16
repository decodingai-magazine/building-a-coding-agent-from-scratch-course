"""Trial aggregation over an Opik ``evaluate()`` result — pass@k, pass^k, flakiness, $/success (§8).

ADR-0017 §8 asks for ``--trials k`` runs whose per-item reliability and cost-normalized success ride
``experiment_scoring_functions`` so they land on the experiment row. The installed ``opik==1.9.8``
``evaluate()`` HAS ``trial_count`` but NO ``experiment_scoring_functions`` param (nearest is
``scoring_functions``, a per-item ``ScorerFunctionProtocol`` — the wrong axis for a cross-trial
aggregate). So this module takes the honest 1.9.8 route: the aggregates are **pure functions computed
post-hoc over the ``EvaluationResult`` (its ``TestResult`` list)**, printed as a Rich summary table by
``python -m evals benchmark``, and attached to the experiment by logging them as **feedback scores on
the experiment's traces** (:func:`attach_experiment_aggregates`) — where Opik's own per-experiment
averaging turns each per-item score into the suite-level number, sortable/filterable in the UI. This
adaptation is recorded in the task-107 log.

The math is deliberately separable from Opik: :func:`pass_at_1` / :func:`pass_at_k` /
:func:`pass_hat_k` / :func:`is_flaky` operate on a plain ``Sequence[bool]`` (one task's trial passes),
:func:`summarize_outcomes` on a ``{item_id: [TrialOutcome, ...]}`` matrix — both unit-testable on
hand-built matrices, and both graceful on the empty/degenerate cases (never raise). The Opik seam is
only :func:`extract_trial_outcomes` (reads the ``verify_oracle`` score + recorded usage off each
``TestResult``) and :func:`summarize` / :func:`attach_experiment_aggregates`.

A trial "passed" iff its ``verify_oracle`` score (the hidden-oracle metric, task 104) is ``1.0``.
Cost is dollar cost per trial when the task-fn payload records one (``cost_usd`` / ``total_cost`` —
e.g. copied from an Opik trace ``total_cost``); with no dollar figure the driver still records tokens,
so ``success_per_dollar`` reports ``None`` and the table falls back to tokens-only (ADR-0017 §8).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import opik
    from rich.table import Table

logger = logging.getLogger(__name__)

# The metric whose 1.0 score means "this trial solved the task" — the hidden oracle (task 104).
DEFAULT_PASS_METRIC = "verify_oracle"
# A ScoreResult at or above this counts as a pass (the code oracles emit exactly 0.0 / 1.0).
PASS_THRESHOLD = 1.0


@dataclass(frozen=True, slots=True)
class TrialOutcome:
    """One trial's graded result: did it pass, plus the usage the cost aggregates read.

    ``cost_usd`` is ``None`` when no dollar figure was recorded (the tokens-only fallback); ``tokens``
    is the run's total (input + output) request tokens from the task-fn payload.
    """

    passed: bool
    tokens: int = 0
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class TaskAggregate:
    """One dataset item's aggregate over its ``k`` trials (ADR-0017 §8)."""

    dataset_item_id: str
    task_id: str | None
    trials: int
    passes: int
    pass_at_1: float
    pass_at_k: float
    pass_hat_k: float
    is_flaky: bool
    mean_tokens: float
    mean_cost_usd: float | None


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    """The whole run's reliability + cost picture: per-task rows and the suite-level means.

    Suite ``pass_at_1`` / ``pass_at_k`` / ``pass_hat_k`` are the means of the per-task values;
    ``flakiness_rate`` is the fraction of tasks that partially passed; ``success_per_dollar`` and
    ``mean_cost_usd`` are ``None`` when no dollar costs were recorded (tokens-only fallback).
    """

    trials: int
    per_task: list[TaskAggregate] = field(default_factory=list)
    pass_at_1: float = 0.0
    pass_at_k: float = 0.0
    pass_hat_k: float = 0.0
    flakiness_rate: float = 0.0
    success_per_dollar: float | None = None
    mean_cost_usd: float | None = None
    mean_tokens: float = 0.0
    total_successes: int = 0
    total_trials: int = 0
    total_tasks: int = 0


# --- Core reliability math (pure, over one task's trial passes) ---


def pass_at_1(trials: Sequence[bool]) -> float:
    """Mean pass rate across the trials — ``0.0`` for an empty run (never divides by zero)."""
    if not trials:
        return 0.0
    return sum(1 for passed in trials if passed) / len(trials)


def pass_at_k(trials: Sequence[bool]) -> float:
    """``1.0`` iff at least one trial passed — "can it ever do the task?"."""
    return 1.0 if any(trials) else 0.0


def pass_hat_k(trials: Sequence[bool]) -> float:
    """``1.0`` iff EVERY trial passed — the reliability bar; empty is not vacuously reliable."""
    return 1.0 if trials and all(trials) else 0.0


def is_flaky(trials: Sequence[bool]) -> bool:
    """True when the task passed some but not all trials (``0 < passes < k``)."""
    passes = sum(1 for passed in trials if passed)
    return 0 < passes < len(trials)


# --- Suite summary over a hand-built / extracted trial matrix ---


def summarize_outcomes(
    outcomes: Mapping[str, Sequence[TrialOutcome]],
    *,
    trials: int,
    task_ids: Mapping[str, str | None] | None = None,
) -> BenchmarkSummary:
    """Fold a ``{item_id: [TrialOutcome, ...]}`` matrix into a :class:`BenchmarkSummary` (ADR-0017 §8).

    Pure and total: an empty matrix, a ``k=1`` degenerate run, or an all-fail suite each yield a
    valid summary rather than raising. ``success_per_dollar`` / ``mean_cost_usd`` are reported only
    when every trial carried a dollar cost — otherwise the run is tokens-only.
    """
    task_ids = task_ids or {}
    per_task = [
        _aggregate_task(item_id, item_trials, task_ids.get(item_id))
        for item_id, item_trials in outcomes.items()
    ]
    all_trials = [trial for item_trials in outcomes.values() for trial in item_trials]
    total_successes = sum(1 for trial in all_trials if trial.passed)
    costs = [trial.cost_usd for trial in all_trials]
    total_cost = sum(cost for cost in costs if cost is not None)
    have_costs = bool(costs) and all(cost is not None for cost in costs)

    return BenchmarkSummary(
        trials=trials,
        per_task=per_task,
        pass_at_1=_mean(agg.pass_at_1 for agg in per_task),
        pass_at_k=_mean(agg.pass_at_k for agg in per_task),
        pass_hat_k=_mean(agg.pass_hat_k for agg in per_task),
        flakiness_rate=_mean(1.0 if agg.is_flaky else 0.0 for agg in per_task),
        success_per_dollar=(
            total_successes / total_cost if have_costs and total_cost > 0 else None
        ),
        mean_cost_usd=(total_cost / len(all_trials) if have_costs and all_trials else None),
        mean_tokens=_mean(trial.tokens for trial in all_trials),
        total_successes=total_successes,
        total_trials=len(all_trials),
        total_tasks=len(per_task),
    )


def _aggregate_task(
    item_id: str, item_trials: Sequence[TrialOutcome], task_id: str | None
) -> TaskAggregate:
    passes = [trial.passed for trial in item_trials]
    costs = [trial.cost_usd for trial in item_trials]
    have_costs = bool(costs) and all(cost is not None for cost in costs)
    return TaskAggregate(
        dataset_item_id=item_id,
        task_id=task_id,
        trials=len(item_trials),
        passes=sum(1 for passed in passes if passed),
        pass_at_1=pass_at_1(passes),
        pass_at_k=pass_at_k(passes),
        pass_hat_k=pass_hat_k(passes),
        is_flaky=is_flaky(passes),
        mean_tokens=_mean(trial.tokens for trial in item_trials),
        mean_cost_usd=(
            sum(cost for cost in costs if cost is not None) / len(costs)
            if have_costs and costs
            else None
        ),
    )


def _mean(values: Any) -> float:
    """Arithmetic mean of an iterable of numbers — ``0.0`` when empty (never divides by zero)."""
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


# --- The Opik seam: extract a matrix off an EvaluationResult, then summarize ---


def summarize(
    result: Any, *, trials: int, pass_metric: str = DEFAULT_PASS_METRIC
) -> BenchmarkSummary:
    """Summarize an Opik ``EvaluationResult`` over its trials (ADR-0017 §8).

    Reads the ``verify_oracle`` pass signal and recorded usage off each ``TestResult``, groups them
    by dataset item, and folds the matrix. Graceful on a malformed result (a non-list
    ``test_results`` yields an empty summary rather than raising).
    """
    outcomes, task_ids = extract_trial_outcomes(result, pass_metric=pass_metric)
    return summarize_outcomes(outcomes, trials=trials, task_ids=task_ids)


def extract_trial_outcomes(
    result: Any, *, pass_metric: str = DEFAULT_PASS_METRIC
) -> tuple[dict[str, list[TrialOutcome]], dict[str, str | None]]:
    """Group an ``EvaluationResult``'s trials into a matrix + a per-item ``task_id`` map.

    Returns ``({item_id: [TrialOutcome, ...]}, {item_id: task_id})``. Never raises: a ``test_results``
    that is not a list/tuple (a mock, a partial result) yields empty maps.
    """
    outcomes: dict[str, list[TrialOutcome]] = {}
    task_ids: dict[str, str | None] = {}
    for test_result in _test_results(result):
        test_case = getattr(test_result, "test_case", None)
        item_id = getattr(test_case, "dataset_item_id", None)
        if item_id is None:
            continue
        outcomes.setdefault(item_id, []).append(_outcome(test_result, pass_metric))
        task_ids.setdefault(item_id, _task_id(test_case))
    return outcomes, task_ids


def _test_results(result: Any) -> list[Any]:
    """The ``test_results`` list off a result object, or ``[]`` for a malformed / mock result."""
    test_results = getattr(result, "test_results", None)
    return list(test_results) if isinstance(test_results, (list, tuple)) else []


def _outcome(test_result: Any, pass_metric: str) -> TrialOutcome:
    passed = _scored_pass(getattr(test_result, "score_results", None), pass_metric)
    task_output = getattr(getattr(test_result, "test_case", None), "task_output", None) or {}
    return TrialOutcome(
        passed=passed,
        tokens=_tokens(task_output),
        cost_usd=_cost(task_output),
    )


def _scored_pass(score_results: Any, pass_metric: str) -> bool:
    """True iff a ``pass_metric`` score of ``>= PASS_THRESHOLD`` is present (a missing score fails)."""
    if not isinstance(score_results, (list, tuple)):
        return False
    for score in score_results:
        if getattr(score, "name", None) != pass_metric or getattr(score, "scoring_failed", False):
            continue
        value = getattr(score, "value", None)
        if isinstance(value, (int, float)) and value >= PASS_THRESHOLD:
            return True
    return False


def _tokens(task_output: Mapping[str, Any]) -> int:
    """Total request tokens (input + output) recorded on the task-fn payload, ``0`` if absent."""
    return _as_int(task_output.get("input_tokens")) + _as_int(task_output.get("output_tokens"))


def _cost(task_output: Mapping[str, Any]) -> float | None:
    """The trial's dollar cost from the payload (``cost_usd`` / ``total_cost``), else ``None``."""
    for key in ("cost_usd", "total_cost"):
        value = task_output.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _task_id(test_case: Any) -> str | None:
    content = getattr(test_case, "dataset_item_content", None)
    return content.get("task_id") if isinstance(content, Mapping) else None


def _as_int(value: Any) -> int:
    """A non-negative int coercion that treats anything non-integer as ``0`` (bools excluded)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# --- Rendering + experiment attach ---


def render_summary_table(summary: BenchmarkSummary) -> Table:
    """A Rich table of per-task pass rates + cost and the suite footer (``python -m evals benchmark``).

    The pass@k / pass^k columns are labelled with the actual ``k`` so a ``--trials 3`` run reads
    ``pass@3`` / ``pass^3``. Costs show ``tokens-only`` when no dollar figure was recorded.
    """
    from rich.table import Table

    k = summary.trials
    table = Table(title=f"decode benchmark — {summary.total_tasks} task(s) x {k} trial(s)")
    table.add_column("task")
    table.add_column("pass@1", justify="right")
    table.add_column(f"pass@{k}", justify="right")
    table.add_column(f"pass^{k}", justify="right")
    table.add_column("flaky", justify="center")
    table.add_column("~tokens", justify="right")
    table.add_column("~$", justify="right")

    for agg in summary.per_task:
        table.add_row(
            agg.task_id or agg.dataset_item_id,
            f"{agg.pass_at_1:.2f}",
            f"{agg.pass_at_k:.2f}",
            f"{agg.pass_hat_k:.2f}",
            "yes" if agg.is_flaky else "",
            f"{agg.mean_tokens:.0f}",
            _cost_cell(agg.mean_cost_usd),
        )

    table.add_section()
    table.add_row(
        "SUITE",
        f"{summary.pass_at_1:.2f}",
        f"{summary.pass_at_k:.2f}",
        f"{summary.pass_hat_k:.2f}",
        f"{summary.flakiness_rate:.0%}",
        f"{summary.mean_tokens:.0f}",
        _success_per_dollar_cell(summary),
    )
    return table


def _cost_cell(cost: float | None) -> str:
    """A per-task cost cell: a dollar figure, or ``tokens-only`` when none was recorded."""
    return f"${cost:.4f}" if cost is not None else "tokens-only"


def _success_per_dollar_cell(summary: BenchmarkSummary) -> str:
    """The suite cost footer: success-per-dollar, or the tokens-only fallback marker (ADR-0017 §8)."""
    if summary.success_per_dollar is None:
        return "tokens-only"
    return f"{summary.success_per_dollar:.1f}/$"


def attach_experiment_aggregates(
    client: opik.Opik,
    result: Any,
    summary: BenchmarkSummary,
    *,
    project_name: str | None = None,
) -> None:
    """Log the per-item aggregates as feedback scores on the experiment's traces (ADR-0017 §8).

    The 1.9.8 stand-in for ``experiment_scoring_functions``: each dataset item's ``pass_at_1`` /
    ``pass_at_k`` / ``pass_hat_k`` / ``flaky`` scores are written onto every one of that item's trial
    traces, so Opik's per-experiment averaging surfaces the suite-level numbers on the experiment row.
    A no-op when the run produced no traces (a malformed / empty result).
    """
    by_item = {agg.dataset_item_id: agg for agg in summary.per_task}
    trace_ids = _trace_ids_by_item(result)
    scores = [
        score
        for item_id, agg in by_item.items()
        for trace_id in trace_ids.get(item_id, [])
        for score in _feedback_scores(trace_id, agg)
    ]
    if not scores:
        return
    client.log_traces_feedback_scores(scores, project_name=project_name)


def _trace_ids_by_item(result: Any) -> dict[str, list[str]]:
    """Map each dataset item id to the trace ids of its trials (skips traces with no id)."""
    trace_ids: dict[str, list[str]] = {}
    for test_result in _test_results(result):
        test_case = getattr(test_result, "test_case", None)
        item_id = getattr(test_case, "dataset_item_id", None)
        trace_id = getattr(test_case, "trace_id", None)
        if item_id is None or not trace_id:
            continue
        trace_ids.setdefault(item_id, []).append(trace_id)
    return trace_ids


def _feedback_scores(trace_id: str, agg: TaskAggregate) -> list[dict[str, Any]]:
    """The four derived feedback scores for one trace (Opik ``FeedbackScoreDict`` shape)."""
    return [
        {"id": trace_id, "name": "pass_at_1", "value": agg.pass_at_1},
        {"id": trace_id, "name": "pass_at_k", "value": agg.pass_at_k},
        {"id": trace_id, "name": "pass_hat_k", "value": agg.pass_hat_k},
        {"id": trace_id, "name": "flaky", "value": 1.0 if agg.is_flaky else 0.0},
    ]
