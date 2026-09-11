"""The Opik glue that turns benchmark tasks into an ``evaluate()`` experiment (ADR-0022 §1,§6).

Two pieces sit on top of the trial runner (:mod:`evals.harness.trial`):

* :func:`make_benchmark_task_fn` builds the sync Opik task fn — for one dataset item it runs ONE
  Benchmark Trial (a subprocess ``decode run`` against a fresh Seed Repo, graded host-side on a
  pristine clone) and returns the flat payload the metrics consume. The task fn NEVER raises, because
  Opik's ``evaluate`` gives task fns no per-item isolation and one raise would abort the whole
  experiment; :func:`~evals.harness.trial.run_trial` already guarantees that by returning an
  ``infra_error`` verdict instead of propagating.
* :func:`run_benchmark` loads + filters the tasks, upserts them into the Opik dataset, and calls
  ``opik.evaluation.evaluate`` with the code metrics, ``experiment_config`` carrying the agent model,
  provider, git sha and sandbox, and ``project_name=settings.eval_project_name`` so eval runs never
  pollute live REPL tracing.

ADR-0022 §1 replaced the in-process driver and the sandbox-seam lifecycle this module used to drive
(ADR-0017 §3,4): a Trial now measures the SHIPPED runtime. The Opik surface itself — the v2 dataset,
the reward metric, ``experiment_scoring_functions`` and the parallelism policy subprocess trials make
safe — lands in task 161; what is here is the wiring that keeps the command runnable in between.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from decode.config.settings import settings
from evals.harness.task_loader import BenchmarkTask, load_benchmark_tasks

# ``git_sha`` lives with the trial runner (it stamps every ``result.json``); it is re-exported here
# because the regression track reads a run's provenance from this module (``regression.py``).
from evals.harness.trial import TrialResult, git_sha, run_trial

if TYPE_CHECKING:
    import opik
    from opik.evaluation.evaluation_result import EvaluationResult

    from evals.harness.task_loader import Difficulty

logger = logging.getLogger(__name__)

# The Opik task fn Opik hands one dataset item and expects one flat output dict back.
BenchmarkTaskFn = Callable[[dict[str, Any]], dict[str, Any]]

# Where Trial Dirs land: under the Harness Home's ``.decode/`` (already git-ignored), one directory
# per benchmark run so a job's trials sit together (ADR-0022 §7).
RUNS_DIR_PARTS = ("evals", "runs")


class BenchmarkSelectionError(Exception):
    """No benchmark task matched the ``--task`` / ``--difficulty`` filters — a loud, friendly stop."""


def make_benchmark_task_fn(
    tasks_by_id: dict[str, BenchmarkTask],
    *,
    sandbox: str = "docker",
    job_dir: Path | None = None,
) -> BenchmarkTaskFn:
    """Build the sync Opik task fn that runs + grades one item's task as a Trial (ADR-0022 §1).

    The returned closure looks the task up by ``item["task_id"]`` and runs one Trial into its own
    Trial Dir under ``job_dir``. Sync because Opik ``evaluate()`` task fns cannot be async; the trial
    itself is a subprocess, so nothing here is loop-bound.
    """
    resolved_job_dir = job_dir if job_dir is not None else new_job_dir()

    def benchmark_task_fn(item: dict[str, Any]) -> dict[str, Any]:
        task = tasks_by_id[item["task_id"]]
        result = run_trial(
            task,
            sandbox=sandbox,  # type: ignore[arg-type]  (the cli constrains it to docker|modal)
            job_dir=resolved_job_dir,
            trial_id=uuid4().hex[:8],
        )
        return trial_payload(result, task)

    return benchmark_task_fn


def new_job_dir() -> Path:
    """A fresh ``<harness home>/.decode/evals/runs/<utc-timestamp>/`` for one benchmark run."""
    job = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(settings.decode_dir).joinpath(*RUNS_DIR_PARTS, job)


def trial_payload(result: TrialResult, task: BenchmarkTask) -> dict[str, Any]:
    """The flat output dict the metrics read, straight off one :class:`TrialResult`.

    ``reward`` / ``status`` / ``reason`` are the grade of record (ADR-0022 §3,§4); ``steps`` and
    ``max_steps`` keep :class:`~evals.harness.metrics.MaxStepsMetric` honest; ``trial_dir`` points a
    human at the evidence. ``infra_error`` carries the reason ONLY when the harness was at fault, so
    an excluded trial is distinguishable from a lost one without re-deriving the taxonomy.
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
        "trial_dir": str(result.trial_dir),
        "infra_error": result.reason if result.status == "infra_error" else None,
    }


