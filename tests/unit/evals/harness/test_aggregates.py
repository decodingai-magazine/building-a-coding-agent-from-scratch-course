"""Offline tests for the trial aggregates — pure math, no Opik, no keys (ADR-0022 §4,§6).

Every function under test takes plain sequences or the module's own frozen dataclasses, so the whole
file runs on hand-built trial matrices: all-pass, all-fail, mixed, ``k=1`` degenerate, empty, and the
one that matters most — a matrix carrying an ``infra_error`` trial, which must vanish from BOTH the
numerator and the denominator (ADR-0022 §4). The Opik seam moved to ``evals/harness/benchmark.py``
(``test_benchmark.py`` covers it); nothing here imports opik.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

from rich.console import Console

from evals.harness.aggregates import (
    DEFAULT_PASS_METRIC,
    TaskTrials,
    TrialOutcome,
    infra_error_rate,
    is_flaky,
    mean_cost_usd,
    mean_seconds,
    pass_at_1,
    pass_at_k,
    pass_hat_k,
    render_spend_line,
    render_summary_table,
    success_per_dollar,
    summarize_tasks,
    total_seconds,
    total_tokens,
    wall_clock_seconds,
)

ORIGIN = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return ORIGIN + timedelta(seconds=seconds)


def _ok(cost: float | None = None) -> TrialOutcome:
    return TrialOutcome(status="agent_ok", reward=1.0, cost_usd=cost)


def _fail(cost: float | None = None) -> TrialOutcome:
    return TrialOutcome(status="agent_fail", reward=0.0, cost_usd=cost)


def _infra() -> TrialOutcome:
    return TrialOutcome(status="infra_error", reward=None, cost_usd=None)


def _task(task_id: str, difficulty: str, outcomes: list[TrialOutcome]) -> TaskTrials:
    return TaskTrials(task_id=task_id, difficulty=difficulty, outcomes=tuple(outcomes))


# --- the reliability math ---


def test_the_pass_metric_is_the_reward_score():
    """The grade of record is the Verifier's reward, not a judge and not an oracle (ADR-0022 §3)."""
    assert DEFAULT_PASS_METRIC == "reward"


def test_pass_at_1_is_the_mean_of_trial_passes():
    assert pass_at_1([True, True]) == 1.0
    assert pass_at_1([True, False]) == 0.5
    assert pass_at_1([False, False]) == 0.0
    assert pass_at_1([]) == 0.0


def test_pass_at_k_is_any_when_n_equals_k():
    """Harbor's estimator collapses to ``any()`` when every trial is spent on the same k."""
    assert pass_at_k([False, True, False]) == 1.0
    assert pass_at_k([False, False]) == 0.0
    assert pass_at_k([]) == 0.0


def test_pass_at_k_is_the_unbiased_estimator_when_k_is_below_n():
    """n=4, c=2, k=2: 1 - C(2,2)/C(4,2) = 1 - 1/6 — the chance a random pair contains a pass."""
    assert pass_at_k([True, True, False, False], k=2) == 1 - 1 / 6
    assert pass_at_k([True, False, False, False], k=2) == 1 - 3 / 6
    assert pass_at_k([False, False, False, False], k=2) == 0.0
    assert pass_at_k([True, True, True, True], k=2) == 1.0


def test_pass_at_k_clamps_a_k_beyond_the_trials_it_has():
    """A k larger than n cannot be estimated from n trials — fall back to the n-trial answer."""
    assert pass_at_k([True, False], k=5) == 1.0
    assert pass_at_k([False, False], k=5) == 0.0


def test_pass_hat_k_is_one_only_when_all_trials_passed():
    assert pass_hat_k([True, True]) == 1.0
    assert pass_hat_k([True, False]) == 0.0
    assert pass_hat_k([]) == 0.0  # empty is not vacuously reliable


def test_is_flaky_is_a_partial_pass():
    assert is_flaky([True, False]) is True
    assert is_flaky([True, True]) is False
    assert is_flaky([False, False]) is False
    assert is_flaky([]) is False


def test_infra_error_rate_counts_the_harness_failures():
    assert infra_error_rate(["agent_ok", "infra_error"]) == 0.5
    assert infra_error_rate(["agent_ok", "agent_fail"]) == 0.0
    assert infra_error_rate([]) == 0.0


def test_mean_cost_usd_averages_the_reported_costs_only():
    """A trial that reported no cost (an Infra Error never reaches a summary) is not a $0 trial."""
    assert mean_cost_usd([0.02, 0.04]) == 0.03
    assert mean_cost_usd([0.02, None]) == 0.02
    assert mean_cost_usd([None, None]) is None
    assert mean_cost_usd([]) is None


