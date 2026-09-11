"""Grade an ARBITRARY answer through a real task's Verifier (ADR-0022 §3; task 158).

The oracle gate (``test_oracle_sanity.py``) only ever drives the two extremes — the gold
``solution/`` and an untouched seed. The edge-case suites need the middle: a workspace holding a
*plausible but wrong* answer (must earn ``0``) or an *alternative-correct* one (must earn ``1``).
:func:`grade_workspace` is that seam, and it reproduces grade time exactly as
:func:`~evals.harness.oracle_sanity.run_verifier` defines it — seed, drop the answer in, overlay
``tests/`` LAST, run ``bash tests/test.sh`` with ``VERIFIER_DIR`` set — with the Oracle step swapped
for the caller's files.

Both fixtures return the same :class:`~evals.harness.oracle_sanity.VerifierResult` the gate asserts
on, so a surprise is debuggable from stdout/stderr and a ``reward`` of ``None`` (a verifier ERROR)
can never be mistaken for a correctly-refused answer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from evals.harness.oracle_sanity import (
    REWARD_FILE_NAME,
    VERIFIER_DIR_NAME,
    VerifierResult,
)
from evals.harness.seed import seed_task_repo
from evals.harness.task_loader import (
    BENCHMARK_TASKS_DIR,
    TESTS_DIR_NAME,
    VERIFIER_SCRIPT_NAME,
    load_benchmark_task,
)

# ``seed_workspace(task_id)`` -> the freshly seeded Seed Repo directory.
SeedWorkspace = Callable[[str], Path]

# ``grade_workspace(task_id, files={...})`` -> the Verifier's verdict on that answer.
GradeWorkspace = Callable[..., VerifierResult]

# ``assert_reward(result, 1.0)`` — assert on the REWARD, never on the exit code: under v2 the exit
# code is informational and a ``None`` reward is a verifier ERROR, so a loose ``!= 1.0`` would let a
# crashed verifier pass as "correctly rejected the answer".
AssertReward = Callable[[VerifierResult, float], None]


@pytest.fixture
def seed_workspace(tmp_path: Path) -> SeedWorkspace:
    """Materialise one real task's Seed Repo under ``tmp_path`` — the agent's starting point."""

    def _seed(task_id: str) -> Path:
        task = load_benchmark_task(BENCHMARK_TASKS_DIR / task_id)
        workspace = tmp_path / "workspace"
        seed_task_repo(task, workspace)
        return workspace

    return _seed


@pytest.fixture
def grade_workspace(seed_workspace: SeedWorkspace) -> GradeWorkspace:
    """Seed a task, write ``files`` into the workspace as the submitted answer, and grade it."""

    def _grade(task_id: str, *, files: dict[str, str]) -> VerifierResult:
        task = load_benchmark_task(BENCHMARK_TASKS_DIR / task_id)
        workspace = seed_workspace(task_id)

        for name, content in files.items():
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        # LAST, exactly as ADR-0022 §3 grades: a test file the answer planted is overwritten here.
        shutil.copytree(task.tests_dir, workspace / TESTS_DIR_NAME, dirs_exist_ok=True)

        verifier_dir = workspace / VERIFIER_DIR_NAME
        shutil.rmtree(verifier_dir, ignore_errors=True)
        verifier_dir.mkdir(parents=True)
        completed = subprocess.run(
            ["bash", f"{TESTS_DIR_NAME}/{VERIFIER_SCRIPT_NAME}"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=task.verifier_timeout_sec,
            env={**os.environ, "VERIFIER_DIR": str(verifier_dir)},
            check=False,
        )
        return VerifierResult(
            reward=_read_reward(verifier_dir),
            stdout=completed.stdout,
            stderr=completed.stderr,
            timed_out=False,
        )

    return _grade


def _read_reward(verifier_dir: Path) -> float | None:
    """The single float in ``$VERIFIER_DIR/reward.txt``; ``None`` if missing, empty or non-numeric."""
    reward_path = verifier_dir / REWARD_FILE_NAME
    if not reward_path.is_file():
        return None
    try:
        return float(reward_path.read_text(encoding="utf-8", errors="replace").strip())
    except ValueError:
        return None


@pytest.fixture
def assert_reward() -> AssertReward:
    """The verdict assertion both edge-case suites share (a fixture, so no cross-file import)."""

    def _assert_reward(result: VerifierResult, expected: float) -> None:
        assert result.reward == expected, (
            f"expected reward {expected}, got {result.reward} (timed_out={result.timed_out})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    return _assert_reward
