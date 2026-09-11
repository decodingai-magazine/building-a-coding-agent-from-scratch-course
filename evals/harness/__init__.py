"""The eval harness: the one in-process agent driver every track reuses (ADR-0017 §4).

:func:`~evals.harness.driver.run_agent_once` drives the REAL ``build_agent()`` + ``Runner`` and
returns an :class:`~evals.harness.driver.EvalRunRecord` whose tool calls come from the message
history's ``ToolCallPart``s and whose usage is summed from each ``ModelResponse`` — never parsed
from Opik traces. The benchmark half of the harness is the format-v2 Benchmark Task contract (ADR-0022 §2): the
loader, the Seed Repo builder (:func:`~evals.harness.seed.seed_task_repo`) and the host-side Verifier
runner (:func:`~evals.harness.oracle_sanity.run_verifier`) the ``make ci`` oracle gate drives.
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

__all__ = [
    "BenchmarkTask",
    "BenchmarkTaskError",
    "DiffLinesMetric",
    "EvalRunRecord",
    "MaxStepsMetric",
    "SeedError",
    "SeedInfo",
    "ToolCallRecord",
    "ToolCalledMetric",
    "ToolNotCalledMetric",
    "VerifierResult",
    "judge_model",
    "load_benchmark_task",
    "load_benchmark_tasks",
    "make_judge",
    "run_agent_once",
    "run_agent_once_sync",
    "run_verifier",
    "seed_task_repo",
]
