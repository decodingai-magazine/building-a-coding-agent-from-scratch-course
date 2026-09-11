"""Run a task's Verifier host-side — the oracle gate's engine (ADR-0022 §3,§5; task 157).

A Verifier that always wrote reward 1 would grade every agent up; one that always wrote 0 would grade
every agent down. The keyless ``make ci`` gate (``tests/unit/evals/benchmark/test_oracle_sanity.py``)
runs each task BOTH directions through :func:`run_verifier` and asserts the Oracle earns ``1.0`` and
silence earns ``0.0``.

The run reproduces grade time exactly as ADR-0022 §3 defines it, host-side, with no sandbox seam:
seed the repo, (optionally) apply ``solution/solve.sh``, then hand the tree to
:func:`~evals.harness.verifier.grade_checkout` — the SAME grade-time step a real trial's pristine
clone goes through (overlay ``tests/`` LAST, empty ``$VERIFIER_DIR``, one float in ``reward.txt``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from evals.harness.seed import seed_task_repo
from evals.harness.task_loader import BenchmarkTask
from evals.harness.verifier import (
    REWARD_FILE_NAME,
    VERIFIER_DIR_NAME,
    VerifierResult,
    grade_checkout,
    host_script_env,
)

# Wall clock for ``solution/solve.sh`` (the Verifier's own cap is the task's ``verifier.timeout_sec``).
ORACLE_TIMEOUT_S = 300.0

# Re-exported so the gate's callers keep one import site for the grade-time vocabulary.
__all__ = [
    "ORACLE_TIMEOUT_S",
    "REWARD_FILE_NAME",
    "VERIFIER_DIR_NAME",
    "OracleError",
    "VerifierResult",
    "run_verifier",
]


class OracleError(Exception):
    """A task's ``solution/solve.sh`` failed to run — the gold answer itself is broken."""


def run_verifier(task: BenchmarkTask, workspace: Path, *, with_solution: bool) -> VerifierResult:
    """Seed ``workspace``, optionally apply the Oracle, then grade it host-side (ADR-0022 §3).

    Four steps, in this order — the order IS the isolation:

    1. :func:`~evals.harness.seed.seed_task_repo` builds the Seed Repo at ``workspace`` (the same
       seeding path a real trial uses);
    2. with ``with_solution``, ``bash <task>/solution/solve.sh`` runs with ``workspace`` as cwd, so
       the Oracle can ``cp "$(dirname "$0")/<file>" .`` from its own directory;
    3. :func:`~evals.harness.verifier.grade_checkout` overlays ``tests/`` onto
       ``<workspace>/tests/`` LAST, so a copy the agent (or the seed) planted there is overwritten
       and can never grade itself;
    4. …and runs ``bash tests/test.sh`` with ``VERIFIER_DIR`` = a fresh, empty
       ``<workspace>/.verifier`` under the ``verifier.timeout_sec`` cap; the reward is the single
       float in ``reward.txt``.

    The exit code is informational (ADR-0022 §3) — only ``reward.txt`` grades. ``reward is None``
    means the Verifier errored, which callers must not read as a zero.
    """
    seed_task_repo(task, workspace)
    if with_solution:
        _run_oracle(task, workspace)
    return grade_checkout(task, workspace)


def _run_oracle(task: BenchmarkTask, workspace: Path) -> None:
    """Apply the gold answer to a fresh seed; a broken Oracle is loud, never a silent reward 0.

    Under the Verifier's own allow-listed env (:func:`~evals.harness.verifier.host_script_env`): the
    gate claims the Oracle earns 1 from a bare shell, so it must not read one operator's ``.env``.
    """
    try:
        result = subprocess.run(
            ["bash", str(task.oracle_script)],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=ORACLE_TIMEOUT_S,
            env=host_script_env(),
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
