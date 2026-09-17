"""The Benchmark Job: Opik ``evaluate()`` over Benchmark Trials (ADR-0022 §1,§4,§6,§7).

One ``python -m evals benchmark`` invocation = one Opik Experiment over ``decode-benchmark``.
Opik's ``evaluate()`` IS Harbor's Job/Trial orchestrator in disguise, so the whole module is a thin
mapping onto it:

* dataset item = Benchmark Task, ``trial_count`` = ``--trials k``, ``task_threads`` = the fan-out
  (subprocess trials are independent, ADR-0022 §6);
* ``task`` = :func:`make_benchmark_task_fn`, which runs ONE Trial (:func:`~evals.harness.trial.run_trial`
  — a subprocess ``decode run`` against a fresh Seed Repo, graded host-side on a pristine clone) and
  returns its verdict as a flat dict. The task fn NEVER raises: ``evaluate`` gives task fns no
  per-item isolation, so one raise would abort the whole Experiment;
* ``scoring_metrics=[RewardMetric()]`` = the grade of record, with an Infra Error landing as
  ``scoring_failed`` so it leaves both the numerator and the denominator (ADR-0022 §4);
* ``experiment_scoring_functions=EXPERIMENT_SCORING_FUNCTIONS`` = pass@k / pass^k / flakiness / cost
  plus the two raw spend axes — tokens and time (ADR-0022 Amendment §14) — ON THE EXPERIMENT ROW,
  computed by opik 2.2.36 itself over the run's ``TestResult`` list. ADR-0017 §8's post-hoc
  feedback-score attachment is deleted;
* ``experiment_config`` = the provenance two rows are told apart by, ``project_name`` =
  ``settings.eval_project_name`` so eval runs never pollute live REPL tracing.

This module is the ONLY place the Opik types meet the benchmark: the math it reports lives in
:mod:`evals.harness.aggregates` (pure, opik-free) and the Trial itself in
:mod:`evals.harness.trial` (subprocess-only).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from opik.evaluation.metrics.score_result import ScoreResult

from decode.config.settings import settings
from evals.harness.aggregates import (
    BenchmarkSummary,
    Rollup,
    TaskTrials,
    TrialOutcome,
    summarize_tasks,
)
from evals.harness.task_loader import BenchmarkTask, load_benchmark_tasks

# ``git_sha`` lives with the trial runner (it stamps every ``result.json``); it is re-exported here
# because the regression track reads a run's provenance from this module (``regression.py``).
from evals.harness.trial import TrialResult, decode_version, git_sha, run_trial

if TYPE_CHECKING:
    import opik
    from opik.evaluation.evaluation_result import EvaluationResult
    from opik.evaluation.test_result import TestResult

    from evals.harness.task_loader import Difficulty

logger = logging.getLogger(__name__)

# The Opik task fn Opik hands one dataset item and expects one flat output dict back.
BenchmarkTaskFn = Callable[[dict[str, Any]], dict[str, Any]]

# Where Trial Dirs land: under the Harness Home's ``.decode/`` (already git-ignored), one directory
# per Benchmark Job so a job's trials sit together (ADR-0022 §7).
RUNS_DIR_PARTS = ("evals", "runs")

# A job's default name — also its Trial Dir's parent and the Opik experiment name.
JOB_NAME_PREFIX = "bench"

# A job name is ONE path segment: it becomes a directory under ``.decode/evals/runs/`` and an Opik
# experiment name, so it must neither traverse (``../../evil``) nor nest (``a/b``), and must stay
# readable on the experiment row.
JOB_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
JOB_NAME_RULE = (
    "a job name must be a single path segment matching "
    "[A-Za-z0-9][A-Za-z0-9._-]{0,63} (no '/' and no '..')"
)

# Default ``task_threads`` per sandbox rung. Docker trials each warm up their own container on the
# operator's laptop, so the default is one; modal trials are remote and fan out freely.
DEFAULT_THREADS = {"docker": 1, "modal": 4}


class BenchmarkSelectionError(Exception):
    """No benchmark task matched the ``--task`` / ``--difficulty`` filters — a loud, friendly stop."""


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    """What one Benchmark Job leaves behind: the Opik result and the Trial Dirs on disk."""

    result: EvaluationResult
    job_dir: Path

    @property
    def experiment_name(self) -> str:
        """The Opik experiment name — the job name, which is also the Trial Dir's parent."""
        name = getattr(self.result, "experiment_name", None)
        return name if isinstance(name, str) and name else self.job_dir.name


