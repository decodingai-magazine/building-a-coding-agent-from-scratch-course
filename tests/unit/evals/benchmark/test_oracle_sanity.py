"""The oracle gate: every Verifier graded honestly, both directions, keyless (ADR-0022 §5; task 157).

A grader that cannot tell the Oracle from silence cannot grade an agent. So for every task, in
ordinary ``make ci``: ``solution/solve.sh`` applied to a fresh Seed Repo must earn reward ``1.0``,
and an untouched seed must earn ``0.0`` — never ``None``, which is a verifier ERROR (a missing,
empty or non-numeric ``reward.txt``), not a zero.

Both directions run through :func:`~evals.harness.oracle_sanity.run_verifier`, which seeds with the
same :func:`~evals.harness.seed.seed_task_repo` the trial runner uses — so CI proves the seeding path
too. Parametrized over the fixture task plus every real task the loader discovers; the real set is
empty until tasks 158/159 convert it, and the fixture guarantees the gate itself always runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.oracle_sanity import VerifierResult, run_verifier
from evals.harness.task_loader import (
    BENCHMARK_TASKS_DIR,
    BenchmarkTask,
    load_benchmark_tasks,
)

# ``tests/unit/evals/benchmark/`` -> ``tests/unit/evals/`` -> the committed fixture tasks tree.
_FIXTURE_TASKS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tasks"

_SANITY_TASKS = load_benchmark_tasks(_FIXTURE_TASKS_DIR) + load_benchmark_tasks(BENCHMARK_TASKS_DIR)


def _report(task: BenchmarkTask, result: VerifierResult, expected: float) -> str:
    return (
        f"{task.id}: expected reward {expected}, got {result.reward} "
        f"(timed_out={result.timed_out})\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.parametrize("task", _SANITY_TASKS, ids=lambda task: task.id)
def test_the_oracle_earns_reward_one(task: BenchmarkTask, tmp_path: Path) -> None:
    result = run_verifier(task, tmp_path / "workspace", with_solution=True)

    assert result.reward == 1.0, _report(task, result, 1.0)


@pytest.mark.parametrize("task", _SANITY_TASKS, ids=lambda task: task.id)
def test_an_untouched_seed_earns_reward_zero(task: BenchmarkTask, tmp_path: Path) -> None:
    result = run_verifier(task, tmp_path / "workspace", with_solution=False)

    assert result.reward == 0.0, _report(task, result, 0.0)
