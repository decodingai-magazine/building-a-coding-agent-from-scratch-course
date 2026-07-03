"""The Modal sandbox executor — one session-persistent remote sandbox, empty scratch (ADR-0011 §3).

:class:`ModalExecutor` is a :class:`~decode.tools.exec.CommandExecutor` (the ``tools/exec.py``
Protocol) that runs model-chosen commands inside **one session-persistent remote ``modal.Sandbox``**,
starting **EMPTY** at ``/workspace`` with **no local-tree sync** — the hosted rung of the isolation
ladder ("nothing executes on your own machine"). It is a directly-tested class here; wiring it into
``bash``'s selection seam is task 074, and it carries no Credential Proxy (docker-only, task 075).

**The modal SDK, imported lazily (ADR-0011 §3).** ``modal`` is a first-class runtime dependency
(``modal>=1.5.1``) but is imported **inside** :func:`_load_modal`, not at module top level, so the
``none`` / ``docker`` / interactive-REPL paths never pay the modal import cost and importing
``decode.cli`` / ``decode.sandbox`` never pulls in ``modal``. No modal type ever leaks past this
module: callers see only :class:`ExecResult`. The whole modal surface used is the four calls the ADR
names — ``App.lookup``, ``Image.from_registry``, ``Sandbox.create``, ``sb.exec`` — plus
``sb.terminate`` and the ``ContainerProcess`` handle's ``stdout`` / ``stderr`` / ``wait`` (verified
against modal 1.5.1; every network call uses the ``.aio`` async variant so :meth:`run` never blocks
the event loop).

**Lifecycle (lazy, one per session).** On the **first** :meth:`run` the sandbox is created:
``App.lookup("decode-sandbox", create_if_missing=True)`` →
``Sandbox.create(app=…, image=Image.from_registry(settings.sandbox_image),
timeout=int(settings.sandbox_timeout_s))``, then a one-shot ``mkdir -p /workspace`` (the stock
``python:3.12-slim`` image has no ``/workspace``, so the per-command ``workdir`` needs it created
once). Every later command reuses that one sandbox. :meth:`aclose` (task 074 calls it on the exit
path) calls ``sandbox.terminate()`` — idempotent, best-effort, and loop-independent for free
(``synchronicity`` proxies it onto modal's own loop, unlike docker's loop-bound subprocess); the modal
``timeout`` (the sandbox's max lifetime) is the crash backstop.

**Per-command exec (empty scratch, no local tree).** Each command is a fresh
``sb.exec("bash", "-lc", command, workdir="/workspace")``: **filesystem changes persist** across calls
on the sandbox fs (``git clone`` / ``pip install`` stick — one sandbox), but **shell ``cwd`` / env
reset per call** because each ``exec`` is a brand-new process (the same effective semantics as ``none``
mode, unlike docker's persistent shell). The **local tree is absent** — the model is told this by
074's mode-specific ``bash`` description; the ``cwd`` argument to :meth:`run` is **ignored** (host
paths are meaningless on the remote sandbox). stdout and stderr are read as **separate** streams and
kept split, so :attr:`ExecResult.stderr` is faithful (no merge, unlike docker). Streams are read as
**raw bytes** (``text=False``) and decoded with ``errors="replace"`` (:func:`_decode`) so non-UTF-8
output (binary, latin-1) is replaced rather than crashing the turn — the :class:`ExecResult` contract.

**Timeout = kill the exec, keep the sandbox (ADR-0011 §3).** Each ``exec`` is bounded by modal's
**native per-exec ``timeout``** — the ``ContainerProcess`` handle exposes no terminate/kill, so this
is the only way to stop a hung command while the sandbox and its filesystem survive. On expiry modal
kills the exec process and ``wait()`` returns :data:`_MODAL_TIMEOUT_RETURNCODE` (``-1``; verified
against modal 1.5.1 — an internal ``ExecTimeoutError`` mapped to ``-1``); the executor normalizes that
to :data:`_TIMEOUT_EXIT` (the sibling executors' killed-by-signal sentinel) and returns the partial
output with ``timed_out=True``. Unlike docker, **no** ``note`` is set: only the exec process died, so
no session-level state was lost. ``ponytail:`` a per-exec timeout below one second is floored to one
second (modal's granularity is integer seconds), and a hung modal API call during
create/lookup is bounded only by the sandbox lifetime, not a client-side deadline.

**Streams are drained while the command runs, never after (ADR-0002 discipline).** :meth:`run` reads
stdout and stderr **concurrently** (``asyncio.gather``) and only then reads the exit code — waiting on
the exit first would deadlock a high-output command on an undrained pipe (the same reason
:class:`~decode.tools.exec.LocalExecutor` never cancels its ``communicate()``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from pathlib import Path
from typing import Any

from decode.config.settings import settings
from decode.tools.exec import ExecResult

logger = logging.getLogger(__name__)

# The remote sandbox's scratch working directory: created once on startup, the workdir of every
# command. Empty at first — no local-tree sync (the canonical modal shape, ADR-0011 §3).
_WORKSPACE = "/workspace"

# The modal App the sandbox is looked up / created under (``create_if_missing=True`` on first use).
_APP_NAME = "decode-sandbox"

# modal signals a per-exec timeout by returning ``-1`` from ``ContainerProcess.wait()`` (an internal
# ``ExecTimeoutError`` mapped to ``-1``; verified against modal 1.5.1). We detect that sentinel and
# normalize it to :data:`_TIMEOUT_EXIT` so ``bash`` sees the same killed-by-signal convention every
# executor uses (``LocalExecutor`` / ``DockerExecutor`` both use ``-signal.SIGKILL`` on timeout).
_MODAL_TIMEOUT_RETURNCODE = -1
_TIMEOUT_EXIT = -signal.SIGKILL


def _load_modal() -> Any:
    """Import the ``modal`` SDK lazily (ADR-0011 §3); the unit tests patch this seam with a fake.

    Kept out of module import so the ``none`` / ``docker`` / interactive-REPL paths — and importing
    ``decode.cli`` / ``decode.sandbox`` at all — never pull in ``modal``. Returns the module.
    """
    import modal

    return modal


class ModalExecutor:
    """Run commands in one session-persistent remote ``modal.Sandbox``, empty scratch (ADR-0011 §3).

    Construction is **inert** — no lookup, no create, no modal import: the sandbox is created lazily on
    the first :meth:`run`. Not safe for concurrent :meth:`run` calls on one instance (decode drives
    ``bash`` one call at a time). Call :meth:`aclose` to terminate the sandbox (task 074 wires it into
    the exit path).
    """

    def __init__(self) -> None:
        # The live remote sandbox (a ``modal.Sandbox``), created on first run(); ``Any`` so no modal
        # type leaks into this module's annotations. ``None`` until then and after :meth:`aclose`.
        self._sandbox: Any = None

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Run ``command`` in the remote sandbox at ``/workspace``; on timeout kill the exec (§3).

        Lazily creates the sandbox on the first call, then runs ``command`` as a fresh
        ``bash -lc`` process bounded by modal's per-exec ``timeout``. A normal command returns
        :class:`ExecResult` with ``timed_out=False`` and an empty ``note``. A timeout returns the
        partial output with ``timed_out=True`` and an empty ``note`` (only the exec died — the sandbox
        and its filesystem persist). The ``cwd`` argument is **ignored**: host paths are meaningless on
        the remote sandbox, which always runs commands in its own ``/workspace`` scratch.
        """
        sandbox = await self._ensure_sandbox()
        # modal takes an int-second per-exec timeout; floor at 1 so a sub-second ``timeout_s`` is not
        # rounded to 0 (which modal reads as "no timeout"). Sub-second precision isn't offered remotely.
        exec_timeout = max(1, int(timeout_s))
        stdout, stderr, exit_code = await self._exec(
            sandbox, "bash", "-lc", command, workdir=_WORKSPACE, timeout=exec_timeout
        )
        if exit_code == _MODAL_TIMEOUT_RETURNCODE:
            logger.debug(
                "[sandbox] $ %s timed out after %ds → exec killed (sandbox survives)",
                command,
                exec_timeout,
            )
            # ``note`` stays "": the sandbox + its fs persist (unlike docker's shell reset).
            return ExecResult(stdout, stderr, _TIMEOUT_EXIT, timed_out=True)
        logger.debug("[sandbox] $ %s → exit=%d bytes=%d", command, exit_code, len(stdout))
        return ExecResult(stdout, stderr, exit_code, timed_out=False)

    async def start(self, cwd: Path) -> None:
        """Eagerly create the remote sandbox — the REPL warm-up hook (idempotent; ADR-0011 §3).

        Called once by :func:`decode.tools.bash.warm_executor` at REPL launch so the session
        sandbox is live from the start instead of materializing invisibly mid-first-turn — and so
        the first ``bash`` skips the remote-create latency. ``cwd`` mirrors :meth:`run`'s contract
        (host paths are meaningless remotely — see :meth:`run`). Idempotent: a second ``start`` —
        or the first ``run`` after it — finds the cached sandbox and creates nothing new. Failures
        propagate; the warm-up call site wraps them and degrades to the lazy path.
        """
        del cwd  # same contract as run(): never a remote working directory
        await self._ensure_sandbox()

    async def aclose(self) -> None:
        """Terminate the session sandbox — idempotent, best-effort (ADR-0011 §3).

        Safe to call when nothing was ever created (a no-op that imports no modal) and safe to call
        twice (the second call finds nothing to do). A terminate failure is swallowed: teardown must
        never block the exit path, and the modal ``timeout`` (sandbox lifetime) is the crash backstop.

        **Loop-independent for free (task 074).** The headless runtime reaps the executor on a *fresh*
        event loop (kitaru's "calls" strategy runs each turn in its own ``asyncio.run`` loop that then
        closes), so the cached ``sandbox`` handle was created on a now-dead loop. Unlike docker's raw
        ``asyncio`` subprocess transports — which bind to their creating loop and genuinely break on a
        cross-loop teardown (see :meth:`DockerExecutor.aclose`) — modal's ``synchronicity`` proxies
        every ``.aio()`` call onto its **own** persistent background-thread loop, so ``terminate.aio()``
        through the stale handle reaps correctly from any caller loop (verified against modal 1.5.1 via
        ``Sandbox.list``: the sandbox drops off the live list whether terminated same-loop or cross-loop).
        """
        sandbox, self._sandbox = self._sandbox, None
        if sandbox is None:
            return
        logger.info("[sandbox] modal terminate %s", sandbox.object_id)
        with contextlib.suppress(Exception):
            await sandbox.terminate.aio()

    async def _ensure_sandbox(self) -> Any:
        """Create the remote sandbox on first use (looked up under the app, ``/workspace`` bootstrapped).

        Returns the cached sandbox on every later call — one sandbox per session, so filesystem changes
        persist across commands. The stock ``python:3.12-slim`` image has no ``/workspace``, so it is
        created once here before any command runs against it as its ``workdir``.
        """
        if self._sandbox is not None:
            return self._sandbox

        modal = _load_modal()
        app = await modal.App.lookup.aio(_APP_NAME, create_if_missing=True)
        image = modal.Image.from_registry(settings.sandbox_image)
        sandbox = await modal.Sandbox.create.aio(
            app=app, image=image, timeout=int(settings.sandbox_timeout_s)
        )
        logger.info("[sandbox] modal create %s image=%s", sandbox.object_id, settings.sandbox_image)
        # Ensure the scratch workspace exists before any command uses it as ``workdir``.
        await self._exec(sandbox, "mkdir", "-p", _WORKSPACE)
        self._sandbox = sandbox
        return sandbox

    @staticmethod
    async def _exec(
        sandbox: Any, *args: str, workdir: str | None = None, timeout: int | None = None
    ) -> tuple[str, str, int]:
        """Exec one process, drain both streams concurrently, then read the exit code (§3).

        Draining stdout and stderr **while the process runs** (never after ``wait``) is what stops a
        high-output command from deadlocking on an undrained pipe. Runs with ``text=False`` so modal
        yields **raw bytes** — decoded here with :func:`_decode` (``errors="replace"``), which upholds
        the :class:`ExecResult` contract that undecodable output is replaced, never crashes (modal's
        ``text=True`` reader decodes *strict* UTF-8 and raises on the first invalid byte — a binary
        command like ``head -c 16 /dev/urandom`` or ``cat`` of an image would otherwise blow up the
        turn). Both the normal and timeout branches route through here, so partial timeout output is
        decoded with replace too. Returns ``(stdout, stderr, exit_code)``.
        """
        proc = await sandbox.exec.aio(*args, workdir=workdir, timeout=timeout, text=False)
        stdout, stderr = await asyncio.gather(proc.stdout.read.aio(), proc.stderr.read.aio())
        exit_code = await proc.wait.aio()
        return _decode(stdout), _decode(stderr), exit_code


def _decode(raw: bytes) -> str:
    """Decode captured stream bytes as UTF-8, replacing undecodable bytes (never crash).

    Mirrors :func:`decode.tools.exec._decode` and the docker executor's helper — the shared
    names-not-crash discipline every :class:`~decode.tools.exec.CommandExecutor` upholds.
    """
    return raw.decode("utf-8", errors="replace")