def make_benchmark_task_fn(
    tasks_by_id: dict[str, BenchmarkTask],
    *,
    sandbox: str,
    job_dir: Path,
    model: str | None = None,
) -> BenchmarkTaskFn:
    """Build the sync Opik task fn that runs + grades one item's task as a Trial (ADR-0022 §1).

    The returned closure looks the task up by ``item["task_id"]`` and runs one Trial into its own
    Trial Dir ``<job_dir>/<task-id>__<short-id>/``. Sync because Opik ``evaluate()`` task fns cannot
    be async; the trial itself is a subprocess, so nothing here is loop-bound. Every failure — an
    item naming a task this checkout no longer has, a trial runner that raised despite its own
    guarantee — comes back as an ``infra_error`` payload rather than an exception.
    """

    def benchmark_task_fn(item: dict[str, Any]) -> dict[str, Any]:
        task_id = str(item.get("task_id"))
        task = tasks_by_id.get(task_id)
        if task is None:
            return _infra_payload(f"no task folder named {task_id!r} in this checkout")
        try:
            result = run_trial(
                task,
                sandbox=sandbox,  # type: ignore[arg-type]  (the cli constrains it to docker|modal)
                job_dir=job_dir,
                trial_id=uuid4().hex[:8],
                model=model,
            )
        except Exception as exc:  # one raise would abort the WHOLE Experiment
            logger.exception("[eval] the trial fn for %s raised", task_id)
            return _infra_payload(f"the trial fn raised: {exc}", max_steps=task.max_steps)
        return trial_payload(result, task)

    return benchmark_task_fn


def default_job_name() -> str:
    """``bench-<UTC yyyymmdd-HHMMSS>`` — sortable, collision-free at one job per second."""
    return f"{JOB_NAME_PREFIX}-{datetime.now(UTC):%Y%m%d-%H%M%S}"


def validate_job_name(job_name: str) -> str:
    """Return ``job_name`` if it is one safe path segment, else raise ``ValueError`` naming the rule.

    ``--job-name`` is joined onto ``.decode/evals/runs/``, so ``../../evil`` would resolve outside
    the harness tree and a real (billed) job would write Trial Dirs onto the host filesystem. The
    same string is the Opik experiment name, so the rule keeps it readable too.
    """
    if not JOB_NAME_RE.match(job_name):
        raise ValueError(f"{JOB_NAME_RULE}; got {job_name!r}.")
    return job_name


def new_job_dir(job_name: str) -> Path:
    """The job's Trial Dir parent: ``<harness home>/.decode/evals/runs/<job name>/``."""
    return Path(settings.decode_dir).joinpath(*RUNS_DIR_PARTS, validate_job_name(job_name))


def default_threads(sandbox: str) -> int:
    """How many trials run at once by default on this rung (1 docker / 4 modal)."""
    return DEFAULT_THREADS.get(sandbox, 1)


