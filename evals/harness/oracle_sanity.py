"""Run a task's Verifier host-side — the oracle gate's engine (ADR-0022 §3,§5; task 157).

A Verifier that always wrote reward 1 would grade every agent up; one that always wrote 0 would grade
every agent down. The keyless ``make ci`` gate (``tests/unit/evals/benchmark/test_oracle_sanity.py``)
runs each task BOTH directions through :func:`run_verifier` and asserts the Oracle earns ``1.0`` and
silence earns ``0.0``.

The run reproduces grade time exactly as ADR-0022 §3 defines it, host-side, with no sandbox seam:
seed the repo, (optionally) apply ``solution/solve.sh``, overlay ``tests/`` LAST, then
``bash tests/test.sh`` with ``VERIFIER_DIR`` pointing at an existing empty dir, and read the single
float from ``$VERIFIER_DIR/reward.txt``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from evals.harness.seed import seed_task_repo
from evals.harness.task_loader import (
    TESTS_DIR_NAME,
    VERIFIER_SCRIPT_NAME,
    BenchmarkTask,
)

# Wall clock for ``solution/solve.sh`` (the Verifier's own cap is the task's ``verifier.timeout_sec``).
ORACLE_TIMEOUT_S = 300.0

# The reward file the Verifier writes inside ``$VERIFIER_DIR``, and the dir's name in the checkout.
REWARD_FILE_NAME = "reward.txt"
VERIFIER_DIR_NAME = ".verifier"


@dataclass(frozen=True, slots=True)
class VerifierResult:
    """One host-side Verifier run: the parsed reward plus everything needed to debug a surprise.

    ``reward`` is ``None`` when ``reward.txt`` is missing, empty or non-numeric — a verifier ERROR,
    never a zero (ADR-0022 §3,§4).
    """

    reward: float | None
    stdout: str
    stderr: str
    timed_out: bool


class OracleError(Exception):
    """A task's ``solution/solve.sh`` failed to run — the gold answer itself is broken."""


def run_verifier(task: BenchmarkTask, workspace: Path, *, with_solution: bool) -> VerifierResult:
    """Seed ``workspace``, optionally apply the Oracle, then grade it host-side (ADR-0022 §3).

    Four steps, in this order — the order IS the isolation:

    1. :func:`~evals.harness.seed.seed_task_repo` builds the Seed Repo at ``workspace`` (the same
       seeding path a real trial uses);
    2. with ``with_solution``, ``bash <task>/solution/solve.sh`` runs with ``workspace`` as cwd, so
       the Oracle can ``cp "$(dirname "$0")/<file>" .`` from its own directory;
    3. ``tests/`` is overlaid onto ``<workspace>/tests/`` LAST, so a copy the agent (or the seed)
       planted there is overwritten and can never grade itself;
    4. ``bash tests/test.sh`` runs with ``VERIFIER_DIR`` = a fresh, empty ``<workspace>/.verifier``
       and a ``verifier.timeout_sec`` cap; the reward is the single float in ``reward.txt``.

    The exit code is informational (ADR-0022 §3) — only ``reward.txt`` grades. ``reward is None``
    means the Verifier errored, which callers must not read as a zero.
    """
    seed_task_repo(task, workspace)
    if with_solution:
        _run_oracle(task, workspace)
    _overlay_tests(task, workspace)

    verifier_dir = workspace / VERIFIER_DIR_NAME
    # EMPTY, not just present: a stale reward.txt from an earlier run would be read as this run's
    # verdict, turning a Verifier that wrote nothing (an error) into a silent pass.
    shutil.rmtree(verifier_dir, ignore_errors=True)
    verifier_dir.mkdir(parents=True)
    try:
        completed = subprocess.run(
            ["bash", f"{TESTS_DIR_NAME}/{VERIFIER_SCRIPT_NAME}"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=task.verifier_timeout_sec,
            env={**os.environ, "VERIFIER_DIR": str(verifier_dir)},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return VerifierResult(
            reward=None,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
            timed_out=True,
        )
    return VerifierResult(
        reward=_read_reward(verifier_dir),
        stdout=completed.stdout,
        stderr=completed.stderr,
        timed_out=False,
    )


def _run_oracle(task: BenchmarkTask, workspace: Path) -> None:
    """Apply the gold answer to a fresh seed; a broken Oracle is loud, never a silent reward 0."""
    try:
        result = subprocess.run(
            ["bash", str(task.oracle_script)],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=ORACLE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OracleError(
            f"{task.id}: solution/solve.sh timed out after {ORACLE_TIMEOUT_S:.0f}s"
        ) from exc
    if result.returncode != 0:
        raise OracleError(
            f"{task.id}: solution/solve.sh failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )


def _overlay_tests(task: BenchmarkTask, workspace: Path) -> None:
    """Copy the hidden ``tests/`` assets onto ``<workspace>/tests/`` — last, so they win."""
    shutil.copytree(task.tests_dir, workspace / TESTS_DIR_NAME, dirs_exist_ok=True)


def _read_reward(verifier_dir: Path) -> float | None:
    """The single float in ``$VERIFIER_DIR/reward.txt``; ``None`` if missing, empty or non-numeric."""
    reward_path = verifier_dir / REWARD_FILE_NAME
    if not reward_path.is_file():
        return None
    raw = reward_path.read_text(encoding="utf-8", errors="replace").strip()
    try:
        return float(raw)
    except ValueError:
        return None


def _decode(captured: str | bytes | None) -> str:
    """Normalise what a timed-out subprocess captured (bytes on the timeout path) to text."""
    if captured is None:
        return ""
    if isinstance(captured, bytes):
        return captured.decode("utf-8", errors="replace")
    return captured
