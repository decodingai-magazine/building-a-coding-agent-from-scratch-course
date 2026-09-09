"""Per-task-run sandbox lifecycle, reusing decode's OWN executor seam (ADR-0017 §3,5; task 106).

A benchmark run needs a fresh, isolated Workspace per task and a hidden oracle that the agent can
never see during its run. Both are exactly what decode's sandbox seam already solves, so this module
adds no runner infra — it drives the existing ``SandboxExecutor`` through the ``decode.tools.bash``
module seam, the same ``runtime/headless.py::_prepare_headless_tool_scope`` pattern the headless
runtime uses.

:func:`benchmark_sandbox` is a sync context manager (Opik task fns are sync) that, for one task run:

1. seeds ``setup/`` host-side into a fresh temp Workspace (the modal backend uploads that tree at
   create, docker bind-mounts it — one seed mechanism for both backends);
2. selects the backend by ``--sandbox`` (``docker`` default, ``modal`` the rung) and warms it against
   the Workspace, wiring it into the ``bash`` seam so the agent's ``bash`` + file tools ride it and
   pointing the run at the Workspace;
3. runs ``setup/setup.sh`` inside the sandbox after create;
4. yields a :class:`SandboxRun` the caller drives the agent against and then GRADES — verify assets
   are injected through the seam only at :meth:`SandboxRun.grade` time, so ``verify.sh`` never exists
   in the Workspace while the agent runs (ADR-0017 §5);
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
from evals.harness.task_loader import VERIFY_SCRIPT_NAME, BenchmarkTask

logger = logging.getLogger(__name__)

# The optional post-seed setup entrypoint a task may ship (git history, a sqlite DB, mixed encodings).
SETUP_SCRIPT_NAME = "setup.sh"

# Wall-clock cap for a harness-run helper command (setup.sh / verify.sh) inside the sandbox.
HELPER_TIMEOUT_S = 300.0


@dataclass(frozen=True, slots=True)
class SandboxRun:
    """A live per-task Workspace behind the executor seam (ADR-0017 §3,5).

    ``workspace`` is the fresh temp Workspace root the agent's tools are pointed at; ``executor`` is
    the warmed :class:`~decode.sandbox.executor.SandboxExecutor` every command rides. Callers run the
    agent, then call :meth:`grade` — nothing injects ``verify/`` before that, so the oracle is hidden
    for the whole run.
    """

    workspace: Path
    executor: Any

    def run(self, command: str, *, timeout_s: float = HELPER_TIMEOUT_S) -> ExecResult:
        """Run one command inside the sandbox Workspace through the seam (sync; wraps the async exec)."""
        return _run_async(self.executor.run(command, cwd=self.workspace, timeout_s=timeout_s))

    def grade(self, task: BenchmarkTask) -> ExecResult:
        """Inject the hidden ``verify/`` assets THROUGH the seam, then run ``bash verify.sh`` (ADR-0017 §5).

        Injection uses the backend's own file ops (not a host copy) so it works on docker AND modal —
        a modal Workspace's live filesystem is remote, not the host temp dir. Called only after the
        agent finishes, so the oracle never existed in the Workspace during the run.
        """
        self._inject(task.verify_script.parent)
        return self.run(f"bash {VERIFY_SCRIPT_NAME}")

    def _inject(self, source_dir: Path) -> None:
        """Write every file under ``source_dir`` into the Workspace root through the file-op seam."""
        _run_async(self._inject_async(source_dir))

    async def _inject_async(self, source_dir: Path) -> None:
        backend = await self.executor.file_backend(self.workspace)
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                await backend.write_bytes(
                    path.relative_to(source_dir).as_posix(), path.read_bytes()
                )


@contextmanager
def benchmark_sandbox(task: BenchmarkTask, *, sandbox: str = "docker") -> Iterator[SandboxRun]:
    """Bring up an isolated per-run Workspace for ``task`` and tear it down afterwards (ADR-0017 §3,5).

    ``sandbox`` selects the backend rung — ``docker`` (default) or ``modal`` — by driving the same
    ``decode.tools.bash`` seam the headless runtime warms. The Workspace is a fresh temp dir seeded
    with ``setup/`` before create; ``setup/setup.sh`` runs after create. The ``finally`` reaps the
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
    """Copy ``setup/`` verbatim into the fresh Workspace before create (host-side, both backends)."""
    if task.setup_dir.is_dir():
        shutil.copytree(task.setup_dir, workspace, dirs_exist_ok=True)


def _run_setup_script(executor: Any, workspace: Path, task: BenchmarkTask) -> None:
    """Run ``setup/setup.sh`` inside the sandbox after create, if the task ships one.

    Runs through the seam (``executor.run``) so it executes identically on docker and modal. A
    non-zero exit is logged, not raised: the benchmark still grades the resulting Workspace (a broken
    setup grades as a task failure), and the harness never crashes on one bad task.
    """
    if not (task.setup_dir / SETUP_SCRIPT_NAME).is_file():
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
