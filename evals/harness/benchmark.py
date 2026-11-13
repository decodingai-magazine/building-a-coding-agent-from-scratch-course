"""Run outcome Benchmark Jobs through Langfuse experiments."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from decode.config.settings import settings
from evals.harness.aggregates import (
    BenchmarkSummary,
    Rollup,
    TaskTrials,
    TrialOutcome,
    summarize_tasks,
)
from evals.harness.datasets import (
    benchmark_items_for_dataset,
    dataset_items,
    sync_benchmark_dataset,
)
from evals.harness.langfuse_experiments import experiment_result, item_input, result_items
from evals.harness.metrics import ScoreResult
from evals.harness.task_loader import BenchmarkTask, load_benchmark_tasks

# ``git_sha`` lives with the trial runner (it stamps every ``result.json``); it is re-exported here
# because the regression track reads a run's provenance from this module (``regression.py``).
from evals.harness.trial import TrialResult, decode_version, git_sha, run_trial

if TYPE_CHECKING:
    from evals.harness.task_loader import Difficulty

logger = logging.getLogger(__name__)

# A Langfuse experiment task returns one flat trial payload.
BenchmarkTaskFn = Callable[[dict[str, Any]], dict[str, Any]]

# Where Trial Dirs land: under the Harness Home's ``.decode/`` (already git-ignored), one directory
# per Benchmark Job so a job's trials sit together (ADR-0022 §7).
RUNS_DIR_PARTS = ("evals", "runs")

# A job's default name — also its Trial Dir's parent and the Langfuse experiment name.
JOB_NAME_PREFIX = "bench"

# A job name is ONE path segment: it becomes a directory under ``.decode/evals/runs/`` and a Langfuse
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
    """What one Benchmark Job leaves behind: its experiment result and Trial Dirs."""

    result: Any
    job_dir: Path

    @property
    def experiment_name(self) -> str:
        """The Langfuse experiment name — also the Trial Dir's parent."""
        name = getattr(self.result, "name", None)
        return name if isinstance(name, str) and name else self.job_dir.name


