"""Offline unit tests for the regression threshold gate's pure logic (ADR-0017 §6; task 115).

The pytest ritual module (``evals/regression/test_thresholds.py``) stays thin: it runs the suite and
delegates every judgement to the pure helpers here. These tests exercise those helpers with crafted
score dicts — no Opik, no keys, no agent run — so the gate's contract is proven in ``make ci``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from evals.regression.thresholds import (
    DEFAULT_THRESHOLD,
    JUDGE_METRIC_NAME,
    JUDGE_METRIC_THRESHOLD,
    THRESHOLDS,
    TOOL_DISCIPLINE_THRESHOLD,
    BaselineCandidate,
    baseline_scores_from_feedback,
    compare_to_baseline,
    evaluate_thresholds,
    format_deltas,
    format_failures,
    latest_baseline,
    scores_by_tier,
    scores_from_aggregation,
    threshold_for,
)


class _Stat:
    """A stand-in for opik's ``ScoreStatistics`` — only ``.mean`` is read by the extractor."""

    def __init__(self, mean: float) -> None:
        self.mean = mean


class _Feedback:
    """A stand-in for opik's ``FeedbackScoreAveragePublic`` (``.name`` / ``.value``)."""

    def __init__(self, name: str, value: float) -> None:
        self.name = name
        self.value = value


def test_threshold_for_uses_default_for_code_metrics() -> None:
    """A mechanical tool-discipline metric falls back to the discipline floor."""
    assert threshold_for("tool_called_read") == TOOL_DISCIPLINE_THRESHOLD
    assert threshold_for("tool_called_read") == DEFAULT_THRESHOLD


def test_threshold_for_uses_judge_floor_for_the_judge_metric() -> None:
    """Every GEval judge reports under one name, held to the lower judge floor."""
    assert threshold_for(JUDGE_METRIC_NAME) == JUDGE_METRIC_THRESHOLD
    assert THRESHOLDS[JUDGE_METRIC_NAME] == JUDGE_METRIC_THRESHOLD
    assert JUDGE_METRIC_THRESHOLD < DEFAULT_THRESHOLD  # judges sit below the discipline floor


def test_evaluate_thresholds_passes_when_every_metric_clears_its_floor() -> None:
    """All-green scores → a report that passed with a gate per metric."""
    report = evaluate_thresholds(
        {"tool_called_read": 1.0, "max_steps": 0.9, JUDGE_METRIC_NAME: 0.75}
    )

    assert report.passed
    assert not report.failures
    assert {gate.name for gate in report.gates} == {
        "tool_called_read",
        "max_steps",
        JUDGE_METRIC_NAME,
    }


def test_evaluate_thresholds_fails_a_code_metric_below_its_floor() -> None:
    """A discipline metric under 0.8 fails the gate and lands in ``failures``."""
    report = evaluate_thresholds({"tool_called_read": 0.5, JUDGE_METRIC_NAME: 0.9})

    assert not report.passed
    assert [gate.name for gate in report.failures] == ["tool_called_read"]


def test_evaluate_thresholds_holds_judges_to_the_lower_floor() -> None:
    """A judge at 0.72 passes (≥ 0.7) where a code metric at 0.72 would fail (< 0.8)."""
    report = evaluate_thresholds({JUDGE_METRIC_NAME: 0.72, "tool_called_read": 0.72})

    failing = {gate.name for gate in report.failures}
    assert failing == {"tool_called_read"}


def test_evaluate_thresholds_boundary_is_inclusive() -> None:
    """A score exactly at the threshold passes — the gate is ``>=``, not ``>``."""
    report = evaluate_thresholds(
        {"tool_called_read": TOOL_DISCIPLINE_THRESHOLD, JUDGE_METRIC_NAME: JUDGE_METRIC_THRESHOLD}
    )

    assert report.passed


def test_evaluate_thresholds_on_no_scores_is_empty_and_vacuously_passes() -> None:
    """No graded metrics → an empty report; the ritual asserts non-empty separately."""
    report = evaluate_thresholds({})

    assert report.empty
    assert report.passed  # vacuous — nothing graded means nothing failed
    assert not report.gates


