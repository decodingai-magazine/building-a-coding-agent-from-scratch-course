"""Grade one checkout host-side — the ONE grade-time step both graders drive (ADR-0022 §3; task 160).

Two callers need the identical last mile of ADR-0022 §3: the keyless oracle gate
(:mod:`evals.harness.oracle_sanity`, which grades a freshly seeded workspace with and without the
Oracle) and the trial runner (:mod:`evals.harness.trial`, which grades a pristine clone of the
agent's Session Branch). The step is the same for both, so it lives here once:

1. overlay the task's hidden ``tests/`` onto ``<checkout>/tests/`` **LAST**, so a copy the agent (or
   the seed) planted there is overwritten and can never grade itself;
2. give the Verifier a fresh, EMPTY ``<checkout>/.verifier`` as ``$VERIFIER_DIR``;
3. run ``bash tests/test.sh`` there under the task's ``verifier.timeout_sec``;
4. read the single float from ``reward.txt``.

No sandbox seam, no docker, no network: a reward is reproducible from a Trial Dir with a bare
``bash`` + ``python3``. The exit code is informational — only ``reward.txt`` grades — and a missing,
empty or non-numeric reward is ``None``, a verifier ERROR that callers must never read as a zero.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from evals.harness.task_loader import (
    TESTS_DIR_NAME,
    VERIFIER_SCRIPT_NAME,
    BenchmarkTask,
)

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


def grade_checkout(task: BenchmarkTask, checkout: Path) -> VerifierResult:
    """Overlay the hidden tests onto ``checkout`` and run the Verifier there (ADR-0022 §3).

    ``checkout`` is whatever tree is being graded — a seeded workspace for the oracle gate, a
    pristine clone of the Session Branch for a trial. Everything the Verifier needs is created here
    (the ``tests/`` overlay, the empty ``$VERIFIER_DIR``), so a caller only has to produce the tree.
    """
    _overlay_tests(task, checkout)

    verifier_dir = checkout / VERIFIER_DIR_NAME
    # EMPTY, not just present: a stale reward.txt from an earlier run would be read as this run's
    # verdict, turning a Verifier that wrote nothing (an error) into a silent pass.
    shutil.rmtree(verifier_dir, ignore_errors=True)
    verifier_dir.mkdir(parents=True)
    try:
        completed = subprocess.run(
            ["bash", f"{TESTS_DIR_NAME}/{VERIFIER_SCRIPT_NAME}"],
            cwd=checkout,
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
        reward=read_reward(verifier_dir),
        stdout=completed.stdout,
        stderr=completed.stderr,
        timed_out=False,
    )


def _overlay_tests(task: BenchmarkTask, checkout: Path) -> None:
    """Copy the hidden ``tests/`` assets onto ``<checkout>/tests/`` — last, so they win."""
    shutil.copytree(task.tests_dir, checkout / TESTS_DIR_NAME, dirs_exist_ok=True)


def read_reward(verifier_dir: Path) -> float | None:
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
