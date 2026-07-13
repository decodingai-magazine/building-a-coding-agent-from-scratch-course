"""The eval harness: the one in-process agent driver every track reuses (ADR-0017 §4).

:func:`~evals.harness.driver.run_agent_once` drives the REAL ``build_agent()`` + ``Runner`` and
returns an :class:`~evals.harness.driver.EvalRunRecord` whose tool calls come from the message
history's ``ToolCallPart``s and whose usage is summed from each ``ModelResponse`` — never parsed
from Opik traces. Metrics, judges, datasets, and the sandbox lifecycle land in later tasks.
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
    VerifyOracleMetric,
)
from evals.harness.oracle_sanity import OracleResult, run_oracle
from evals.harness.task_loader import (
    BenchmarkTask,
    BenchmarkTaskError,
    JudgeSpec,
    load_benchmark_task,
    load_benchmark_tasks,
)

__all__ = [
    "BenchmarkTask",
    "BenchmarkTaskError",
    "DiffLinesMetric",
    "EvalRunRecord",
    "JudgeSpec",
    "MaxStepsMetric",
    "OracleResult",
    "ToolCallRecord",
    "ToolCalledMetric",
    "ToolNotCalledMetric",
    "VerifyOracleMetric",
    "judge_model",
    "load_benchmark_task",
    "load_benchmark_tasks",
    "make_judge",
    "run_agent_once",
    "run_agent_once_sync",
    "run_oracle",
]