def test_absent_metric_is_never_gated() -> None:
    """A metric missing from the scores (e.g. skip-guarded probe 12) is simply not asserted."""
    # probe 12's ``tool_called_echo`` never ran, so it is absent from the aggregation.
    report = evaluate_thresholds({"tool_called_read": 1.0})

    assert [gate.name for gate in report.gates] == ["tool_called_read"]
    assert all(gate.name != "tool_called_echo" for gate in report.gates)


def test_scores_from_aggregation_reads_the_means() -> None:
    """The extractor collapses opik's per-metric statistics to a flat ``{name: mean}`` dict."""
    aggregated = {"tool_called_read": _Stat(1.0), JUDGE_METRIC_NAME: _Stat(0.66)}

    scores = scores_from_aggregation(aggregated)

    assert scores == {"tool_called_read": 1.0, JUDGE_METRIC_NAME: 0.66}


def test_baseline_scores_from_feedback_reads_name_and_value() -> None:
    """A baseline experiment's feedback-score averages flatten to ``{name: value}``."""
    feedback = [_Feedback("tool_called_read", 0.9), _Feedback(JUDGE_METRIC_NAME, 0.7)]

    scores = baseline_scores_from_feedback(feedback)

    assert scores == {"tool_called_read": 0.9, JUDGE_METRIC_NAME: 0.7}


def test_compare_to_baseline_flags_only_regressions() -> None:
    """A metric that dropped vs baseline is a regression; an equal or improved one is not."""
    current = {"a": 0.6, "b": 0.9, "c": 1.0}
    baseline = {"a": 0.8, "b": 0.9, "c": 0.5}

    deltas = compare_to_baseline(current, baseline)
    regressed = {delta.name for delta in deltas if delta.regressed}

    assert regressed == {"a"}
    a = next(delta for delta in deltas if delta.name == "a")
    assert a.delta == pytest.approx(-0.2)


def test_compare_to_baseline_skips_metrics_absent_from_baseline() -> None:
    """A brand-new metric has no baseline to compare against — it is skipped, not flagged."""
    deltas = compare_to_baseline({"new_metric": 0.3, "shared": 0.9}, {"shared": 0.9})

    assert {delta.name for delta in deltas} == {"shared"}


def test_compare_to_baseline_tolerance_absorbs_noise() -> None:
    """A tiny dip within tolerance is not counted a regression (judge noise, ADR-0017 §6)."""
    deltas = compare_to_baseline({"g": 0.68}, {"g": 0.70}, tolerance=0.05)

    assert not any(delta.regressed for delta in deltas)


def test_latest_baseline_picks_the_most_recent_scored_candidate() -> None:
    """The baseline is the newest prior experiment that actually carries scores."""
    now = datetime.now(UTC)
    older = BaselineCandidate("old", now - timedelta(days=2), {"a": 0.8})
    newer = BaselineCandidate("new", now - timedelta(days=1), {"a": 0.9})

    assert latest_baseline([older, newer]) is newer


def test_latest_baseline_ignores_candidates_without_scores() -> None:
    """An experiment with no feedback scores (a crashed/empty run) is not a usable baseline."""
    now = datetime.now(UTC)
    empty_recent = BaselineCandidate("empty", now, {})
    scored_older = BaselineCandidate("scored", now - timedelta(days=1), {"a": 0.8})

    assert latest_baseline([empty_recent, scored_older]) is scored_older


def test_latest_baseline_handles_mixed_none_and_datetime_created_at() -> None:
    """A ``created_at=None`` candidate (opik's ``Optional[datetime]``) must not crash the newest pick.

    The Tester's exact repro: mixing an undated candidate with a dated one previously raised a
    ``TypeError`` inside ``max`` — an unhandled crash of the gate. The dated candidate wins regardless
    of ordering, and no comparison of ``None`` against a ``datetime`` ever happens.
    """
    now = datetime.now(UTC)
    undated = BaselineCandidate("undated", None, {"a": 0.7})
    dated = BaselineCandidate("dated", now, {"a": 0.9})

    assert latest_baseline([undated, dated]) is dated
    assert latest_baseline([dated, undated]) is dated  # order must not matter


