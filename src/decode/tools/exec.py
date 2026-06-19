"""The command-executor seam — the one real abstraction in the tool layer (ADR-0002 §7,10).

``bash`` (task 008) does not spawn subprocesses itself; it asks a :class:`CommandExecutor` to
run a command and hands back the result. M1 ships exactly one implementation,
:class:`LocalExecutor` (a local ``asyncio`` subprocess); **M8 swaps in a Docker / Modal
sandbox behind this same ``run`` method** without touching ``bash`` (ADR-0002 §7, AGENTS.md:
"Sandbox is the one real abstraction"). Keep this module **infra-agnostic** — it knows about a
command string, a working directory, a timeout, and the four fields of an :class:`ExecResult`.
It knows nothing about bash, the agent, truncation, or permissions.

**Timeout = no leaked processes.** :class:`LocalExecutor` launches the child in its **own
process group** (``start_new_session=True``) and, on timeout, signals the *whole group* (not
just the immediate child) so a command that spawned children — ``sh -c 'sleep 100 & ...'`` —
does not leave orphans behind. It escalates ``SIGTERM`` → ``SIGKILL`` after a short grace
window, then returns the partial output captured so far with ``timed_out=True``.
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

# Grace period between SIGTERM and the SIGKILL escalation when a timed-out group ignores the
# polite signal. Deliberately short — a runaway command is already over its deadline.
_KILL_GRACE_S = 2.0


@dataclass(frozen=True, slots=True)
class ExecResult:
    """The outcome of running one command (ADR-0002 §7).

    ``stdout`` / ``stderr`` are the captured streams decoded as UTF-8 (undecodable bytes are
    replaced, never crash). ``exit_code`` is the process exit status (``-N`` for "killed by
    signal N", mirroring :class:`asyncio.subprocess.Process`). ``timed_out`` is ``True`` when
    the executor had to terminate the process for exceeding its deadline — in which case
    ``stdout`` / ``stderr`` hold whatever partial output was captured before the kill.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


class CommandExecutor(Protocol):
    """The seam ``bash`` runs commands through (ADR-0002 §7; M8 swaps the implementation).

    One method: :meth:`run` a command string in ``cwd`` with a wall-clock ``timeout_s`` and
    return an :class:`ExecResult`. M1 has :class:`LocalExecutor`; M8 adds a sandboxed executor
    (Docker / Modal) behind this identical signature so ``bash`` is unaffected.
    """

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Run ``command`` in ``cwd``, terminating it after ``timeout_s`` seconds."""
        ...


class LocalExecutor:
    """Run a command as a local ``asyncio`` subprocess in its own process group (§7).

    The command is executed via the shell (``asyncio.create_subprocess_shell``) so the model
    can use pipes, redirects, and ``&&`` like a real terminal. The child starts a new session
    (``start_new_session=True``) so it leads its own process group; on timeout the executor
    signals the whole group, which is what stops a command's *children* from outliving it.
    """

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Run ``command`` in ``cwd``; on timeout kill its process group and return partial output.

        Returns an :class:`ExecResult`. A normal exit fills ``exit_code`` and leaves
        ``timed_out`` ``False``; a timeout terminates the whole process group (SIGTERM, then
        SIGKILL after :data:`_KILL_GRACE_S`), sets ``timed_out`` ``True``, and returns whatever
        output was captured before the kill.

        **Why a non-cancelled ``communicate()``.** ``communicate()`` reads the child's pipes
        into in-memory buffers; *cancelling* it (e.g. ``asyncio.wait_for``) discards those
        buffers, so output the child flushed *before* the deadline would be lost. We instead run
        ``communicate()`` as a task and :func:`asyncio.wait` on it with a timeout (which does not
        cancel on expiry); on timeout we kill the group and ``await`` the *same* task so it
        drains the partial output the child already wrote.
        """
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,  # own process group: kill it as a unit, children included
        )
        # Run communicate() as a task we never cancel — cancelling would throw away the partial
        # output buffered before a timeout. asyncio.wait() lets the timeout lapse without killing
        # the read, so the same task keeps draining once we signal the process group.
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

        SIGTERM the group first (polite stop); if it has not exited within
        :data:`_KILL_GRACE_S`, SIGKILL the group (children included) so nothing is orphaned.
        ``comm`` is the **already-running** ``communicate()`` task from :meth:`run` — we
        ``await`` it (rather than re-invoking ``communicate()``, which would return empty bytes)
        so the partial ``(stdout, stderr)`` the child buffered before the kill is returned.
        Signalling the *group* (negative pid) — not just the immediate child — is what guarantees
        a command's spawned children die with it.
        """
        _signal_group(process, signal.SIGTERM)
        done, _ = await asyncio.wait({comm}, timeout=_KILL_GRACE_S)
        if not done:
            _signal_group(process, signal.SIGKILL)
        return await comm


def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    """Send ``sig`` to the child's entire process group; tolerate an already-dead process.

    With ``start_new_session=True`` the child's PID is its process-group id, so
    ``os.killpg(pid, sig)`` reaches the child and every descendant in one call. A
    :class:`ProcessLookupError` (the group already exited) or :class:`PermissionError` is
    swallowed — there is nothing left to kill.
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