def trial_payload(result: TrialResult, task: BenchmarkTask) -> dict[str, Any]:
    """The flat output dict the metrics read, straight off one :class:`TrialResult`.

    ``reward`` / ``status`` / ``reason`` are the grade of record (ADR-0022 §3,§4) and are what
    :class:`~evals.harness.metrics.RewardMetric` and the experiment scores read; ``steps``, the
    token counts and ``cost_usd`` come from the run's own summary; ``trial_dir`` points a human at
    the evidence. ``infra_error`` carries the reason ONLY when the harness was at fault, so an
    excluded trial is distinguishable from a lost one without re-deriving the taxonomy.

    The time axis (ADR-0022 Amendment §14): ``run_seconds`` is the agent's own ``run`` phase (the
    time the model was being driven), ``trial_seconds`` the whole trial (seed + run + verify), and
    ``started_at`` / ``finished_at`` the UTC stamps the experiment row spans into the job's wall
    clock. The tokens count the run's OWN requests — an Explore subagent's spend never joins the
    parent's history (``decode.runtime.summary``), so a trial that fanned out is undercounted here
    and fully counted only on its Opik trace.

    ``session_id`` / ``kitaru_session_id`` come straight off the run's own ``--summary-json`` and are
    the ONLY join from an experiment row back to the Session the trial recorded (task 165): the
    item's Opik trace is ``evaluate()``'s wrapper around the task fn, never the ``decode run``
    subprocess trace, so ``evals kitaru cohort from-experiment`` has nothing else to resolve by.
    """
    summary = result.summary or {}
    return {
        "output": summary.get("output", ""),
        "reward": result.reward,
        "status": result.status,
        "reason": result.reason,
        "timed_out": result.timed_out,
        "branch": result.branch,
        "steps": summary.get("requests", 0),
        "max_steps": task.max_steps,
        "input_tokens": summary.get("input_tokens", 0),
        "output_tokens": summary.get("output_tokens", 0),
        "cost_usd": summary.get("cost_usd"),
        "run_seconds": result.timings.get("run"),
        "trial_seconds": round((result.finished_at - result.started_at).total_seconds(), 3),
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "trial_dir": str(result.trial_dir),
        "session_id": summary.get("session_id"),
        "kitaru_session_id": summary.get("kitaru_session_id"),
        "infra_error": result.reason if result.status == "infra_error" else None,
    }


def _infra_payload(reason: str, *, max_steps: int = 0) -> dict[str, Any]:
    """The payload for a trial the harness never got to run — same shape, ``infra_error`` status."""
    return {
        "output": "",
        "reward": None,
        "status": "infra_error",
        "reason": reason,
        "timed_out": False,
        "branch": None,
        "steps": 0,
        "max_steps": max_steps,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": None,
        "run_seconds": None,
        "trial_seconds": None,
        "started_at": None,
        "finished_at": None,
        "trial_dir": "",
        "session_id": None,
        "kitaru_session_id": None,
        "infra_error": reason,
    }