def test_total_tokens_sums_every_trial_including_the_zero_of_one_that_never_ran():
    assert total_tokens([1_000, 0, 250]) == 1_250
    assert total_tokens([]) == 0


def test_total_and_mean_seconds_skip_the_unmeasured_and_are_none_when_nothing_ran():
    """A trial whose phase never ran is not an instant trial (the cost rule, applied to time)."""
    assert total_seconds([60.0, None, 30.0]) == 90.0
    assert mean_seconds([60.0, None, 30.0]) == 45.0
    assert total_seconds([None, None]) is None
    assert mean_seconds([]) is None


def test_wall_clock_is_a_span_from_the_first_start_to_the_last_end():
    """Overlapping trials (a fan-out) share the clock: 0-60 and 30-100 are 100 s, not 130."""
    assert wall_clock_seconds([_at(0), _at(30)], [_at(60), _at(100)]) == 100.0
    assert wall_clock_seconds([_at(0), None], [_at(60), None]) == 60.0
    assert wall_clock_seconds([None], [None]) is None
    assert wall_clock_seconds([_at(0)], [None]) is None  # a lone stamp is not a span
    assert wall_clock_seconds([], []) is None


def test_success_per_dollar_divides_wins_by_the_dollars_spent():
    assert success_per_dollar([True, False], [0.25, 0.25]) == 2.0
    assert success_per_dollar([False, False], [0.25, 0.25]) == 0.0
    assert success_per_dollar([True], [None]) is None  # no dollar figure at all
    assert success_per_dollar([True], [0.0]) is None  # never divide by zero
    assert success_per_dollar([], []) is None


# --- the suite summary ---


def test_summary_excludes_infra_errors_from_numerator_and_denominator():
    """ADR-0022 §4's core rule: a harness failure never flatters and never punishes a pass rate."""
    summary = summarize_tasks([_task("001", "easy", [_ok(), _infra()])], trials=2)

    aggregate = summary.per_task[0]
    assert aggregate.trials == 2
    assert aggregate.graded == 1
    assert aggregate.infra_errors == 1
    assert aggregate.pass_at_1 == 1.0  # the one graded trial passed
    assert aggregate.pass_hat_k == 1.0
    assert aggregate.is_flaky is False
    assert summary.total.infra_error_rate == 0.5


def test_summary_of_an_all_infra_task_is_not_a_zero():
    """Every trial lost to the harness ⇒ nothing to grade; the row reads 0 graded, never 0% pass."""
    summary = summarize_tasks([_task("001", "easy", [_infra(), _infra()])], trials=2)

    assert summary.per_task[0].graded == 0
    assert summary.per_task[0].pass_at_1 == 0.0
    assert summary.total.infra_error_rate == 1.0
    assert summary.total.graded == 0


def test_summary_rolls_up_per_difficulty_and_totals():
    """A tier row macro-averages its tasks; the totals row macro-averages every task (ADR-0022 §6)."""
    summary = summarize_tasks(
        [
            _task("001", "easy", [_ok(0.01), _ok(0.01)]),
            _task("002", "easy", [_ok(0.01), _fail(0.01)]),
            _task("003", "hard", [_fail(0.02), _fail(0.02)]),
        ],
        trials=2,
    )

    tiers = {rollup.label: rollup for rollup in summary.per_difficulty}
    assert list(tiers) == ["easy", "hard"]  # tier order, and no empty medium row
    assert tiers["easy"].tasks == 2
    assert tiers["easy"].pass_at_1 == 0.75
    assert tiers["easy"].pass_at_k == 1.0
    assert tiers["easy"].pass_hat_k == 0.5
    assert tiers["easy"].flaky_rate == 0.5
    assert tiers["hard"].pass_at_1 == 0.0
    assert summary.total.tasks == 3
    assert summary.total.trials == 6
    assert summary.total.pass_at_1 == (1.0 + 0.5 + 0.0) / 3


def test_summary_costs_are_n_a_without_a_dollar_figure():
    """No provider cost ⇒ ``None`` everywhere, never a 0 that reads as "free" (PA note, task 161)."""
    summary = summarize_tasks([_task("001", "easy", [_ok(), _fail()])], trials=2)

    assert summary.per_task[0].mean_cost_usd is None
    assert summary.total.mean_cost_usd is None
    assert summary.total.success_per_dollar is None


def test_summary_reports_cost_when_the_provider_priced_the_run():
    summary = summarize_tasks([_task("001", "easy", [_ok(0.10), _fail(0.10)])], trials=2)

    assert summary.per_task[0].mean_cost_usd == 0.10
    assert summary.total.mean_cost_usd == 0.10
    assert summary.total.success_per_dollar == 5.0