def run_benchmark(
    *,
    task_id: str | None = None,
    difficulty: Difficulty | None = None,
    sandbox: str = "docker",
    nb_samples: int | None = None,
    trials: int = 1,
    client: opik.Opik | None = None,
) -> EvaluationResult:
    """Run the filtered benchmark as one Opik experiment and return its result (ADR-0017 §3,4,5,8).

    Loads every task, applies the ``--task`` / ``--difficulty`` filters, upserts the selection into
    ``decode-benchmark-v1``, and calls ``evaluate`` scoped (via ``dataset_item_ids``) to just those
    items with the code metrics. ``trials`` rides Opik's own ``trial_count`` axis
    (``k`` runs per item); after the run, the trial aggregates (pass@1/pass@k/pass^k/flakiness + cost,
    :mod:`evals.harness.aggregates`) are attached to the experiment as trace feedback scores — the
    1.9.8 stand-in for the removed ``experiment_scoring_functions`` (ADR-0017 §8; task-107 log). A
    failed attach never sinks a completed run. ``experiment_config`` records the agent model, provider,
    git sha and sandbox; ``project_name`` is ``settings.eval_project_name`` so live tracing stays
    clean. Runs single-threaded — the ``bash`` executor seam is process-global. Raises
    :class:`BenchmarkSelectionError` when nothing matches.
    """
    import opik
    from opik.evaluation import evaluate

    from evals.harness.datasets import sync_benchmark_dataset
    from evals.harness.metrics import MaxStepsMetric

    if trials < 1:
        # Guard BEFORE evaluate: opik's evaluate(trial_count<1) range()-loops zero times and returns
        # cleanly, which would report a nonsense pass@<0> over zero real trials (ADR-0017 §8).
        raise ValueError(f"trials must be >= 1, got {trials}.")

    all_tasks = load_benchmark_tasks()
    selected = _select_tasks(all_tasks, task_id=task_id, difficulty=difficulty)
    if not selected:
        raise BenchmarkSelectionError(
            f"no benchmark task matched (task={task_id!r}, difficulty={difficulty!r}); "
            f"{len(all_tasks)} task(s) available."
        )

    client = client or opik.Opik()
    dataset = sync_benchmark_dataset(selected, client=client)
    item_ids = _selected_item_ids(dataset, {task.id for task in selected})

    tasks_by_id = {task.id: task for task in all_tasks}
    task_fn = make_benchmark_task_fn(tasks_by_id, sandbox=sandbox)
    result = evaluate(
        dataset=dataset,
        task=task_fn,
        scoring_metrics=[MaxStepsMetric()],
        experiment_config=experiment_config(sandbox),
        project_name=settings.eval_project_name,
        nb_samples=nb_samples,
        dataset_item_ids=item_ids or None,
        task_threads=1,
        trial_count=trials,
    )
    _attach_aggregates(client, result, trials)
    return result


def _attach_aggregates(client: opik.Opik, result: EvaluationResult, trials: int) -> None:
    """Log the post-hoc trial aggregates onto the experiment's traces — best-effort (ADR-0017 §8).

    Opik 1.9.8 has no ``experiment_scoring_functions``, so pass@k / pass^k / flakiness / cost ride the
    experiment as per-item trace feedback scores that Opik averages onto the experiment row. A logging
    failure (Opik unreachable, an unexpected result shape) is swallowed with a warning: the benchmark
    already ran and its result must still return.
    """
    from evals.harness.aggregates import attach_experiment_aggregates, summarize

    try:
        summary = summarize(result, trials=trials)
        attach_experiment_aggregates(
            client, result, summary, project_name=settings.eval_project_name
        )
    except Exception:  # a completed run must not fail on its post-hoc bookkeeping
        logger.exception("[eval] failed to attach trial aggregates to the experiment")


def _select_tasks(
    tasks: list[BenchmarkTask], *, task_id: str | None, difficulty: Difficulty | None
) -> list[BenchmarkTask]:
    """Filter loaded tasks by exact ``task_id`` and/or ``difficulty`` (both optional, AND-combined)."""
    selected = tasks
    if task_id is not None:
        selected = [task for task in selected if task.id == task_id]
    if difficulty is not None:
        selected = [task for task in selected if task.difficulty == difficulty]
    return selected


def _selected_item_ids(dataset: Any, selected_ids: set[str]) -> list[str]:
    """The dataset item ids whose ``task_id`` is in ``selected_ids`` — scopes ``evaluate`` to the run.

    Reads the just-synced dataset's items and keeps those the filters selected, so a run over the
    shared ``decode-benchmark-v1`` dataset executes only the chosen tasks. An item missing an ``id``
    (an unexpected Opik shape) is skipped rather than crashing the run.
    """
    items = dataset.get_items()
    return [
        item["id"]
        for item in items
        if item.get("task_id") in selected_ids and item.get("id") is not None
    ]


def experiment_config(sandbox: str) -> dict[str, Any]:
    """The Opik ``experiment_config`` for a run: the agent model, provider, git sha and sandbox.

    Enough to tell two experiment rows apart by what actually changed between them (ADR-0017 §8): the
    model + provider driving the agent, the code the run was on (``git rev-parse HEAD``), and which
    sandbox rung executed it.
    """
    return {
        "agent_model": agent_model(),
        "provider": settings.llm_provider,
        "git_sha": git_sha(),
        "sandbox": sandbox,
    }


def agent_model() -> str:
    """The model string the agent runs on, resolved from the active provider (mirrors the gateway).

    Public so the regression harness reuses it (:func:`evals.harness.regression.experiment_config`)
    instead of reaching for a private name across modules — one resolver, both experiment tracks.
    """
    provider = settings.llm_provider
    if provider == "openrouter":
        return settings.openrouter_model
    if provider == "modal":
        return settings.modal_endpoint_model
    return settings.gemini_model