def run_benchmark(
    *,
    task_id: str | Sequence[str] | None = None,
    difficulty: Difficulty | None = None,
    sandbox: str = "docker",
    trials: int = 1,
    threads: int | None = None,
    job_name: str | None = None,
    model: str | None = None,
    client: opik.Opik | None = None,
) -> BenchmarkRun:
    """Run the filtered benchmark as one Opik Experiment and return it + its job dir (ADR-0022 §6).

    Loads every task, applies the ``--task`` (one id, or several) / ``--difficulty`` filters, upserts the selection into
    ``decode-benchmark``, and calls ``evaluate`` scoped (via ``dataset_item_ids``) to the items
    whose ``checksum`` matches the task folders on disk — Opik never deletes a superseded item, so
    without that scoping an edited task would be run once per historical version. Raises
    :class:`BenchmarkSelectionError` when the filters match no task OR when a matched task has no
    fresh dataset item (``dataset_item_ids=None`` would run the WHOLE dataset), and ``ValueError`` on
    ``trials < 1``.
    """
    import opik
    from opik.evaluation import evaluate

    from evals.harness.datasets import sync_benchmark_dataset, task_checksum
    from evals.harness.metrics import RewardMetric

    if trials < 1:
        # Guard BEFORE evaluate: opik's evaluate(trial_count<1) range()-loops zero times and returns
        # cleanly, which would report a nonsense pass@<0> over zero real trials.
        raise ValueError(f"trials must be >= 1, got {trials}.")

    all_tasks = load_benchmark_tasks()
    selected = select_tasks(all_tasks, task_id=task_id, difficulty=difficulty)
    if not selected:
        raise BenchmarkSelectionError(
            f"no benchmark task matched (task={task_id!r}, difficulty={difficulty!r}); "
            f"{len(all_tasks)} task(s) available."
        )

    job_name = job_name or default_job_name()
    job_dir = new_job_dir(job_name)
    client = client or opik.Opik()
    dataset = sync_benchmark_dataset(selected, client=client)
    item_ids = _selected_item_ids(dataset, {task.id: task_checksum(task) for task in selected})
    missing = {task.id for task in selected} - set(item_ids)
    if missing:
        # Never a silent ALL-item experiment: ``evaluate(dataset_item_ids=None)`` runs the whole
        # dataset, so an empty/partial match must stop the run rather than quietly bill 19 tasks.
        raise BenchmarkSelectionError(
            "the sync did not produce a matching dataset item for "
            f"{sorted(missing)} in {dataset.name!r} — re-run `python -m evals sync --benchmark`."
        )

    task_fn = make_benchmark_task_fn(
        {task.id: task for task in all_tasks}, sandbox=sandbox, job_dir=job_dir, model=model
    )
    task_threads = threads if threads is not None else default_threads(sandbox)
    result = evaluate(
        dataset=dataset,
        task=task_fn,
        scoring_metrics=[RewardMetric()],
        experiment_scoring_functions=EXPERIMENT_SCORING_FUNCTIONS,
        experiment_config=experiment_config(
            sandbox=sandbox, trials=trials, threads=task_threads, job_name=job_name, model=model
        ),
        experiment_name=job_name,
        project_name=evaluate_project_name(dataset),
        dataset_item_ids=list(item_ids.values()),
        task_threads=task_threads,
        trial_count=trials,
    )
    return BenchmarkRun(result=result, job_dir=job_dir)


def select_tasks(
    tasks: list[BenchmarkTask],
    *,
    task_id: str | Sequence[str] | None,
    difficulty: Difficulty | None,
) -> list[BenchmarkTask]:
    """Filter loaded tasks by exact id(s) and/or ``difficulty`` (both optional, AND-combined).

    ``task_id`` is one id or several (a repeated ``--task``): a hand-picked subset runs as ONE
    Experiment, which is what a light A/B across providers wants (ADR-0022 Amendment §14). Public so
    the CLI's ``sync --difficulty`` slices the dataset upsert by the SAME rule a run does (the
    Regression Case side is :func:`evals.regression.loader.select_cases`).
    """
    selected = tasks
    if task_id is not None:
        wanted = {task_id} if isinstance(task_id, str) else set(task_id)
        selected = [task for task in selected if task.id in wanted]
    if difficulty is not None:
        selected = [task for task in selected if task.difficulty == difficulty]
    return selected


def _selected_item_ids(dataset: Any, checksums: dict[str, str]) -> dict[str, str]:
    """``{task_id: item id}`` for the items whose ``checksum`` matches the task folder on disk.

    Opik's ``insert`` dedupes by content hash but never deletes: an edited task leaves its stale item
    in ``decode-benchmark`` forever. Selecting on the checksum too is what keeps one ``--task``
    run from evaluating every historical version of that task. One id per task (the first match);
    an item missing an ``id`` (an unexpected Opik shape) is skipped, and a task with no matching item
    is simply absent — the caller turns that into a loud stop.
    """
    selected: dict[str, str] = {}
    for item in dataset.get_items():
        task_id = item.get("task_id")
        if task_id in selected or item.get("id") is None:
            continue
        if checksums.get(task_id) == item.get("checksum"):
            selected[task_id] = item["id"]
    return selected


def evaluate_project_name(dataset: Any) -> str | None:
    """The ``evaluate(project_name=...)`` value — ``None`` once the dataset names the project.

    opik 2.2.36 resolves the run's project from the dataset and DEPRECATES this parameter, warning
    once per run when both are set. Both syncs create their dataset inside
    ``settings.eval_project_name``, so the parameter is only needed as the fallback for a dataset
    created before that (and it is the same name either way — eval traces never touch live tracing).
    Public because BOTH experiment tracks answer this question the same way (the regression harness
    reuses it) — one rule, never two drifting copies.
    """
    return None if getattr(dataset, "project_name", None) else settings.eval_project_name


