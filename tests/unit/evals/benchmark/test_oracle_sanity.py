"""Oracle-sanity harness: every benchmark oracle graded honestly, both directions (ADR-0017 §5).

A hidden ``verify.sh`` that always passed (or always failed) would grade every agent version the
same. The guard, run in ordinary CI, is to reproduce the grade-time Workspace host-side and assert
each oracle answers correctly BOTH ways: PASS over the gold ``solution/`` overlay, FAIL over the
untouched ``setup/`` seed. Parametrized over the fixture task plus every authored benchmark task
(tasks 108-110), so a broken oracle can never silently land.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.oracle_sanity import run_oracle
from evals.harness.task_loader import (
    BENCHMARK_TASKS_DIR,
    BenchmarkTask,
    load_benchmark_tasks,
)

# ``tests/unit/evals/benchmark/`` -> ``tests/unit/evals/`` -> the committed fixture tasks tree.
_FIXTURE_TASKS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tasks"


def _sanity_tasks() -> list[BenchmarkTask]:
    """Every oracle to keep honest: the on-disk fixture task plus the real authored benchmark set.

    The real set (``evals/benchmark/tasks/``) is empty until tasks 108-110; the fixture guarantees
    the harness itself always runs.
    """
    return load_benchmark_tasks(_FIXTURE_TASKS_DIR) + load_benchmark_tasks(BENCHMARK_TASKS_DIR)


_SANITY_TASKS = _sanity_tasks()


@pytest.mark.parametrize("task", _SANITY_TASKS, ids=lambda task: task.id)
def test_oracle_passes_on_the_gold_solution(task: BenchmarkTask, tmp_path: Path) -> None:
    result = run_oracle(task, tmp_path, with_solution=True)

    assert result.passed, (
        f"{task.id}: verify.sh should PASS on solution/ but exited {result.exit_code}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.parametrize("task", _SANITY_TASKS, ids=lambda task: task.id)
def test_oracle_fails_on_the_untouched_setup(task: BenchmarkTask, tmp_path: Path) -> None:
    result = run_oracle(task, tmp_path, with_solution=False)

    assert not result.passed, (
        f"{task.id}: verify.sh should FAIL on untouched setup/ but exited 0\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