def test_summary_carries_both_spend_axes():
    """Tokens are summed, run seconds summed and averaged, the wall clock spanned (Amendment §14)."""
    fast = TrialOutcome(
        status="agent_ok",
        reward=1.0,
        input_tokens=1_000,
        output_tokens=100,
        run_seconds=50.0,
        started_at=_at(0),
        finished_at=_at(60),
    )
    slow = TrialOutcome(
        status="agent_fail",
        reward=0.0,
        input_tokens=3_000,
        output_tokens=300,
        run_seconds=80.0,
        started_at=_at(60),
        finished_at=_at(150),
    )
    summary = summarize_tasks([_task("001", "easy", [fast, slow])], trials=2)

    assert summary.per_task[0].mean_tokens == 2_200
    assert summary.per_task[0].mean_run_seconds == 65.0
    assert summary.total.input_tokens == 4_000
    assert summary.total.output_tokens == 400
    assert summary.total.tokens == 4_400
    assert summary.total.run_seconds == 130.0
    assert summary.total.mean_run_seconds == 65.0
    assert summary.total.wall_clock_seconds == 150.0


def test_summary_spend_axes_of_a_run_that_never_ran_are_honest():
    """No trial reached the model: zero tokens, and NO time rather than a zero that reads as instant."""
    summary = summarize_tasks([_task("001", "easy", [_infra()])], trials=1)

    assert summary.total.tokens == 0
    assert summary.total.run_seconds is None
    assert summary.total.mean_run_seconds is None
    assert summary.total.wall_clock_seconds is None


def test_summary_of_an_empty_run_never_raises():
    summary = summarize_tasks([], trials=3)

    assert summary.per_task == []
    assert summary.per_difficulty == []
    assert summary.total.tasks == 0
    assert summary.total.pass_at_1 == 0.0
    assert summary.total.tokens == 0
    assert summary.total.wall_clock_seconds is None


def test_summary_k1_degenerate_matches_pass_at_1():
    summary = summarize_tasks(
        [_task("001", "easy", [_ok()]), _task("002", "hard", [_fail()])], trials=1
    )

    assert summary.total.pass_at_1 == 0.5
    assert summary.total.pass_at_k == 0.5
    assert summary.total.pass_hat_k == 0.5


# --- rendering ---


def _render(summary) -> str:
    console = Console(file=io.StringIO(), width=160)
    console.print(render_summary_table(summary))
    return console.file.getvalue()


def test_table_shows_task_rows_tier_rollups_and_a_totals_row():
    summary = summarize_tasks(
        [
            _task("001-find-and-replace", "easy", [_ok(0.01), _fail(0.01)]),
            _task("018-git-bisect-revert", "hard", [_infra(), _fail(0.02)]),
        ],
        trials=2,
    )

    text = _render(summary)

    assert "001-find-and-replace" in text
    assert "018-git-bisect" in text  # the table may wrap a long id
    assert "easy" in text and "hard" in text
    assert "TOTAL" in text
    assert "pass@2" in text and "pass^2" in text
    assert "$0.0100" in text


def test_table_says_n_a_when_no_cost_was_reported():
    summary = summarize_tasks([_task("001", "easy", [_ok()])], trials=1)

    assert "n/a" in _render(summary)


def test_table_shows_the_two_spend_axes_per_trial():
    outcome = TrialOutcome(
        status="agent_ok", reward=1.0, input_tokens=12_000, output_tokens=345, run_seconds=61.4
    )
    text = _render(summarize_tasks([_task("001", "easy", [outcome])], trials=1))

    assert "~s/trial" in text and "~tok/trial" in text
    assert "61s" in text
    assert "12,345" in text


def test_spend_line_names_the_wall_clock_the_run_seconds_and_the_tokens():
    outcome = TrialOutcome(
        status="agent_ok",
        reward=1.0,
        input_tokens=12_000,
        output_tokens=345,
        run_seconds=61.4,
        started_at=_at(0),
        finished_at=_at(70),
    )
    summary = summarize_tasks([_task("001", "easy", [outcome])], trials=1)

    line = render_spend_line(summary)

    assert (
        line == "spend: wall clock 70s · agent run 61s total · tokens 12,345 (12,000 in / 345 out)"
    )


def test_spend_line_says_n_a_when_no_trial_ran():
    line = render_spend_line(summarize_tasks([_task("001", "easy", [_infra()])], trials=1))

    assert line == "spend: wall clock n/a · agent run n/a total · tokens 0 (0 in / 0 out)"


def test_table_shows_one_pass_column_for_a_single_trial_run():
    """At k=1 pass@k and pass^k ARE pass@1 — three columns headed ``pass@1`` say nothing."""
    text = _render(summarize_tasks([_task("001", "easy", [_ok()])], trials=1))

    assert text.count("pass@1") == 1
    assert "pass^1" not in text
