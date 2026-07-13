"""Prove every benchmark oracle honest, both directions (ADR-0017 §5; task 105).

A hidden ``verify.sh`` that always exits 0 would grade every agent up; one that always fails would
grade every agent down. The only guard is to run each oracle against known inputs and assert it
answers correctly BOTH ways:

* over the gold ``solution/`` overlay it MUST pass (exit 0),
* over the untouched ``setup/`` seed it MUST fail (exit non-zero).

:func:`run_oracle` reproduces the grade-time Workspace host-side: it seeds ``setup/`` into a temp
dir, optionally runs ``setup/setup.sh``, optionally overlays ``solution/``, injects ``verify/``, and
runs ``bash verify.sh`` from the Workspace root. verify.sh may only use bash + python + git +
sqlite3 (task 105), so this host-side run matches the sandbox image. The oracle-sanity pytest
harness (``tests/unit/evals/benchmark/test_oracle_sanity.py``) drives both directions per task; this
module holds the reusable seeding logic so it stays honest as tasks 108-110 land.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from evals.harness.task_loader import VERIFY_SCRIPT_NAME, BenchmarkTask

# The setup entrypoint executed after seeding, if a task ships one.
SETUP_SCRIPT_NAME = "setup.sh"


@dataclass(frozen=True, slots=True)
class OracleResult:
    """The outcome of running one task's ``verify.sh`` over a prepared Workspace.

    ``passed`` is ``exit_code == 0`` — the oracle's PASS/FAIL verdict; ``stdout`` / ``stderr`` are
    captured so a surprising verdict is debuggable.
    """

    exit_code: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def run_oracle(task: BenchmarkTask, workspace: Path, *, with_solution: bool) -> OracleResult:
    """Prepare a Workspace and run ``task``'s hidden oracle over it host-side (ADR-0017 §5).

    Seeds ``setup/`` into ``workspace``, runs ``setup/setup.sh`` if present, overlays ``solution/``
    when ``with_solution`` is set, injects ``verify/``, then runs ``bash verify.sh`` from the
    Workspace root. ``workspace`` must be an existing (typically temporary) directory the caller
    owns. Returns the oracle's :class:`OracleResult`; raises nothing for a non-zero verify (that IS
    a valid FAIL verdict), only for a broken setup/overlay.
    """
    _copy_tree(task.setup_dir, workspace)
    _run_setup_script(workspace)
    if with_solution:
        _copy_tree(task.solution_dir, workspace)
    _inject_verify(task, workspace)
    return _run_verify(workspace)


def _copy_tree(source: Path, dest: Path) -> None:
    """Overlay ``source``'s contents onto ``dest`` (files clobber, dirs merge). No-op if absent."""
    if not source.is_dir():
        return
    shutil.copytree(source, dest, dirs_exist_ok=True)


def _run_setup_script(workspace: Path) -> None:
    """Run ``setup.sh`` from the Workspace root if the seed shipped one; raise on failure."""
    setup_script = workspace / SETUP_SCRIPT_NAME
    if not setup_script.is_file():
        return
    result = subprocess.run(
        ["bash", SETUP_SCRIPT_NAME],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"setup.sh failed in {workspace} (exit {result.returncode}): {result.stderr.strip()}"
        )


def _inject_verify(task: BenchmarkTask, workspace: Path) -> None:
    """Copy the hidden ``verify/`` assets into the Workspace root at grade time (ADR-0017 §5)."""
    _copy_tree(task.verify_script.parent, workspace)


def _run_verify(workspace: Path) -> OracleResult:
    """Run ``bash verify.sh`` from the Workspace root and capture its verdict."""
    result = subprocess.run(
        ["bash", VERIFY_SCRIPT_NAME],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    return OracleResult(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
