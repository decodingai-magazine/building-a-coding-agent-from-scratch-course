"""The eval harness: the one in-process agent driver every track reuses (ADR-0017 §4).

:func:`~evals.harness.driver.run_agent_once` drives the REAL ``build_agent()`` + ``Runner`` and
returns an :class:`~evals.harness.driver.EvalRunRecord` whose tool calls come from the message
history's ``ToolCallPart``s and whose usage is summed from each ``ModelResponse`` — never parsed
from Opik traces. It is the REGRESSION track's engine (ADR-0022 §1 keeps it there).

The BENCHMARK half is the format-v2 Benchmark Task contract (ADR-0022 §2) plus the Trial:
the loader, the Seed Repo builder (:func:`~evals.harness.seed.seed_task_repo`), the host-side grading
step (:func:`~evals.harness.verifier.grade_checkout`, driven both by the ``make ci`` oracle gate and
by a real trial) and :func:`~evals.harness.trial.run_trial`, which measures the shipped runtime by
driving ``decode run`` in a subprocess.
"""

from __future__ import annotations

from evals.harness.driver import (
    EvalRunRecord,
    ToolCallRecord,
    run_agent_once,
    run_agent_once_sync,
)
from evals.harness.judges import judge_model, make_judge
from evals.harness.metrics import (
    DiffLinesMetric,
    MaxStepsMetric,
    RewardMetric,
    ToolCalledMetric,
    ToolNotCalledMetric,
)
from evals.harness.oracle_sanity import VerifierResult, run_verifier
from evals.harness.seed import SeedError, SeedInfo, seed_task_repo
from evals.harness.task_loader import (
    BenchmarkTask,
    BenchmarkTaskError,
    load_benchmark_task,
    load_benchmark_tasks,
)
from evals.harness.trial import TrialResult, TrialStatus, child_env, run_trial
from evals.harness.verifier import grade_checkout

__all__ = [
    "BenchmarkTask",
    "BenchmarkTaskError",
    "DiffLinesMetric",
    "EvalRunRecord",
    "MaxStepsMetric",
    "RewardMetric",
    "SeedError",
    "SeedInfo",
    "ToolCallRecord",
    "ToolCalledMetric",
    "ToolNotCalledMetric",
    "TrialResult",
    "TrialStatus",
    "VerifierResult",
    "child_env",
    "grade_checkout",
    "judge_model",
    "load_benchmark_task",
    "load_benchmark_tasks",
    "make_judge",
    "run_agent_once",
    "run_agent_once_sync",
    "run_trial",
    "run_verifier",
    "seed_task_repo",
]