def make_benchmark_task_fn(
    tasks_by_id: dict[str, BenchmarkTask],
    *,
    sandbox: str,
    job_dir: Path,
    model: str | None = None,
) -> BenchmarkTaskFn:
    """Build the task fn that runs + grades one item's task as a Trial (ADR-0022 §1).

    The returned closure looks the task up by ``item["task_id"]`` and runs one Trial into its own
    Trial Dir ``<job_dir>/<task-id>__<short-id>/``. The trial itself is a subprocess, so nothing
    here is loop-bound. Every failure — an
    item naming a task this checkout no longer has, a trial runner that raised despite its own
    guarantee — comes back as an ``infra_error`` payload rather than an exception.
    """

    def benchmark_task_fn(item: dict[str, Any]) -> dict[str, Any]:
        item_input_data = item_input(item) if "input" in item else item
        task_id = str(item_input_data.get("task_id"))
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
    same string is the Langfuse experiment name, so the rule keeps it readable too.
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
    and fully counted only on its Langfuse trace.

    ``session_id`` / ``kitaru_session_id`` come straight off the run's own ``--summary-json`` and are
    the ONLY join from an experiment row back to the Session the trial recorded (task 165): the
    item's Langfuse trace wraps the task fn, never the ``decode run``
    subprocess trace, so ``evals kitaru cohort from-experiment`` has nothing else to resolve by.
    """
    summary = result.summary or {}
    return {
        "task_id": result.task_id,
        "trial_id": result.trial_id,
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
    client: Any = None,
) -> BenchmarkRun:
    """Run the filtered benchmark as one Langfuse experiment (ADR-0022 §6).

    Historical dataset versions stay available, but only current task checksums are selected.
    Repeated trials use local experiment items linked to their source dataset item in metadata.
    """
    from langfuse.experiment import Evaluation

    from evals.harness.datasets import langfuse_client, task_checksum
    from evals.harness.metrics import RewardMetric

    if trials < 1:
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
    client = client or langfuse_client()
    dataset = sync_benchmark_dataset(selected, client=client)
    item_ids = _selected_item_ids(dataset, {task.id: task_checksum(task) for task in selected})
    missing = {task.id for task in selected} - set(item_ids)
    if missing:
        raise BenchmarkSelectionError(
            "the sync did not produce a matching dataset item for "
            f"{sorted(missing)} in {getattr(dataset, 'name', 'decode-benchmark')!r} — "
            "re-run `python -m evals sync --benchmark`."
        )

    task_fn = make_benchmark_task_fn(
        {task.id: task for task in all_tasks}, sandbox=sandbox, job_dir=job_dir, model=model
    )
    task_threads = threads if threads is not None else default_threads(sandbox)
    metadata = experiment_config(
        sandbox=sandbox, trials=trials, threads=task_threads, job_name=job_name, model=model
    )
    items_by_id = {str(getattr(item, "id", "")): item for item in dataset_items(dataset)}
    data: list[dict[str, Any]] = []
    for task in selected:
        source_id = item_ids[task.id]
        source = items_by_id.get(source_id)
        source_input = getattr(source, "input", None) if source is not None else None
        source_metadata = getattr(source, "metadata", None) if source is not None else None
        for trial_index in range(trials):
            data.append(
                {
                    "input": source_input
                    if isinstance(source_input, dict)
                    else {"task_id": task.id, "instruction": task.instruction},
                    "metadata": {
                        **(source_metadata if isinstance(source_metadata, dict) else {}),
                        "task_id": task.id,
                        "difficulty": task.difficulty,
                        "source_dataset_item_id": source_id,
                        "trial_index": trial_index,
                    },
                }
            )

    def reward_evaluator(*, output: Any, **_: Any) -> Any:
        score = RewardMetric().score(**(output if isinstance(output, dict) else {}))
        return Evaluation(
            name=score.name,
            value=score.value,
            comment=score.reason,
            metadata={"scoring_failed": score.scoring_failed},
        )

    run_evaluators = [_run_evaluator(score_fn) for score_fn in EXPERIMENT_SCORING_FUNCTIONS]
    raw_result = client.run_experiment(
        name=job_name,
        run_name=job_name,
        description=f"Benchmark: {job_name}",
        data=data,
        task=lambda *, item, **_: task_fn(
            {
                **(item.get("input", {}) if isinstance(item, dict) else {}),
                **(item.get("metadata", {}) if isinstance(item, dict) else {}),
            }
        ),
        evaluators=[reward_evaluator],
        run_evaluators=run_evaluators,
        max_concurrency=task_threads,
        metadata=metadata,
    )
    return BenchmarkRun(
        result=experiment_result(raw_result, name=job_name, metadata=metadata),
        job_dir=job_dir,
    )


def _run_evaluator(score_fn: Callable[[Sequence[Any]], ScoreResult]) -> Callable[..., Any]:
    """Adapt one local aggregate to Langfuse's experiment-level evaluator contract."""
    from types import SimpleNamespace

    from langfuse.experiment import Evaluation

    def evaluate(*, item_results: list[Any], **_: Any) -> Any:
        adapted = result_items(SimpleNamespace(item_results=item_results))
        score = score_fn(adapted)
        return Evaluation(
            name=score.name,
            value=score.value,
            comment=score.reason,
            metadata={"scoring_failed": score.scoring_failed},
        )

    return evaluate


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
    """Return current dataset item ids by task/checksum."""
    return benchmark_items_for_dataset(dataset, checksums)


def evaluate_project_name(dataset: Any) -> str | None:
    """Retained for callers; Langfuse projects are selected by client credentials."""
    return None