def experiment_config(
    *, sandbox: str, trials: int, threads: int, job_name: str, model: str | None
) -> dict[str, Any]:
    """The Opik ``experiment_config``: everything two Experiment rows differ by (ADR-0022 §6).

    The model + provider driving the agent, the code the run was on (``git rev-parse HEAD`` + the
    installed package version), the rung that executed it, the job's own shape, and the
    ``kitaru_agent_id`` that joins this Experiment to the Kitaru Sessions its trials recorded —
    ``None``, never a placeholder, when recording was not configured (ADR-0022 §10). ``threads`` is
    provenance for the time axis: the job's wall clock is a span, so two rows with the same trials
    and a different fan-out are not comparable on it (ADR-0022 Amendment §14).
    """
    return {
        "model": agent_model(model),
        "provider": settings.llm_provider,
        "git_sha": git_sha(),
        "sandbox": sandbox,
        "decode_version": decode_version(),
        "kitaru_agent_id": settings.kitaru_agent_id or None,
        "trials": trials,
        "threads": threads,
        "job_name": job_name,
    }


def agent_model(model: str | None = None) -> str:
    """The model string the agent runs on: the ``--model`` override, else the active provider's.

    Public so the regression harness reuses it (:func:`evals.harness.regression.experiment_config`)
    instead of reaching for a private name across modules — one resolver, both experiment tracks.
    """
    return model or settings.active_model


# --- The Opik seam: read a run's TestResults as a trial matrix ---


def task_trials(test_results: Sequence[TestResult]) -> list[TaskTrials]:
    """Group an Opik run's ``TestResult``s into one :class:`TaskTrials` per Benchmark Task.

    The task fn's payload (``status`` / ``reward`` / ``cost_usd`` / the tokens / the time stamps)
    rides on ``test_case.task_output`` and the slice labels on ``test_case.dataset_item_content`` —
    so the aggregates read the SAME verdict the Trial Dir's ``result.json`` holds, never a parsed
    trace. Trials keep the order Opik returned them in; an item with no ``task_id`` is skipped.
    """
    grouped: dict[str, list[TrialOutcome]] = {}
    difficulties: dict[str, str] = {}
    for test_result in test_results:
        test_case = getattr(test_result, "test_case", None)
        content = getattr(test_case, "dataset_item_content", None) or {}
        task_id = content.get("task_id") if isinstance(content, dict) else None
        if not isinstance(task_id, str):
            continue
        output = getattr(test_case, "task_output", None) or {}
        grouped.setdefault(task_id, []).append(_outcome(output))
        difficulties.setdefault(task_id, str(content.get("difficulty", "unknown")))
    return [
        TaskTrials(task_id=task_id, difficulty=difficulties[task_id], outcomes=tuple(outcomes))
        for task_id, outcomes in grouped.items()
    ]


def _outcome(output: Any) -> TrialOutcome:
    """One trial payload as a :class:`TrialOutcome`; an unusable payload is an Infra Error."""
    if not isinstance(output, dict):
        return TrialOutcome(status="infra_error")
    return TrialOutcome(
        status=str(output.get("status", "infra_error")),
        reward=_as_float(output.get("reward")),
        cost_usd=_as_float(output.get("cost_usd")),
        input_tokens=_as_int(output.get("input_tokens")),
        output_tokens=_as_int(output.get("output_tokens")),
        run_seconds=_as_float(output.get("run_seconds")),
        started_at=_as_datetime(output.get("started_at")),
        finished_at=_as_datetime(output.get("finished_at")),
    )


