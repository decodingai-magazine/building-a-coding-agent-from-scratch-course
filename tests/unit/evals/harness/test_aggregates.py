"""Offline tests for the trial aggregates (ADR-0017 §8; task 107).

Two layers, both pure and keyless: the reliability math (``pass@1`` / ``pass@k`` / ``pass^k`` /
flakiness / cost-per-success) is exercised on hand-built trial matrices — all-pass, all-fail, mixed,
``k=1`` degenerate, empty — proving it NEVER raises on a missing or empty score; the Opik adapter
(:func:`summarize`, :func:`attach_experiment_aggregates`) is exercised on real ``TestResult`` objects
and a mock ``Opik`` client, proving it groups trials by dataset item and lands the derived scores on
the experiment's traces (the 1.9.8 stand-in for the removed ``experiment_scoring_functions``).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from opik.evaluation.metrics.score_result import ScoreResult
from opik.evaluation.test_case import TestCase as OpikTestCase
from opik.evaluation.test_result import TestResult as OpikTestResult
from rich.console import Console

from evals.harness.aggregates import (
    BenchmarkSummary,
    TrialOutcome,
    attach_experiment_aggregates,
    is_flaky,
    pass_at_1,
    pass_at_k,
    pass_hat_k,
    render_summary_table,
    summarize,
    summarize_outcomes,
)


@dataclass
class _FakeResult:
    """The one field the aggregate adapter reads off an Opik ``EvaluationResult``."""

    test_results: Any


def _test_result(
    item_id: str,
    trial_id: int,
    *,
    passed: bool,
    task_output: dict | None = None,
    task_id: str = "t",
) -> OpikTestResult:
    """A real Opik ``TestResult`` carrying one ``verify_oracle`` score — the pass signal."""
    return OpikTestResult(
        test_case=OpikTestCase(
            trace_id=f"trace-{item_id}-{trial_id}",
            dataset_item_id=item_id,
            scoring_inputs={},
            task_output=task_output or {},
            dataset_item_content={"task_id": task_id},
        ),
        score_results=[ScoreResult(name="verify_oracle", value=1.0 if passed else 0.0)],
        trial_id=trial_id,
    )


# --- Core math on hand-built boolean trial matrices (ADR-0017 §8) ---


def test_pass_at_1_is_the_mean_of_trial_passes():
    assert pass_at_1([True, True, True]) == 1.0
    assert pass_at_1([False, False]) == 0.0
    assert pass_at_1([True, False, True, False]) == 0.5
    assert pass_at_1([True]) == 1.0  # k=1 degenerate
    assert pass_at_1([]) == 0.0  # empty never raises


def test_pass_at_k_is_one_when_any_trial_passed():
    assert pass_at_k([False, False, True]) == 1.0
    assert pass_at_k([True, True]) == 1.0
    assert pass_at_k([False, False]) == 0.0
    assert pass_at_k([True]) == 1.0  # k=1
    assert pass_at_k([]) == 0.0  # empty never raises


def test_pass_hat_k_is_one_only_when_all_trials_passed():
    assert pass_hat_k([True, True, True]) == 1.0
    assert pass_hat_k([True, False, True]) == 0.0  # one flake fails the reliability bar
    assert pass_hat_k([True]) == 1.0  # k=1
    assert pass_hat_k([]) == 0.0  # empty is NOT vacuously reliable


def test_is_flaky_is_a_partial_pass():
    assert is_flaky([True, False, True]) is True
    assert is_flaky([True, True]) is False  # reliably passes
    assert is_flaky([False, False]) is False  # reliably fails
    assert is_flaky([True]) is False  # k=1 cannot be flaky
    assert is_flaky([]) is False  # empty never raises


# --- Suite summary over hand-built matrices ---


def _outcomes(*passes: bool, tokens: int = 100, cost: float | None = None) -> list[TrialOutcome]:
    return [TrialOutcome(passed=p, tokens=tokens, cost_usd=cost) for p in passes]


def test_summarize_all_pass_suite_is_perfectly_reliable():
    matrix = {"a": _outcomes(True, True, True), "b": _outcomes(True, True, True)}

    summary = summarize_outcomes(matrix, trials=3)

    assert summary.pass_at_1 == 1.0
    assert summary.pass_at_k == 1.0
    assert summary.pass_hat_k == 1.0
    assert summary.flakiness_rate == 0.0
    assert summary.total_tasks == 2
    assert summary.total_successes == 6


def test_summarize_all_fail_suite_scores_zero_everywhere():
    matrix = {"a": _outcomes(False, False), "b": _outcomes(False, False)}

    summary = summarize_outcomes(matrix, trials=2)

    assert summary.pass_at_1 == 0.0
    assert summary.pass_at_k == 0.0
    assert summary.pass_hat_k == 0.0
    assert summary.flakiness_rate == 0.0  # a reliable fail is not flaky
    assert summary.total_successes == 0


def test_summarize_mixed_suite_tracks_flakiness_and_reliability():
    matrix = {
        "reliable": _outcomes(True, True, True),
        "flaky": _outcomes(True, False, True),
        "broken": _outcomes(False, False, False),
    }

    summary = summarize_outcomes(matrix, trials=3)

    # pass@k: reliable + flaky solved at least once → 2/3.
    assert summary.pass_at_k == 2 / 3
    # pass^k: only "reliable" passed all 3 → 1/3.
    assert summary.pass_hat_k == 1 / 3
    # flakiness: exactly one of three tasks is a partial pass.
    assert summary.flakiness_rate == 1 / 3


def test_summarize_empty_suite_never_raises_and_is_all_zero():
    summary = summarize_outcomes({}, trials=3)

    assert isinstance(summary, BenchmarkSummary)
    assert summary.pass_at_1 == 0.0
    assert summary.pass_at_k == 0.0
    assert summary.pass_hat_k == 0.0
    assert summary.flakiness_rate == 0.0
    assert summary.success_per_dollar is None
    assert summary.mean_cost_usd is None
    assert summary.total_tasks == 0


def test_summarize_k1_degenerate_matches_pass_at_1():
    matrix = {"a": _outcomes(True), "b": _outcomes(False)}

    summary = summarize_outcomes(matrix, trials=1)

    assert summary.pass_at_1 == 0.5
    assert summary.pass_at_k == 0.5  # identical to pass@1 at k=1
    assert summary.pass_hat_k == 0.5
    assert summary.flakiness_rate == 0.0


def test_summarize_computes_cost_per_success_when_dollar_costs_are_present():
    matrix = {
        "a": _outcomes(True, False, cost=0.01, tokens=200),
        "b": _outcomes(True, True, cost=0.02, tokens=400),
    }

    summary = summarize_outcomes(matrix, trials=2)

    # 3 passing trials over $0.06 total spend.
    assert summary.success_per_dollar == 3 / 0.06
    assert summary.mean_cost_usd is not None
    assert summary.mean_tokens == 300.0


def test_summarize_falls_back_to_tokens_only_without_dollar_costs():
    matrix = {"a": _outcomes(True, cost=None, tokens=150)}

    summary = summarize_outcomes(matrix, trials=1)

    assert summary.success_per_dollar is None  # documented tokens-only fallback (ADR-0017 §8)
    assert summary.mean_cost_usd is None
    assert summary.mean_tokens == 150.0


# --- The Opik adapter: real TestResult objects ---


def test_summarize_groups_trials_by_dataset_item():
    result = _FakeResult(
        test_results=[
            _test_result(
                "item-a", 0, passed=True, task_output={"input_tokens": 10, "output_tokens": 5}
            ),
            _test_result(
                "item-a", 1, passed=False, task_output={"input_tokens": 10, "output_tokens": 5}
            ),
            _test_result("item-b", 0, passed=True),
            _test_result("item-b", 1, passed=True),
        ]
    )

    summary = summarize(result, trials=2)

    assert summary.total_tasks == 2
    by_item = {agg.dataset_item_id: agg for agg in summary.per_task}
    assert by_item["item-a"].passes == 1
    assert by_item["item-a"].is_flaky is True
    assert by_item["item-b"].pass_hat_k == 1.0
    assert by_item["item-a"].mean_tokens == 15.0


def test_summarize_treats_a_missing_verify_score_as_a_fail():
    tr = OpikTestResult(
        test_case=OpikTestCase(
            trace_id="t", dataset_item_id="x", scoring_inputs={}, task_output={}
        ),
        score_results=[ScoreResult(name="some_judge", value=1.0)],  # no verify_oracle
        trial_id=0,
    )

    summary = summarize(_FakeResult(test_results=[tr]), trials=1)

    assert summary.total_successes == 0  # graceful: no pass signal → not passed


def test_summarize_is_graceful_when_test_results_is_not_a_list():
    summary = summarize(_FakeResult(test_results=object()), trials=3)

    assert summary.total_tasks == 0  # never raises on a malformed result


# --- Rendering + experiment attach ---


def test_render_summary_table_shows_pass_rates_and_task_rows():
    matrix = {"item-a": _outcomes(True, False, cost=0.01)}
    summary = summarize_outcomes(matrix, trials=2, task_ids={"item-a": "001-greeting"})

    console = Console(file=io.StringIO(), width=200)
    console.print(render_summary_table(summary))
    text = console.file.getvalue()

    assert "001-greeting" in text
    assert "pass@2" in text
    assert "pass^2" in text


def test_attach_logs_derived_scores_onto_the_experiment_traces(mocker):
    result = _FakeResult(
        test_results=[
            _test_result("item-a", 0, passed=True),
            _test_result("item-a", 1, passed=False),
        ]
    )
    summary = summarize(result, trials=2)
    client = mocker.Mock()

    attach_experiment_aggregates(client, result, summary, project_name="decode-evals")

    client.log_traces_feedback_scores.assert_called_once()
    (scores,), kwargs = client.log_traces_feedback_scores.call_args
    assert kwargs["project_name"] == "decode-evals"
    names = {score["name"] for score in scores}
    assert {"pass_at_1", "pass_at_k", "pass_hat_k", "flaky"} <= names
    trace_ids = {score["id"] for score in scores}
    assert trace_ids == {"trace-item-a-0", "trace-item-a-1"}


def test_attach_is_a_no_op_when_there_are_no_traces(mocker):
    client = mocker.Mock()

    attach_experiment_aggregates(
        client, _FakeResult(test_results=object()), summarize_outcomes({}, trials=1)
    )

    client.log_traces_feedback_scores.assert_not_called()
