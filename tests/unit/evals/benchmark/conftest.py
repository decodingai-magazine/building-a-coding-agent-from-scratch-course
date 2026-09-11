"""Grade an ARBITRARY answer through a real task's Verifier (ADR-0022 §3; task 158).

The oracle gate (``test_oracle_sanity.py``) only ever drives the two extremes — the gold
``solution/`` and an untouched seed. The edge-case suites need the middle: a workspace holding a
*plausible but wrong* answer (must earn ``0``) or an *alternative-correct* one (must earn ``1``).
:func:`grade_workspace` is that seam, and it reproduces grade time exactly as
:func:`~evals.harness.oracle_sanity.run_verifier` defines it — by CALLING the same
:func:`~evals.harness.verifier.grade_checkout` (overlay ``tests/`` LAST, empty ``$VERIFIER_DIR``,
``bash tests/test.sh`` under an allow-listed env, one float in ``reward.txt``) with the Oracle step
swapped for the caller's files. A hand copy of those steps would let the suites grade differently
from the real tasks.

Both fixtures return the same :class:`~evals.harness.oracle_sanity.VerifierResult` the gate asserts
on, so a surprise is debuggable from stdout/stderr and a ``reward`` of ``None`` (a verifier ERROR)
can never be mistaken for a correctly-refused answer.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from evals.harness.oracle_sanity import VerifierResult
from evals.harness.seed import seed_task_repo
from evals.harness.task_loader import BENCHMARK_TASKS_DIR, load_benchmark_task
from evals.harness.verifier import grade_checkout

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
    """Seed a task, put the submitted answer in the workspace, and grade it.

    An answer is ``files`` (path -> content) and/or ``post_setup``, a bash snippet run in the
    workspace — some tasks' deliverable is a git ACTION (018's revert), not a passive file, and a
    ``git revert`` cannot be expressed as a file to drop in.
    """

    def _grade(
        task_id: str, *, files: dict[str, str] | None = None, post_setup: str | None = None
    ) -> VerifierResult:
        task = load_benchmark_task(BENCHMARK_TASKS_DIR / task_id)
        workspace = seed_workspace(task_id)

        for name, content in (files or {}).items():
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        if post_setup is not None:
            acted = subprocess.run(
                ["bash", "-c", post_setup],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            assert acted.returncode == 0, f"post_setup failed: {acted.stdout}{acted.stderr}"

        # THE grade-time step both real graders drive — overlay, empty $VERIFIER_DIR, run, parse.
        # A timeout comes back as ``timed_out`` here too, instead of escaping the fixture.
        return grade_checkout(task, workspace)

    return _grade


@pytest.fixture
def assert_reward() -> AssertReward:
    """The verdict assertion both edge-case suites share (a fixture, so no cross-file import)."""

    def _assert_reward(result: VerifierResult, expected: float) -> None:
        assert result.reward == expected, (
            f"expected reward {expected}, got {result.reward} (timed_out={result.timed_out})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    return _assert_reward
