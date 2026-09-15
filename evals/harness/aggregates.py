"""The benchmark's reliability + cost math — pure functions over trial matrices (ADR-0022 §4,§6).

Opik 2.2.36 takes the aggregates on its own axis: ``evaluate(experiment_scoring_functions=[...])``
hands each callable the run's ``TestResult`` list and logs what it returns onto the Experiment row.
That adapter lives with the rest of the Opik surface in :mod:`evals.harness.benchmark`; **this module
is deliberately opik-free**. It holds the math and the Rich table, so every number the benchmark
reports can be checked on a hand-built matrix with no client, no keys and no network.

Two layers:

* the per-task functions — :func:`pass_at_1`, :func:`pass_at_k`, :func:`pass_hat_k`,
  :func:`is_flaky`, :func:`success_per_dollar`, :func:`infra_error_rate`, :func:`mean_cost_usd`,
  :func:`total_tokens`, :func:`total_seconds`, :func:`wall_clock_seconds` — over plain sequences;
* :func:`summarize_tasks`, which folds ``[TaskTrials, ...]`` into a :class:`BenchmarkSummary`:
  one row per task, a rollup per Difficulty Tier and a totals row, all macro-averaged (each task
  weighs the same regardless of how many trials survived).

The rule every layer obeys is ADR-0022 §4: an **Infra Error** is excluded from BOTH the numerator and
the denominator — it is counted and shown, never scored. Nothing here raises: an empty matrix, a
single trial, an all-infra task each yield a valid summary.

Two spend axes ride beside the pass rates, because the two ways decode is billed for a model differ
(ADR-0022 Amendment §14): **tokens** (input + output, what a pay-per-token route like OpenRouter
charges) and **time** (the agent's ``run`` seconds per trial and the job's wall clock from the first
trial's start to the last one's end, what a pay-per-GPU-hour endpoint like Modal charges while it is
kept warm). Both are raw measurements — the harness never multiplies them by a price it cannot know.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from math import comb
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.table import Table

# The Opik score whose value IS the grade of record: the Verifier's reward (ADR-0022 §3).
DEFAULT_PASS_METRIC = "reward"

# The two statuses this module cares about out of the ADR-0022 §4 taxonomy.
AGENT_OK_STATUS = "agent_ok"
INFRA_ERROR_STATUS = "infra_error"

# Tier order for the rollup rows — easiest first, as the benchmark README lists them.
DIFFICULTY_ORDER = ("easy", "medium", "hard")


@dataclass(frozen=True, slots=True)
class TrialOutcome:
    """One Trial as the aggregates see it: how it ended, what it scored, what it cost.

    Mirrors the fields of ``result.json`` the math needs (:class:`evals.harness.trial.TrialResult`)
    so a Trial Dir on disk and an Opik ``TestResult`` fold into the same shape. ``cost_usd`` is
    ``None`` when the provider reported no dollar figure — never 0.0, which would read as "free".
    ``input_tokens`` / ``output_tokens`` are the run's own usage totals (``0`` for a trial that never
    reached a summary); ``run_seconds`` is the agent's ``run`` phase and ``started_at`` /
    ``finished_at`` bound the whole trial — each ``None`` when the trial never ran.
    """

    status: str
    reward: float | None = None
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    run_seconds: float | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def tokens(self) -> int:
        """Input plus output tokens — the figure a per-token price multiplies."""
        return self.input_tokens + self.output_tokens

    @property
    def passed(self) -> bool:
        """True only for ``agent_ok`` — the trial runner's single definition of a won trial."""
        return self.status == AGENT_OK_STATUS

    @property
    def graded(self) -> bool:
        """True when the trial reached a reward, i.e. it counts in the denominator (ADR-0022 §4)."""
        return self.status != INFRA_ERROR_STATUS


