"""Per-task-run sandbox lifecycle, reusing decode's OWN executor seam (ADR-0017 §3,5; task 106).

A benchmark run needs a fresh, isolated Workspace per task and a hidden oracle that the agent can
never see during its run. Both are exactly what decode's sandbox seam already solves, so this module
adds no runner infra — it drives the existing ``SandboxExecutor`` through the ``decode.tools.bash``
module seam, the same ``runtime/headless.py::_prepare_headless_tool_scope`` pattern the headless
runtime uses.

:func:`benchmark_sandbox` is a sync context manager (Opik task fns are sync) that, for one task run:

1. seeds ``environment/`` host-side into a fresh temp Workspace (the modal backend uploads that tree at
   create, docker bind-mounts it — one seed mechanism for both backends);
2. selects the backend by ``--sandbox`` (``docker`` default, ``modal`` the rung) and warms it against
   the Workspace, wiring it into the ``bash`` seam so the agent's ``bash`` + file tools ride it and
   pointing the run at the Workspace;
3. runs ``environment/setup.sh`` inside the sandbox after create;
4. yields a :class:`SandboxRun` the caller drives the agent against and then GRADES — verify assets
   are injected through the seam only at :meth:`SandboxRun.grade` time, so ``tests/test.sh`` never
   exists in the Workspace while the agent runs (ADR-0017 §5);
5. tears the executor down and removes the temp Workspace in a ``finally`` — on success AND on any
   agent / verify failure.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decode.config.settings import settings
from decode.tools.bash import (
    active_executor,
    close_executor,
    reset_executor,
    warm_executor,
)
from decode.tools.exec import ExecResult
from evals.harness.task_loader import (
    SETUP_SCRIPT_NAME,
    TESTS_DIR_NAME,
    VERIFIER_SCRIPT_NAME,
    BenchmarkTask,
)

logger = logging.getLogger(__name__)

# Wall-clock cap for a harness-run helper command (setup.sh / tests/test.sh) inside the sandbox.
HELPER_TIMEOUT_S = 300.0


@dataclass(frozen=True, slots=True)
class SandboxRun:
    """A live per-task Workspace behind the executor seam (ADR-0017 §3,5).

    ``workspace`` is the fresh temp Workspace root the agent's tools are pointed at; ``executor`` is
    the warmed :class:`~decode.sandbox.executor.SandboxExecutor` every command rides. Callers run the
    agent, then call :meth:`grade` — nothing injects ``tests/`` before that, so the Verifier is hidden
    for the whole run.
    """

    workspace: Path
    executor: Any

    def run(self, command: str, *, timeout_s: float = HELPER_TIMEOUT_S) -> ExecResult:
        """Run one command inside the sandbox Workspace through the seam (sync; wraps the async exec)."""
        return _run_async(self.executor.run(command, cwd=self.workspace, timeout_s=timeout_s))

    def grade(self, task: BenchmarkTask) -> ExecResult:
        """Inject the hidden ``tests/`` assets THROUGH the seam, then run the Verifier (ADR-0022 §2).

        Injection uses the backend's own file ops (not a host copy) so it works on docker AND modal —
        a modal Workspace's live filesystem is remote, not the host temp dir. Called only after the
        agent finishes, so the Verifier never existed in the Workspace during the run. The reward
        lands in ``.verifier/reward.txt``; this in-sandbox path is superseded by the host-side
        Verifier of ADR-0022 §3 when task 160 lands the trial runner.
        """
        self._inject(task.tests_dir, prefix=TESTS_DIR_NAME)
        return self.run(
            f"mkdir -p .verifier && VERIFIER_DIR=$PWD/.verifier "
            f"bash {TESTS_DIR_NAME}/{VERIFIER_SCRIPT_NAME}"
        )

    def _inject(self, source_dir: Path, *, prefix: str) -> None:
        """Write every file under ``source_dir`` into ``<workspace>/<prefix>/`` through the seam."""
        _run_async(self._inject_async(source_dir, prefix))

    async def _inject_async(self, source_dir: Path, prefix: str) -> None:
        backend = await self.executor.file_backend(self.workspace)
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(source_dir).as_posix()
                await backend.write_bytes(f"{prefix}/{rel}", path.read_bytes())


@contextmanager
def benchmark_sandbox(task: BenchmarkTask, *, sandbox: str = "docker") -> Iterator[SandboxRun]:
    """Bring up an isolated per-run Workspace for ``task`` and tear it down afterwards (ADR-0017 §3,5).

    ``sandbox`` selects the backend rung — ``docker`` (default) or ``modal`` — by driving the same
    ``decode.tools.bash`` seam the headless runtime warms. The Workspace is a fresh temp dir seeded
    with ``environment/`` before create; ``environment/setup.sh`` runs after create. The ``finally`` reaps the
    executor (``close_executor``), restores ``SANDBOX_MODE`` and deletes the temp Workspace whether
    the ``with`` body succeeds or raises.
    """
    workspace = Path(tempfile.mkdtemp(prefix="decode-eval-")).resolve()
    _seed_setup(task, workspace)
    previous_mode = settings.sandbox_mode
    settings.sandbox_mode = sandbox
    reset_executor()
    try:
        _run_async(warm_executor(workspace))
        # ``active_executor`` is the seam's public read accessor — it returns the SAME executor
        # ``warm_executor`` just started (shared memo), no private handle needed.
        executor = active_executor()
        _run_setup_script(executor, workspace, task)
        yield SandboxRun(workspace=workspace, executor=executor)
    finally:
        _run_async(close_executor())
        settings.sandbox_mode = previous_mode
        shutil.rmtree(workspace, ignore_errors=True)


def _seed_setup(task: BenchmarkTask, workspace: Path) -> None:
    """Copy ``environment/`` verbatim into the fresh Workspace before create (both backends)."""
    if task.environment_dir.is_dir():
        shutil.copytree(task.environment_dir, workspace, dirs_exist_ok=True)


def _run_setup_script(executor: Any, workspace: Path, task: BenchmarkTask) -> None:
    """Run ``setup/setup.sh`` inside the sandbox after create, if the task ships one.

    Runs through the seam (``executor.run``) so it executes identically on docker and modal. A
    non-zero exit is logged, not raised: the benchmark still grades the resulting Workspace (a broken
    setup grades as a task failure), and the harness never crashes on one bad task.
    """
    if not task.setup_script.is_file():
        return
    result = _run_async(
        executor.run(f"bash {SETUP_SCRIPT_NAME}", cwd=workspace, timeout_s=HELPER_TIMEOUT_S)
    )
    if result.exit_code != 0:
        logger.warning(
            "[eval] setup.sh failed for %s (exit %d): %s",
            task.id,
            result.exit_code,
            result.stderr.strip(),
        )


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run one coroutine to completion on a fresh loop — safe here because the seam is fresh-exec.

    The benchmark task fn is sync (Opik requires it) and holds no loop-bound handle across calls (each
    sandbox command spawns a fresh subprocess), so a per-call :func:`asyncio.run` is correct — the
    same one-``asyncio.run``-per-run shape ``runtime/headless.py::run_headless_task`` has.
    """
    return asyncio.run(coro)