def test_latest_baseline_all_undated_returns_a_candidate_without_crashing() -> None:
    """All candidates lack a timestamp but carry scores → best-effort return, never a crash."""
    first = BaselineCandidate("first", None, {"x": 0.8})
    second = BaselineCandidate("second", None, {"x": 0.9})

    assert latest_baseline([first, second]) is second


def test_latest_baseline_is_none_when_no_scored_candidate_exists() -> None:
    """No prior scored experiment → no baseline (the ritual warns and moves on)."""
    assert latest_baseline([]) is None
    assert latest_baseline([BaselineCandidate("empty", datetime.now(UTC), {})]) is None


def test_format_failures_names_each_failing_metric() -> None:
    """The assertion message lists every failing metric with its value and floor."""
    report = evaluate_thresholds({"tool_called_read": 0.5})

    message = format_failures(report)

    assert "tool_called_read" in message
    assert "0.5" in message
    assert str(TOOL_DISCIPLINE_THRESHOLD) in message


def test_format_deltas_marks_regressions() -> None:
    """The warn message flags the regressed metric distinctly from an improvement."""
    deltas = compare_to_baseline({"a": 0.6, "b": 1.0}, {"a": 0.8, "b": 0.9})

    message = format_deltas(deltas)

    assert "a" in message
    assert "b" in message


class _Score:
    """A stand-in for opik's ``ScoreResult`` — the fields the per-tier report reads."""

    def __init__(self, name: str, value: float, scoring_failed: bool = False) -> None:
        self.name = name
        self.value = value
        self.scoring_failed = scoring_failed


class _Case:
    """A stand-in for opik's ``TestCase`` — the item content carries the case id."""

    def __init__(self, case_id: str | None) -> None:
        self.dataset_item_content = {"case_id": case_id} if case_id else {}
        self.task_output: dict[str, object] = {}


class _Result:
    """A stand-in for opik's ``TestResult`` — one item's case plus its scores."""

    def __init__(self, case_id: str | None, scores: list[_Score]) -> None:
        self.test_case = _Case(case_id)
        self.score_results = scores


_TIERS = {"01-read-vs-cat": "easy", "11-step-efficiency": "easy", "17-grounded-answer": "hard"}


def test_scores_by_tier_averages_each_metric_within_its_tier() -> None:
    """The per-tier report: one mean per metric per tier, over a hand-built result matrix (§8)."""
    results = [
        _Result("01-read-vs-cat", [_Score("tool_called_read", 1.0), _Score("max_steps", 1.0)]),
        _Result("11-step-efficiency", [_Score("max_steps", 0.0)]),
        _Result("17-grounded-answer", [_Score(JUDGE_METRIC_NAME, 0.75)]),
    ]

    by_tier = scores_by_tier(results, _TIERS)

    assert by_tier == {
        "easy": {"tool_called_read": 1.0, "max_steps": 0.5},
        "hard": {JUDGE_METRIC_NAME: 0.75},
    }


def test_scores_by_tier_ignores_a_metric_that_failed_to_score() -> None:
    """A metric that CRASHED scored nothing; folding its 0.0 in would read as a regression."""
    results = [
        _Result("01-read-vs-cat", [_Score("max_steps", 1.0)]),
        _Result("11-step-efficiency", [_Score("max_steps", 0.0, scoring_failed=True)]),
    ]

    assert scores_by_tier(results, _TIERS) == {"easy": {"max_steps": 1.0}}


def test_scores_by_tier_skips_items_with_no_known_tier() -> None:
    """An unknown / missing case id has no tier to report under — it is left out, never mis-filed."""
    results = [
        _Result(None, [_Score("max_steps", 1.0)]),
        _Result("99-not-a-case", [_Score("max_steps", 0.0)]),
        _Result("17-grounded-answer", [_Score("max_steps", 1.0)]),
    ]

    assert scores_by_tier(results, _TIERS) == {"hard": {"max_steps": 1.0}}


def test_scores_by_tier_of_an_empty_run_is_empty() -> None:
    """No results, no report — the ritual asserts the GLOBAL gate is non-empty, not this."""
    assert scores_by_tier([], _TIERS) == {}
