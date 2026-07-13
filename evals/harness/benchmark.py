"""The Opik glue that turns benchmark tasks into an ``evaluate()`` experiment (ADR-0017 §3,4,5; task 106).

Two pieces sit on top of the sandbox lifecycle (:mod:`evals.harness.sandbox`) and the driver
(:mod:`evals.harness.driver`):

* :func:`make_benchmark_task_fn` builds the sync Opik task fn — for one dataset item it runs the real
  agent in a fresh sandbox Workspace under a BYPASS gate, grades it with the hidden oracle, and
  returns the flat payload the landed code metrics (:mod:`evals.harness.metrics`) and per-task judges
  consume: ``output`` / ``tool_calls`` / ``steps`` / token counts / a ``verify`` result / ``max_steps``
  / ``agent_error`` / ``infra_error``. The task fn NEVER raises: a crashed agent run grades as
  fail-with-reason (``agent_error``), and a sandbox that never came up — daemon down, bad creds —
  grades as fail-with-reason too (``infra_error``), because Opik's ``evaluate`` gives task fns no
  per-item isolation, so one raise would abort the whole experiment.
* :func:`run_benchmark` loads + filters the tasks, upserts them into ``decode-benchmark-v1``, and
  calls ``opik.evaluation.evaluate`` with the code metrics + (single-task) judges,
  ``experiment_config`` carrying the agent model, provider, git sha and sandbox, and
  ``project_name=settings.eval_project_name`` so eval runs never pollute live REPL tracing.

The sandbox seam is a PROCESS-GLOBAL (one ``decode.tools.bash`` executor), so the benchmark runs
``evaluate(task_threads=1)`` — concurrent task fns would race on that shared seam. A per-run executor
seam is the documented upgrade path if benchmark wall-time ever bites.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from decode.config.settings import settings
from decode.permissions.types import PermissionMode
from evals.harness.driver import run_agent_once_sync
from evals.harness.sandbox import benchmark_sandbox
from evals.harness.task_loader import BenchmarkTask, load_benchmark_tasks

if TYPE_CHECKING:
    import opik
    from opik.evaluation.evaluation_result import EvaluationResult

    from evals.harness.driver import EvalRunRecord
    from evals.harness.task_loader import Difficulty

logger = logging.getLogger(__name__)

# The Opik task fn Opik hands one dataset item and expects one flat output dict back.
BenchmarkTaskFn = Callable[[dict[str, Any]], dict[str, Any]]


class BenchmarkSelectionError(Exception):
    """No benchmark task matched the ``--task`` / ``--difficulty`` filters — a loud, friendly stop."""


def make_benchmark_task_fn(
    tasks_by_id: dict[str, BenchmarkTask], *, sandbox: str = "docker"
) -> BenchmarkTaskFn:
    """Build the sync Opik task fn that runs + grades one item's task (ADR-0017 §3,4,5).

    The returned closure looks the task up by ``item["task_id"]``, runs the full sandbox lifecycle,
    and returns the metric-facing payload. Sync because Opik ``evaluate()`` task fns cannot be async.
    """

    def benchmark_task_fn(item: dict[str, Any]) -> dict[str, Any]:
        task = tasks_by_id[item["task_id"]]
        return _run_and_grade(task, sandbox=sandbox)

    return benchmark_task_fn


def _run_and_grade(task: BenchmarkTask, *, sandbox: str) -> dict[str, Any]:
    """Run + grade one task, turning ANY failure into a graded payload — never a raise (ADR-0017 §3).

    Wraps the WHOLE sandbox lifecycle (creation included), because Opik's ``evaluate`` runs task fns
    in a plain list comprehension with no per-item isolation — one raised task fn aborts the entire
    experiment. So a sandbox that never came up (docker daemon down, bad modal creds) is caught here
    into ``infra_error``; the item then carries no verify result, which
    :class:`~evals.harness.metrics.VerifyOracleMetric` grades ``0.0`` with a reason. A crashed AGENT
    run (the sandbox was fine) is the narrower ``agent_error`` case, handled in
    :func:`_run_in_sandbox` so the oracle still grades the Workspace.
    """
    try:
        return _run_in_sandbox(task, sandbox=sandbox)
    except Exception as exc:  # a sandbox-lifecycle failure must not abort the whole experiment
        logger.exception("[eval] sandbox lifecycle failed for task %s", task.id)
        return _payload(
            None,
            verify_exit=None,
            verify_stdout="",
            task=task,
            agent_error=None,
            infra_error=f"sandbox lifecycle failed: {exc}",
        )


def _run_in_sandbox(task: BenchmarkTask, *, sandbox: str) -> dict[str, Any]:
    """Bring up the Workspace, run the agent BYPASS, grade with the hidden oracle (ADR-0008 §2; §5).

    The agent runs BYPASS + headless deny resolvers, capped at ``max_steps`` model requests. A raised
    agent run is caught into ``agent_error`` so the oracle STILL grades the Workspace (a crash is
    fail-with-reason, not a skipped grade). A failure to even enter the sandbox, or a failure to
    grade, propagates to :func:`_run_and_grade`'s ``infra_error`` handler. The Workspace is torn down
    either way (the ``finally`` in :func:`benchmark_sandbox`).
    """
    record: EvalRunRecord | None = None
    agent_error: str | None = None
    with benchmark_sandbox(task, sandbox=sandbox) as run:
        try:
            record = run_agent_once_sync(
                task.prompt,
                cwd=run.workspace,
                gate_mode=PermissionMode.BYPASS,
                max_requests=task.max_steps,
            )
            agent_error = record.agent_error
        except Exception as exc:  # a crashed agent still grades — don't skip the oracle
            logger.exception("[eval] agent run raised for task %s", task.id)
            agent_error = f"agent run raised: {exc}"
        verify = run.grade(task)
    return _payload(
        record,
        verify_exit=verify.exit_code,
        verify_stdout=verify.stdout,
        task=task,
        agent_error=agent_error,
        infra_error=None,
    )


def _payload(
    record: EvalRunRecord | None,
    *,
    verify_exit: int | None,
    verify_stdout: str,
    task: BenchmarkTask,
    agent_error: str | None,
    infra_error: str | None,
) -> dict[str, Any]:
    """The flat output dict the landed metrics + judges read (ADR-0017 §4).

    ``tool_calls`` is de-dataclassed to plain ``{"name", "args"}`` dicts so the payload is
    JSON-serializable for Opik storage; ``verify`` is the ``{"exit_code", "stdout"}`` shape
    :class:`~evals.harness.metrics.VerifyOracleMetric` maps. Two distinct failure channels, both
    absorbed by the metrics' ``**ignored_kwargs``: ``agent_error`` names a crashed agent run (the
    oracle still ran); ``infra_error`` names a sandbox that never came up or could not be graded, in
    which case ``verify.exit_code`` is ``None`` and the oracle metric grades ``0.0`` with a reason. A
    ``None`` record degrades every run field to its empty default.
    """
    tool_calls = (
        [{"name": call.name, "args": call.args} for call in record.tool_calls] if record else []
    )
    return {
        "output": record.output if record else "",
        "tool_calls": tool_calls,
        "steps": record.steps if record else 0,
        "input_tokens": record.input_tokens if record else 0,
        "output_tokens": record.output_tokens if record else 0,
        "verify": {"exit_code": verify_exit, "stdout": verify_stdout},
        "max_steps": task.max_steps,
        "agent_error": agent_error,
        "infra_error": infra_error,
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
    items with the code metrics + single-task judges. ``trials`` rides Opik's own ``trial_count`` axis
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
        scoring_metrics=_scoring_metrics(selected),
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


def _scoring_metrics(tasks: list[BenchmarkTask]) -> list[Any]:
    """The metric list for the run: the code oracles always, plus a single task's G-Eval judges.

    ``evaluate`` applies one metric list to every item, so per-task judges are only sound when the run
    targets exactly one task (``--task <id>``); a multi-task run uses the code metrics alone (a
    task-15 judge grading task-1 output would be meaningless — ADR-0017 §7).
    """
    from evals.harness.judges import make_judge
    from evals.harness.metrics import MaxStepsMetric, VerifyOracleMetric

    metrics: list[Any] = [VerifyOracleMetric(), MaxStepsMetric()]
    if len(tasks) == 1:
        metrics += [
            make_judge(spec.task_introduction, spec.evaluation_criteria) for spec in tasks[0].judges
        ]
    return metrics


def experiment_config(sandbox: str) -> dict[str, Any]:
    """The Opik ``experiment_config`` for a run: the agent model, provider, git sha and sandbox.

    Enough to tell two experiment rows apart by what actually changed between them (ADR-0017 §8): the
    model + provider driving the agent, the code the run was on (``git rev-parse HEAD``), and which
    sandbox rung executed it.
    """
    return {
        "agent_model": _agent_model(),
        "provider": settings.llm_provider,
        "git_sha": _git_sha(),
        "sandbox": sandbox,
    }


def _agent_model() -> str:
    """The model string the agent runs on, resolved from the active provider (mirrors the gateway)."""
    provider = settings.llm_provider
    if provider == "openrouter":
        return settings.openrouter_model
    if provider == "modal":
        return settings.modal_endpoint_model
    return settings.gemini_model


def _git_sha() -> str:
    """The current commit sha, or ``"unknown"`` if git is unavailable (never crash a benchmark on it)."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"
