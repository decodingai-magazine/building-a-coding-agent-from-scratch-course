"""The command-executor seam — the one real abstraction in the tool layer (ADR-0002 §7,10).

``bash`` asks a :class:`CommandExecutor` to run a command; :class:`LocalExecutor` is the host
implementation and the sandbox executors swap in behind the same ``run`` method. Keep this
module **infra-agnostic**: a command string, a working directory, a timeout, and an
:class:`ExecResult` — nothing about bash, the agent, truncation, or permissions.

**Timeout = no leaked processes.** The child runs in its **own process group**
(``start_new_session=True``); on timeout the *whole group* is signalled (``SIGTERM`` →
``SIGKILL`` after a short grace window) so spawned children die too, and the partial output
captured so far is returned with ``timed_out=True``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Grace between SIGTERM and SIGKILL for a timed-out group — deliberately short.
_KILL_GRACE_S = 2.0


@dataclass(frozen=True, slots=True)
class ExecResult:
    """The outcome of running one command (ADR-0002 §7).

    ``stdout`` / ``stderr`` are UTF-8-decoded (undecodable bytes replaced, never crash).
    ``exit_code`` mirrors :class:`asyncio.subprocess.Process` (``-N`` = killed by signal N).
    ``timed_out=True`` means the executor terminated the process at its deadline — the streams
    hold whatever partial output was captured before the kill. ``note`` is an optional
    out-of-band execution notice appended by ``bash._render`` when non-empty (ADR-0011 §2);
    :class:`LocalExecutor` never sets it, so its ``""`` default keeps ``none``-mode rendering
    **byte-identical** to before the field existed.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    note: str = ""


class CommandExecutor(Protocol):
    """The seam ``bash`` runs commands through (ADR-0002 §7): one ``run`` method, swappable implementation."""

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Run ``command`` in ``cwd``, terminating it after ``timeout_s`` seconds."""
        ...


class LocalExecutor:
    """Run a command as a local ``asyncio`` subprocess in its own process group (§7).

    Executed via the shell (pipes, redirects, ``&&`` work); ``start_new_session=True`` makes
    the child lead its own group so a timeout can kill its children too.
    """

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Run ``command`` in ``cwd``; on timeout kill its process group and return partial output.

        A timeout terminates the whole group (SIGTERM, then SIGKILL after :data:`_KILL_GRACE_S`),
        sets ``timed_out`` ``True``, and returns whatever output was captured before the kill.

        ``communicate()`` runs as a task that is **never cancelled**: cancelling it (e.g.
        ``asyncio.wait_for``) would discard the buffered partial output, so we
        :func:`asyncio.wait` with a timeout instead and, after killing the group, ``await`` the
        *same* task to drain what the child already wrote.
        """
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,  # own process group: kill it as a unit, children included
        )
        comm = asyncio.ensure_future(process.communicate())
        done, _ = await asyncio.wait({comm}, timeout=timeout_s)
        if not done:
            logger.debug("command timed out after %.3fs, killing process group", timeout_s)
            stdout, stderr = await self._terminate(process, comm)
            return ExecResult(
                stdout=_decode(stdout),
                stderr=_decode(stderr),
                exit_code=process.returncode if process.returncode is not None else -signal.SIGKILL,
                timed_out=True,
            )
        stdout, stderr = await comm
        return ExecResult(
            stdout=_decode(stdout),
            stderr=_decode(stderr),
            exit_code=process.returncode if process.returncode is not None else 0,
            timed_out=False,
        )

    @staticmethod
    async def _terminate(
        process: asyncio.subprocess.Process,
        comm: asyncio.Future[tuple[bytes, bytes]],
    ) -> tuple[bytes, bytes]:
        """Kill the timed-out child's whole process group and drain its partial output.

        SIGTERM the group first; SIGKILL after :data:`_KILL_GRACE_S`. ``comm`` is the
        **already-running** ``communicate()`` task — awaiting it (not re-invoking
        ``communicate()``, which would return empty bytes) yields the buffered partial output;
        signalling the *group* is what guarantees spawned children die with the command.
        """
        _signal_group(process, signal.SIGTERM)
        done, _ = await asyncio.wait({comm}, timeout=_KILL_GRACE_S)
        if not done:
            _signal_group(process, signal.SIGKILL)
        return await comm


def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    """Send ``sig`` to the child's entire process group; tolerate an already-dead process.

    With ``start_new_session=True`` the child's PID is its group id, so ``os.killpg`` reaches
    every descendant in one call; :class:`ProcessLookupError` / :class:`PermissionError` are
    swallowed — nothing left to kill.
    """
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        logger.debug("process group %d already gone when sending %s", process.pid, sig.name)


def _decode(raw: bytes) -> str:
    """Decode captured subprocess bytes as UTF-8, replacing undecodable bytes (never crash)."""
    return raw.decode("utf-8", errors="replace")
