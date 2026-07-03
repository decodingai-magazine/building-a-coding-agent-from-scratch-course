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

**Lifecycle (lazy create + eager warm-up, one live sandbox per session).** On the **first**
:meth:`run` (or the REPL's eager :meth:`start` warm-up) the sandbox is created:
``App.lookup("decode-sandbox", create_if_missing=True)`` →
``Sandbox.create(app=…, image=Image.from_registry(settings.sandbox_image),
timeout=int(settings.sandbox_timeout_s))``, then a one-shot ``mkdir -p /workspace`` (the stock
``ghcr.io/astral-sh/uv:python3.12-bookworm-slim`` image has no ``/workspace``, so the per-command ``workdir`` needs it created
once). Every later command reuses that sandbox **while it is live**: the modal ``timeout`` is the
sandbox's max *lifetime* from create, so a long session can outlive it — :meth:`_ensure_sandbox`
probes ``poll()`` and transparently replaces a remotely-ended sandbox with a fresh one (re-seeded),
surfacing the filesystem reset to the model via the result ``note`` instead of crashing the turn.
:meth:`aclose` (task 074 calls it on the exit path) calls ``sandbox.terminate()`` — idempotent,
best-effort, and loop-independent for free (``synchronicity`` proxies it onto modal's own loop,
unlike docker's loop-bound subprocess); the modal ``timeout`` is also the crash backstop.

**Per-command exec (skills-seeded scratch, no local tree).** Each command is a fresh
``sb.exec("bash", "-lc", command, workdir="/workspace")``: **filesystem changes persist** across calls
on the sandbox fs (``git clone`` / ``pip install`` stick — one sandbox), but **shell ``cwd`` / env
reset per call** because each ``exec`` is a brand-new process (the same effective semantics as ``none``
mode, unlike docker's persistent shell). The **local tree is absent** — except the project's
``.decode/skills/`` directory, seeded at ``/workspace/.decode/skills`` on create (see
:meth:`_ensure_sandbox`) so skill scripts are runnable remotely; the model is told both by 074's
mode-specific ``bash`` description. The ``cwd`` argument to :meth:`run` is never a remote working
directory (host paths are meaningless on the remote sandbox) — it is read once at sandbox creation
to locate the local skills to seed. stdout and stderr are read as **separate** streams and
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

# The out-of-band note when a dead remote sandbox (max-lifetime expiry / external terminate) was
# transparently replaced by a fresh one — the model must know the filesystem state it built up is
# gone (the docker executor's shell-reset note is the sibling pattern, ADR-0011 §2).
_SANDBOX_RECREATED_NOTE = (
    "The remote sandbox's lifetime expired and a fresh one was created — its filesystem was "
    "reset, so packages installed and files created by earlier bash calls are gone."
)


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
        # Set by :meth:`_ensure_sandbox` when it dropped a remotely-ended sandbox and created a
        # fresh one; the next :meth:`run` pops it into the result ``note`` so the model learns the
        # filesystem was reset (one-shot, never sticky).
        self._recreated = False

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Run ``command`` in the remote sandbox at ``/workspace``; on timeout kill the exec (§3).

        Lazily creates the sandbox on the first call (seeding ``.decode/skills/`` from ``cwd`` —
        see :meth:`_ensure_sandbox`), then runs ``command`` as a fresh ``bash -lc`` process bounded
        by modal's per-exec ``timeout``. A normal command returns :class:`ExecResult` with
        ``timed_out=False``; a timeout returns the partial output with ``timed_out=True`` (only the
        exec died — the sandbox and its filesystem persist). ``note`` is empty on both, EXCEPT when
        this call had to replace a remotely-ended sandbox (max-lifetime expiry): then it carries the
        filesystem-reset notice. ``cwd`` is never a remote working directory (commands always run in
        the sandbox's own ``/workspace`` scratch — host paths are meaningless there); it is read
        once at sandbox creation to locate the local ``.decode/skills/`` to seed.
        """
        sandbox = await self._ensure_sandbox(cwd)
        # Pop the one-shot recreate flag: THIS result carries the reset notice, later ones don't.
        recreated, self._recreated = self._recreated, False
        note = _SANDBOX_RECREATED_NOTE if recreated else ""
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
            # Only the exec died — the sandbox + its fs persist (unlike docker's shell reset), so
            # the note stays whatever the recreate probe set (usually "").
            return ExecResult(stdout, stderr, _TIMEOUT_EXIT, timed_out=True, note=note)
        logger.debug("[sandbox] $ %s → exit=%d bytes=%d", command, exit_code, len(stdout))
        return ExecResult(stdout, stderr, exit_code, timed_out=False, note=note)

    async def start(self, cwd: Path) -> None:
        """Eagerly create the remote sandbox — the REPL warm-up hook (idempotent; ADR-0011 §3).

        Called once by :func:`decode.tools.bash.warm_executor` at REPL launch so the session
        sandbox is live from the start instead of materializing invisibly mid-first-turn — and so
        the first ``bash`` skips the remote-create latency. ``cwd`` follows :meth:`run`'s contract:
        never a remote working directory, only the locator for the ``.decode/skills/`` seed (see
        :meth:`_ensure_sandbox`). Idempotent: a second ``start`` — or the first ``run`` after it —
        finds the cached live sandbox and creates nothing new. Failures propagate; the warm-up
        call site wraps them and degrades to the lazy path.
        """
        await self._ensure_sandbox(cwd)

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

    async def _ensure_sandbox(self, cwd: Path | None) -> Any:
        """Create (or revive) the remote sandbox, seeding ``.decode/skills/`` from ``cwd`` (§3).

        Returns the cached sandbox while it is still **live** — one sandbox per session, so
        filesystem changes persist across commands. Liveness is probed with ``poll()`` (``None``
        while running): the modal ``timeout`` is the sandbox's max **lifetime** from create, so a
        long session can outlive it — a dead cached handle is dropped and a fresh sandbox created
        (and re-seeded) instead of crashing the next ``bash``; the recreate flag makes :meth:`run`
        carry the filesystem-reset ``note`` to the model.

        On create, when ``cwd`` names a project with a ``.decode/skills/`` directory it is layered
        onto the image at ``/workspace/.decode/skills`` via ``add_local_dir(copy=False)`` (mounted
        at container start — no image rebuild), so the cwd-relative skill-script paths the skill
        payloads hand the model resolve inside the remote ``workdir``. ONLY the skills are seeded —
        sessions / MEMORY.md / logs stay absent (the ``.gitignore`` boundary; the local tree is
        still never synced, ADR-0011 §3). ``cwd=None`` (direct/test callers) and a missing skills
        dir both skip seeding. The stock ``ghcr.io/astral-sh/uv:python3.12-bookworm-slim`` image has no ``/workspace``, so it is
        created once before any command runs against it as its ``workdir``.
        """
        if self._sandbox is not None:
            if await self._sandbox.poll.aio() is None:
                return self._sandbox
            # The sandbox ended remotely (max-lifetime expiry / external terminate): the cached
            # handle is dead and any exec through it would crash the turn. Drop it and fall
            # through to a fresh create (+ re-seed); run() surfaces the reset via the note.
            logger.info(
                "[sandbox] modal sandbox %s ended remotely; creating a fresh one",
                self._sandbox.object_id,
            )
            self._sandbox = None
            self._recreated = True

        modal = _load_modal()
        app = await modal.App.lookup.aio(_APP_NAME, create_if_missing=True)
        image = modal.Image.from_registry(settings.sandbox_image)
        skills_dir = None if cwd is None else cwd / settings.skills_dir
        if skills_dir is not None and skills_dir.is_dir():
            # Seed ONLY the project's skills (never sessions/MEMORY/logs): skill payloads tell the
            # model to run `.decode/skills/<name>/scripts/…` via bash, and those cwd-relative paths
            # must resolve inside the remote /workspace workdir.
            image = image.add_local_dir(
                skills_dir, f"{_WORKSPACE}/{settings.skills_dir.as_posix()}", copy=False
            )
            logger.info(
                "[sandbox] modal seed %s → %s/%s",
                skills_dir,
                _WORKSPACE,
                settings.skills_dir.as_posix(),
            )
        # An explicit long-lived entrypoint (the docker keeper's exact shape): without it modal runs
        # the image's own CMD, and an image whose CMD exits immediately — the default astral uv image
        # ships ``Cmd=[uv]``, which prints help and quits — would take the whole sandbox down with it.
        sandbox = await modal.Sandbox.create.aio(
            "sleep", "infinity", app=app, image=image, timeout=int(settings.sandbox_timeout_s)
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