def _as_float(value: Any) -> float | None:
    """A float coercion that reads anything non-numeric (including ``bool``) as ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value: Any) -> int:
    """A token count: a non-negative int, anything else (``None``, a bool, a string) as ``0``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _as_datetime(value: Any) -> datetime | None:
    """An ISO-8601 stamp back as an AWARE ``datetime``, or ``None`` for anything else.

    The payload round-trips through Opik as JSON, so the stamp comes back a string; a naive one (no
    offset) is rejected rather than guessed at, per the project's aware-UTC rule.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def summarize(result: Any, *, trials: int) -> BenchmarkSummary:
    """Fold an Opik ``EvaluationResult`` into the summary the Rich table renders (ADR-0022 §6).

    Graceful on a malformed result: a ``test_results`` that is not a list (a mock, a partial result)
    yields an empty summary rather than raising, because the CLI prints the table unconditionally.
    """
    return summarize_tasks(task_trials(_test_results(result)), trials=trials)


def _test_results(result: Any) -> list[Any]:
    """The ``test_results`` list off a result object, or ``[]`` for a malformed / mock result."""
    test_results = getattr(result, "test_results", None)
    return list(test_results) if isinstance(test_results, (list, tuple)) else []


# --- experiment_scoring_functions: the aggregates ON the Experiment row (opik 2.2.36) ---
#
# Opik 2.2.36 calls each with the run's ``List[TestResult]`` and expects one ``ScoreResult`` (or a
# list); it logs them onto the Experiment and SWALLOWS a raising function with a warning, so each one
# below is total. All of them macro-average over tasks — every task weighs the same regardless of how
# many of its trials the harness lost (ADR-0022 §4,§6).


def _total(test_results: Sequence[TestResult]) -> Rollup:
    """The whole run folded into one row — the group every experiment score reports."""
    tasks = task_trials(test_results)
    k = max((len(task.outcomes) for task in tasks), default=0)
    return summarize_tasks(tasks, trials=k).total


def pass_at_1_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """Macro-averaged pass@1 over the tasks' GRADED trials.

    Not redundant with Opik's own mean of the ``reward`` metric: that is a micro-average over every
    graded trial, so the two diverge as soon as one task loses trials to an Infra Error.
    """
    total = _total(test_results)
    return ScoreResult(
        name="pass_at_1",
        value=total.pass_at_1,
        reason=f"mean pass rate over {total.graded} graded trial(s) of {total.tasks} task(s).",
    )


def pass_at_k_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """Macro-averaged pass@k — Harbor's unbiased estimator, i.e. "solved in at least one trial"."""
    total = _total(test_results)
    return ScoreResult(
        name="pass_at_k",
        value=total.pass_at_k,
        reason=f"{total.tasks} task(s) solved in at least one of their graded trials.",
    )


def pass_hat_k_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """Macro-averaged pass^k — the reliability bar: solved in EVERY graded trial."""
    total = _total(test_results)
    return ScoreResult(
        name="pass_hat_k",
        value=total.pass_hat_k,
        reason=f"{total.tasks} task(s) solved in every one of their graded trials.",
    )


def flaky_rate_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """The fraction of tasks that passed some but not all of their graded trials."""
    total = _total(test_results)
    return ScoreResult(
        name="flaky_rate",
        value=total.flaky_rate,
        reason=f"tasks with a partial pass out of {total.tasks} task(s).",
    )


def infra_error_rate_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """The fraction of trials the HARNESS lost — a run far from 0 measures the harness, not decode."""
    total = _total(test_results)
    return ScoreResult(
        name="infra_error_rate",
        value=total.infra_error_rate,
        reason=f"{total.infra_errors} of {total.trials} trial(s) never reached a reward.",
    )


def mean_cost_usd_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """Mean dollar cost per trial, or a FAILED score when the provider priced nothing."""
    total = _total(test_results)
    return _cost_score("mean_cost_usd", total.mean_cost_usd, f"per trial over {total.trials}.")


def success_per_dollar_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """Wins per dollar spent, or a FAILED score when the provider priced nothing."""
    total = _total(test_results)
    return _cost_score(
        "success_per_dollar", total.success_per_dollar, f"over {total.graded} graded trial(s)."
    )