def experiment_config(
    *, sandbox: str, trials: int, threads: int, job_name: str, model: str | None
) -> dict[str, Any]:
    """Experiment provenance stored as Langfuse metadata (ADR-0022 §6).

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


# --- Experiment-result adapter: read the run as a trial matrix ---


def task_trials(test_results: Sequence[Any]) -> list[TaskTrials]:
    """Group Langfuse experiment items into one :class:`TaskTrials` per Benchmark Task.

    The task fn's payload (``status`` / ``reward`` / ``cost_usd`` / the tokens / the time stamps)
    rides on ``test_case.task_output`` and the slice labels on ``test_case.dataset_item_content`` —
    so the aggregates read the SAME verdict the Trial Dir's ``result.json`` holds, never a parsed
    trace. Trials keep the returned order; an item with no ``task_id`` is skipped.
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

    The payload round-trips through the API as JSON, so the stamp comes back a string; a naive one (no
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
    """Fold a local experiment result into the summary the Rich table renders (ADR-0022 §6).

    Graceful on a malformed result: a ``test_results`` that is not a list (a mock, a partial result)
    yields an empty summary rather than raising, because the CLI prints the table unconditionally.
    """
    return summarize_tasks(task_trials(_test_results(result)), trials=trials)


def _test_results(result: Any) -> list[Any]:
    """The ``test_results`` list off a result object, or ``[]`` for a malformed / mock result."""
    test_results = getattr(result, "test_results", None)
    return list(test_results) if isinstance(test_results, (list, tuple)) else []


# --- run evaluators: aggregates on the Langfuse experiment row ---
#
# Langfuse run evaluators return one or more Evaluation results. Each adapter is total so one
# malformed item cannot fail the run. All aggregates macro-average over tasks (ADR-0022 §4,§6).


def _total(test_results: Sequence[Any]) -> Rollup:
    """The whole run folded into one row — the group every experiment score reports."""
    tasks = task_trials(test_results)
    k = max((len(task.outcomes) for task in tasks), default=0)
    return summarize_tasks(tasks, trials=k).total


def pass_at_1_score(test_results: Sequence[Any]) -> ScoreResult:
    """Macro-averaged pass@1 over the tasks' GRADED trials.

    Not redundant with the item's mean ``reward`` metric: that is a micro-average over every
    graded trial, so the two diverge as soon as one task loses trials to an Infra Error.
    """
    total = _total(test_results)
    return ScoreResult(
        name="pass_at_1",
        value=total.pass_at_1,
        reason=f"mean pass rate over {total.graded} graded trial(s) of {total.tasks} task(s).",
    )


def pass_at_k_score(test_results: Sequence[Any]) -> ScoreResult:
    """Macro-averaged pass@k — Harbor's unbiased estimator, i.e. "solved in at least one trial"."""
    total = _total(test_results)
    return ScoreResult(
        name="pass_at_k",
        value=total.pass_at_k,
        reason=f"{total.tasks} task(s) solved in at least one of their graded trials.",
    )


def pass_hat_k_score(test_results: Sequence[Any]) -> ScoreResult:
    """Macro-averaged pass^k — the reliability bar: solved in EVERY graded trial."""
    total = _total(test_results)
    return ScoreResult(
        name="pass_hat_k",
        value=total.pass_hat_k,
        reason=f"{total.tasks} task(s) solved in every one of their graded trials.",
    )


def flaky_rate_score(test_results: Sequence[Any]) -> ScoreResult:
    """The fraction of tasks that passed some but not all of their graded trials."""
    total = _total(test_results)
    return ScoreResult(
        name="flaky_rate",
        value=total.flaky_rate,
        reason=f"tasks with a partial pass out of {total.tasks} task(s).",
    )


def infra_error_rate_score(test_results: Sequence[Any]) -> ScoreResult:
    """The fraction of trials the HARNESS lost — a run far from 0 measures the harness, not decode."""
    total = _total(test_results)
    return ScoreResult(
        name="infra_error_rate",
        value=total.infra_error_rate,
        reason=f"{total.infra_errors} of {total.trials} trial(s) never reached a reward.",
    )


def mean_cost_usd_score(test_results: Sequence[Any]) -> ScoreResult:
    """Mean dollar cost per trial, or a FAILED score when the provider priced nothing."""
    total = _total(test_results)
    return _cost_score("mean_cost_usd", total.mean_cost_usd, f"per trial over {total.trials}.")


def success_per_dollar_score(test_results: Sequence[Any]) -> ScoreResult:
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


def input_tokens_total_score(test_results: Sequence[Any]) -> ScoreResult:
    """Input tokens summed over every trial — times the route's input $/Mtok for a per-token bill."""
    total = _total(test_results)
    return ScoreResult(
        name="input_tokens_total",
        value=float(total.input_tokens),
        reason=f"input tokens summed over {total.trials} trial(s) (the agent's own requests).",
    )


def output_tokens_total_score(test_results: Sequence[Any]) -> ScoreResult:
    """Output tokens summed over every trial — times the route's output $/Mtok for a per-token bill."""
    total = _total(test_results)
    return ScoreResult(
        name="output_tokens_total",
        value=float(total.output_tokens),
        reason=f"output tokens summed over {total.trials} trial(s) (the agent's own requests).",
    )


def tokens_mean_score(test_results: Sequence[Any]) -> ScoreResult:
    """Mean input+output tokens per trial — the size of one attempt, comparable across ``--trials``."""
    total = _total(test_results)
    return ScoreResult(
        name="tokens_mean",
        value=total.mean_tokens,
        reason=f"input+output tokens per trial, over {total.trials} trial(s).",
    )


def wall_clock_seconds_score(test_results: Sequence[Any]) -> ScoreResult:
    """First trial start to last trial end — times $/hour for a warm pay-per-compute endpoint's bill."""
    total = _total(test_results)
    return _time_score(
        "wall_clock_seconds",
        total.wall_clock_seconds,
        f"first trial start to last trial end over {total.trials} trial(s); "
        "a span, so it depends on --threads (experiment_config.threads).",
    )


def run_seconds_total_score(test_results: Sequence[Any]) -> ScoreResult:
    """The agent's ``run`` phase summed over every trial — the time the model was being driven."""
    total = _total(test_results)
    return _time_score(
        "run_seconds_total",
        total.run_seconds,
        f"`decode run` seconds summed over {total.trials} trial(s), seed and verify excluded.",
    )


def run_seconds_mean_score(test_results: Sequence[Any]) -> ScoreResult:
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
EXPERIMENT_SCORING_FUNCTIONS: list[Callable[[Sequence[Any]], ScoreResult]] = [
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