@dataclass(frozen=True, slots=True)
class TaskTrials:
    """One Benchmark Task's ``k`` trials plus the labels the rollups slice by."""

    task_id: str
    difficulty: str
    outcomes: tuple[TrialOutcome, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskAggregate:
    """One task's row: reliability over its graded trials, plus what the harness lost and spent."""

    task_id: str
    difficulty: str
    trials: int
    graded: int
    passes: int
    infra_errors: int
    pass_at_1: float
    pass_at_k: float
    pass_hat_k: float
    is_flaky: bool
    mean_cost_usd: float | None
    mean_tokens: float
    mean_run_seconds: float | None


@dataclass(frozen=True, slots=True)
class Rollup:
    """A group of tasks folded into one row — a Difficulty Tier, or the whole run (``TOTAL``).

    The pass rates are MACRO averages over the group's tasks (every task weighs the same, so a task
    that lost trials to the harness cannot dominate a tier); the counts, the money, the tokens and
    the seconds are sums over the group's trials; ``wall_clock_seconds`` is the span from the
    group's first trial start to its last trial end (``None`` when no trial ran).
    """

    label: str
    tasks: int
    trials: int
    graded: int
    infra_errors: int
    pass_at_1: float
    pass_at_k: float
    pass_hat_k: float
    flaky_rate: float
    infra_error_rate: float
    mean_cost_usd: float | None
    success_per_dollar: float | None
    input_tokens: int
    output_tokens: int
    mean_tokens: float
    run_seconds: float | None
    mean_run_seconds: float | None
    wall_clock_seconds: float | None

    @property
    def tokens(self) -> int:
        """Input plus output tokens over the group — the figure a per-token price multiplies."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    """A whole Benchmark Job: per-task rows, per-tier rollups and the totals row."""

    trials: int
    per_task: list[TaskAggregate] = field(default_factory=list)
    per_difficulty: list[Rollup] = field(default_factory=list)
    total: Rollup = field(
        default_factory=lambda: Rollup(
            label="TOTAL",
            tasks=0,
            trials=0,
            graded=0,
            infra_errors=0,
            pass_at_1=0.0,
            pass_at_k=0.0,
            pass_hat_k=0.0,
            flaky_rate=0.0,
            infra_error_rate=0.0,
            mean_cost_usd=None,
            success_per_dollar=None,
            input_tokens=0,
            output_tokens=0,
            mean_tokens=0.0,
            run_seconds=None,
            mean_run_seconds=None,
            wall_clock_seconds=None,
        )
    )


# --- Core reliability math (pure, over one task's graded trials) ---


def pass_at_1(trials: Sequence[bool]) -> float:
    """Mean pass rate across the trials — ``0.0`` for an empty run (never divides by zero)."""
    if not trials:
        return 0.0
    return sum(1 for passed in trials if passed) / len(trials)


def pass_at_k(trials: Sequence[bool], k: int | None = None) -> float:
    """Harbor's unbiased pass@k estimator over ``n`` binary trials: ``1 - C(n-c, k) / C(n, k)``.

    The probability that a random sample of ``k`` of the ``n`` trials contains at least one pass —
    the honest way to report "could it do it in k attempts?" from more attempts than k. With
    ``k = n`` (the default, and what ``--trials k`` spends) it collapses to plain ``any()``, which is
    why a k-trial run and this estimator never disagree. A ``k`` above ``n`` is clamped: n trials
    cannot estimate a larger budget.
    """
    n = len(trials)
    if n == 0:
        return 0.0
    k = n if k is None else max(1, min(k, n))
    failures = n - sum(1 for passed in trials if passed)
    if failures < k:
        return 1.0
    return 1.0 - comb(failures, k) / comb(n, k)


def pass_hat_k(trials: Sequence[bool]) -> float:
    """``1.0`` iff EVERY trial passed — the reliability bar; empty is not vacuously reliable."""
    return 1.0 if trials and all(trials) else 0.0


def is_flaky(trials: Sequence[bool]) -> bool:
    """True when the task passed some but not all trials (``0 < passes < k``)."""
    passes = sum(1 for passed in trials if passed)
    return 0 < passes < len(trials)


def infra_error_rate(statuses: Sequence[str]) -> float:
    """The fraction of trials the harness itself lost — the number that invalidates a run.

    Rises with flaky docker, a wedged Verifier or a seed that will not clone; a run whose rate is not
    ~0 is measuring the harness, not the agent (ADR-0022 §4).
    """
    if not statuses:
        return 0.0
    return sum(1 for status in statuses if status == INFRA_ERROR_STATUS) / len(statuses)


def mean_cost_usd(costs: Sequence[float | None]) -> float | None:
    """Mean dollar cost over the trials that REPORTED one, or ``None`` when none did.

    A trial with no cost (an Infra Error never reaches a summary; some providers price nothing) is
    left out of the average rather than counted as ``$0`` — the table says ``n/a``, never 0.
    """
    reported = [cost for cost in costs if cost is not None]
    if not reported:
        return None
    return sum(reported) / len(reported)


def success_per_dollar(trials: Sequence[bool], costs: Sequence[float | None]) -> float | None:
    """Wins per dollar spent, or ``None`` when the run was not priced (ADR-0022 §6).

    The suite's efficiency number: a model that solves twice as many tasks for four times the money
    is worse. ``None`` — not ``0`` — when no trial reported a cost or the total is zero.
    """
    spent = sum(cost for cost in costs if cost is not None)
    if spent <= 0:
        return None
    return sum(1 for passed in trials if passed) / spent


# --- The spend axes a price multiplies: tokens (per-token billing) and time (per-hour billing) ---


def total_tokens(tokens: Sequence[int]) -> int:
    """Sum of per-trial token counts — a trial that never ran contributes its ``0``, honestly."""
    return sum(tokens)


def total_seconds(seconds: Sequence[float | None]) -> float | None:
    """Sum of the trials' seconds that were measured, or ``None`` when none was.

    A trial whose phase never ran (an Infra Error at seed time) reports no seconds and is left out
    rather than counted as instant; a job in which nothing ran has no time to report at all.
    """
    measured = [value for value in seconds if value is not None]
    if not measured:
        return None
    return sum(measured)


def mean_seconds(seconds: Sequence[float | None]) -> float | None:
    """Mean of the measured seconds per trial, or ``None`` when none was measured."""
    measured = [value for value in seconds if value is not None]
    if not measured:
        return None
    return sum(measured) / len(measured)


def wall_clock_seconds(
    starts: Sequence[datetime | None], ends: Sequence[datetime | None]
) -> float | None:
    """Seconds from the earliest trial start to the latest trial end, or ``None`` when none ran.

    THE number a pay-per-GPU-hour endpoint bills for: while a job is in flight the endpoint is kept
    warm end to end, whatever ``--threads`` did in between. It is a span, not a sum — so with
    ``--threads 4`` it is roughly a quarter of :func:`total_seconds` over the ``run`` phases, and the
    two together say how well the fan-out used the hardware. Mismatched or missing stamps are
    skipped; a lone stamp of either kind yields ``None``, never a negative or a zero.
    """
    started = [value for value in starts if value is not None]
    finished = [value for value in ends if value is not None]
    if not started or not finished:
        return None
    return max(0.0, (max(finished) - min(started)).total_seconds())


# --- The suite summary over a trial matrix ---


def summarize_tasks(tasks: Sequence[TaskTrials], *, trials: int) -> BenchmarkSummary:
    """Fold every task's trials into per-task rows, per-tier rollups and a totals row.

    Pure and total: an empty matrix, a ``k=1`` degenerate run, an all-infra task each yield a valid
    summary rather than raising. ``trials`` is the requested ``k`` (it labels the table's columns);
    the actual trial counts come from the matrix, which can be shorter when the harness lost one.
    """
    aggregates = [_aggregate_task(task) for task in tasks]
    by_difficulty = {
        tier: [task for task in tasks if task.difficulty == tier] for tier in DIFFICULTY_ORDER
    }
    return BenchmarkSummary(
        trials=trials,
        per_task=aggregates,
        per_difficulty=[_rollup(tier, group) for tier, group in by_difficulty.items() if group],
        total=_rollup("TOTAL", tasks),
    )


def _aggregate_task(task: TaskTrials) -> TaskAggregate:
    """One task's row — reliability over its GRADED trials, infra errors counted beside them."""
    graded = _graded_passes(task.outcomes)
    return TaskAggregate(
        task_id=task.task_id,
        difficulty=task.difficulty,
        trials=len(task.outcomes),
        graded=len(graded),
        passes=sum(1 for passed in graded if passed),
        infra_errors=sum(1 for outcome in task.outcomes if not outcome.graded),
        pass_at_1=pass_at_1(graded),
        pass_at_k=pass_at_k(graded),
        pass_hat_k=pass_hat_k(graded),
        is_flaky=is_flaky(graded),
        mean_cost_usd=mean_cost_usd([outcome.cost_usd for outcome in task.outcomes]),
        mean_tokens=_mean(float(outcome.tokens) for outcome in task.outcomes),
        mean_run_seconds=mean_seconds([outcome.run_seconds for outcome in task.outcomes]),
    )


def _rollup(label: str, tasks: Sequence[TaskTrials]) -> Rollup:
    """Macro-average a group of tasks into one row (a tier, or the whole run)."""
    aggregates = [_aggregate_task(task) for task in tasks]
    outcomes = [outcome for task in tasks for outcome in task.outcomes]
    graded = _graded_passes(outcomes)
    costs = [outcome.cost_usd for outcome in outcomes]
    run_seconds = [outcome.run_seconds for outcome in outcomes]
    return Rollup(
        label=label,
        tasks=len(aggregates),
        trials=len(outcomes),
        graded=len(graded),
        infra_errors=sum(1 for outcome in outcomes if not outcome.graded),
        pass_at_1=_mean(aggregate.pass_at_1 for aggregate in aggregates),
        pass_at_k=_mean(aggregate.pass_at_k for aggregate in aggregates),
        pass_hat_k=_mean(aggregate.pass_hat_k for aggregate in aggregates),
        flaky_rate=_mean(1.0 if aggregate.is_flaky else 0.0 for aggregate in aggregates),
        infra_error_rate=infra_error_rate([outcome.status for outcome in outcomes]),
        mean_cost_usd=mean_cost_usd(costs),
        success_per_dollar=success_per_dollar(graded, costs),
        input_tokens=total_tokens([outcome.input_tokens for outcome in outcomes]),
        output_tokens=total_tokens([outcome.output_tokens for outcome in outcomes]),
        mean_tokens=_mean(float(outcome.tokens) for outcome in outcomes),
        run_seconds=total_seconds(run_seconds),
        mean_run_seconds=mean_seconds(run_seconds),
        wall_clock_seconds=wall_clock_seconds(
            [outcome.started_at for outcome in outcomes],
            [outcome.finished_at for outcome in outcomes],
        ),
    )


def _graded_passes(outcomes: Sequence[TrialOutcome]) -> list[bool]:
    """The pass flags of the trials that reached a reward — Infra Errors dropped (ADR-0022 §4)."""
    return [outcome.passed for outcome in outcomes if outcome.graded]


def _mean(values: Iterable[float]) -> float:
    """Arithmetic mean of an iterable of numbers — ``0.0`` when empty (never divides by zero)."""
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


# --- Rendering ---


def render_summary_table(summary: BenchmarkSummary) -> Table:
    """The Rich table ``python -m evals benchmark`` prints: task rows, tier rollups, totals.

    The pass@k / pass^k columns are labelled with the actual ``k`` so a ``--trials 3`` run reads
    ``pass@3`` / ``pass^3``, and they are dropped entirely at ``k=1`` where they would just repeat
    pass@1. ``n`` is the trials attempted and ``infra`` how many of them the harness lost — read
    together they say how much of the row is real. Costs print ``n/a`` when the provider reported no
    dollar figure. ``~s/trial`` and ``~tok/trial`` are the two spend axes per trial — mean agent
    ``run`` seconds (per-hour billing) and mean input+output tokens (per-token billing); the job's
    totals and wall clock print as one line under the table (:func:`render_spend_line`).
    """
    from rich.table import Table

    k = summary.trials
    # At k=1 pass@k and pass^k ARE pass@1 by definition — three identical columns headed "pass@1"
    # would say nothing, so a single-trial run shows one.
    multi_trial = k > 1
    table = Table(title=f"decode benchmark — {summary.total.tasks} task(s) x {k} trial(s)")
    table.add_column("task")
    table.add_column("n", justify="right")
    table.add_column("pass@1", justify="right")
    if multi_trial:
        table.add_column(f"pass@{k}", justify="right")
        table.add_column(f"pass^{k}", justify="right")
        table.add_column("flaky", justify="center")
    table.add_column("~$/trial", justify="right")
    table.add_column("~s/trial", justify="right")
    table.add_column("~tok/trial", justify="right")
    table.add_column("infra", justify="right")

    for aggregate in summary.per_task:
        table.add_row(
            aggregate.task_id,
            str(aggregate.trials),
            f"{aggregate.pass_at_1:.2f}",
            *(
                (
                    f"{aggregate.pass_at_k:.2f}",
                    f"{aggregate.pass_hat_k:.2f}",
                    "yes" if aggregate.is_flaky else "",
                )
                if multi_trial
                else ()
            ),
            _cost_cell(aggregate.mean_cost_usd),
            _seconds_cell(aggregate.mean_run_seconds),
            f"{aggregate.mean_tokens:,.0f}",
            str(aggregate.infra_errors) if aggregate.infra_errors else "",
        )

    for rollup in summary.per_difficulty:
        table.add_section()
        _add_rollup_row(table, rollup, f"{rollup.label} ({rollup.tasks} task(s))", multi_trial)

    table.add_section()
    _add_rollup_row(table, summary.total, summary.total.label, multi_trial)
    return table


def _add_rollup_row(table: Table, rollup: Rollup, label: str, multi_trial: bool) -> None:
    """One macro-averaged group row — a tier or the whole run."""
    table.add_row(
        label,
        str(rollup.trials),
        f"{rollup.pass_at_1:.2f}",
        *(
            (
                f"{rollup.pass_at_k:.2f}",
                f"{rollup.pass_hat_k:.2f}",
                f"{rollup.flaky_rate:.0%}",
            )
            if multi_trial
            else ()
        ),
        _cost_cell(rollup.mean_cost_usd),
        _seconds_cell(rollup.mean_run_seconds),
        f"{rollup.mean_tokens:,.0f}",
        str(rollup.infra_errors),
    )


def render_spend_line(summary: BenchmarkSummary) -> str:
    """One line with the job's totals — what a price multiplies, printed under the table.

    ``wall clock`` is first trial start to last trial end (times your $/hour = a warm endpoint's bill),
    ``agent run`` the sum of every trial's ``run`` phase, and the tokens the sum over every trial
    (times the route's $/Mtok = a per-token bill). The same three numbers ride the Opik experiment row.
    """
    total = summary.total
    return (
        f"spend: wall clock {_seconds_cell(total.wall_clock_seconds)} · "
        f"agent run {_seconds_cell(total.run_seconds)} total · "
        f"tokens {total.tokens:,} ({total.input_tokens:,} in / {total.output_tokens:,} out)"
    )


def _cost_cell(cost: float | None) -> str:
    """A cost cell: a dollar figure, or ``n/a`` when the provider reported none (never ``$0``)."""
    return f"${cost:.4f}" if cost is not None else "n/a"


def _seconds_cell(seconds: float | None) -> str:
    """A time cell: whole seconds, or ``n/a`` when nothing was measured (never ``0s``)."""
    return f"{seconds:,.0f}s" if seconds is not None else "n/a"