def _cost_score(name: str, value: float | None, detail: str) -> ScoreResult:
    """A money score, or ``scoring_failed`` when nothing was priced — a 0 would read as "free"."""
    if value is None:
        return ScoreResult(
            name=name,
            value=0.0,
            scoring_failed=True,
            reason="the provider reported no cost for this run.",
        )
    return ScoreResult(name=name, value=value, reason=detail)


# --- The two spend axes a price multiplies (ADR-0022 Amendment §14) ---
#
# Raw measurements, never a dollar figure the harness cannot know: a pay-per-token route (OpenRouter)
# bills the tokens, a pay-per-GPU-hour endpoint (Modal) bills the time it is kept warm. Each is the
# same number the CLI's spend line prints, so the terminal and the experiment row never disagree.


def input_tokens_total_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """Input tokens summed over every trial — times the route's input $/Mtok for a per-token bill."""
    total = _total(test_results)
    return ScoreResult(
        name="input_tokens_total",
        value=float(total.input_tokens),
        reason=f"input tokens summed over {total.trials} trial(s) (the agent's own requests).",
    )


def output_tokens_total_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """Output tokens summed over every trial — times the route's output $/Mtok for a per-token bill."""
    total = _total(test_results)
    return ScoreResult(
        name="output_tokens_total",
        value=float(total.output_tokens),
        reason=f"output tokens summed over {total.trials} trial(s) (the agent's own requests).",
    )


def tokens_mean_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """Mean input+output tokens per trial — the size of one attempt, comparable across ``--trials``."""
    total = _total(test_results)
    return ScoreResult(
        name="tokens_mean",
        value=total.mean_tokens,
        reason=f"input+output tokens per trial, over {total.trials} trial(s).",
    )


def wall_clock_seconds_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """First trial start to last trial end — times $/hour for a warm pay-per-compute endpoint's bill."""
    total = _total(test_results)
    return _time_score(
        "wall_clock_seconds",
        total.wall_clock_seconds,
        f"first trial start to last trial end over {total.trials} trial(s); "
        "a span, so it depends on --threads (experiment_config.threads).",
    )


def run_seconds_total_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """The agent's ``run`` phase summed over every trial — the time the model was being driven."""
    total = _total(test_results)
    return _time_score(
        "run_seconds_total",
        total.run_seconds,
        f"`decode run` seconds summed over {total.trials} trial(s), seed and verify excluded.",
    )


def run_seconds_mean_score(test_results: Sequence[TestResult]) -> ScoreResult:
    """Mean ``decode run`` seconds per trial — how long one attempt holds the model."""
    total = _total(test_results)
    return _time_score(
        "run_seconds_mean",
        total.mean_run_seconds,
        f"`decode run` seconds per trial, over {total.trials} trial(s).",
    )


def _time_score(name: str, value: float | None, detail: str) -> ScoreResult:
    """A time score, or ``scoring_failed`` when nothing ran — a 0 would read as instant."""
    if value is None:
        return ScoreResult(
            name=name,
            value=0.0,
            scoring_failed=True,
            reason="no trial ran, so there is no time to report.",
        )
    return ScoreResult(name=name, value=value, reason=detail)


# The list handed to ``evaluate(experiment_scoring_functions=...)`` — ADR-0022 §6's five, plus the
# pass@1 and flakiness the glossary's pass@k row names, plus the two spend axes of Amendment §14.
EXPERIMENT_SCORING_FUNCTIONS: list[Callable[[Sequence[TestResult]], ScoreResult]] = [
    pass_at_1_score,
    pass_at_k_score,
    pass_hat_k_score,
    flaky_rate_score,
    success_per_dollar_score,
    mean_cost_usd_score,
    infra_error_rate_score,
    input_tokens_total_score,
    output_tokens_total_score,
    tokens_mean_score,
    wall_clock_seconds_score,
    run_seconds_total_score,
    run_seconds_mean_score,
]
