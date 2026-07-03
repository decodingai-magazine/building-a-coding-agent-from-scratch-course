"""The one unified sandbox executor + the thin backend seam it drives (ADR-0012 §2,4).

The core architectural collapse of ADR-0012: instead of two divergent executors (docker's
persistent shell, modal's remote scratch — ADR-0011 §2,3) there is now **one**
:class:`SandboxExecutor` — a :class:`~decode.tools.exec.CommandExecutor` behind the ADR-0002 ``run``
seam — over a thin :class:`SandboxBackend` Protocol. The Protocol carries **exec + file ops +
lifecycle**, so the *same* executor drives docker (079) and modal (080), and the file/search tools
route their byte transport through the same seam (the "swap the set" pattern, wired in 081).

**Fresh-exec, one sandbox per session.** :meth:`SandboxExecutor.run` is ``ensure-created →
backend.exec("bash","-lc", command) → ExecResult`` — one exec per call, **no persistent shell**. The
container / remote sandbox is created once (lazily on the first ``run`` or eagerly via :meth:`start`)
and its filesystem persists across calls, but each command is a brand-new process, so ``cd`` /
``export`` do **not** carry over (chain them in one command). This is what deletes docker's
marker/``$?`` protocol and its loop-bound-shell teardown (ADR-0012 §2).

**Workspace resolution contract (stable from here).** The executor works against a single logical
root — ``/workspace`` in the sandbox, backed by the host ``settings.sandbox_workspace_dir``. The
canonical path is set by :meth:`start` (the warm-up / headless install site passes
``workspace.workspace_dir(cwd)``); ``run(cwd=…)`` is **ignored for the workdir** by a sandbox executor
(commands always run in ``/workspace``) and only derives ``workspace.workspace_dir(cwd)`` as a
test/lazy fallback when nothing was started. Skills are seeded host-side into that workspace by
:func:`~decode.sandbox.workspace.seed_skills` (replacing docker's read-only skills mount and modal's
``add_local_dir`` — ADR-0012 §5), so every entry point gets them.

**Teardown is loop-independent by construction.** :meth:`aclose` runs ``backend.export()`` (the
session-end sandbox→host sweep — a docker no-op, since its mount is already live) then
``backend.destroy()``. Because fresh-exec holds **no** subprocess or pipe transport across ``run``
calls, there is no loop-bound handle to reap from a foreign loop: the headless reaper's ``docker rm
-f`` (a fresh subprocess) needs no old event loop, which retires the whole ADR-0011 §4 loop-free
shell-teardown machinery. Idempotent + best-effort: the backends no-op when nothing was created.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from decode.sandbox.workspace import seed_skills, workspace_dir
from decode.tools.exec import ExecResult

logger = logging.getLogger(__name__)

# Exit code + note the executor renders when the backend could not be created (daemon down / bad
# image / missing creds): the never-crash contract — a ``bash`` call gets a rendered failure instead
# of an exception crashing the turn. 125 is docker's "container failed to run" convention, reused as a
# generic "sandbox unavailable" code; the backend's own error text rides on ``stderr`` so the model
# still sees the specific cause (e.g. "Cannot connect to the Docker daemon").
_SANDBOX_UNAVAILABLE_EXIT = 125
_SANDBOX_UNAVAILABLE_NOTE = "The sandbox backend became unreachable — the session was lost."


@dataclass(frozen=True, slots=True)
class FileStat:
    """Metadata for one Workspace entry on a **logical** (workspace-relative, POSIX) path (ADR-0012 §4).

    The backend-agnostic shape the file/search tools read through the seam (wired in 081): ``path`` is
    the entry's logical path relative to the Workspace root (never a host path — modal paths are not
    host paths), ``is_dir`` distinguishes a directory from a file, and ``size`` is the byte size (``0``
    for a directory). Frozen so a stat result is an immutable snapshot.
    """

    path: str
    is_dir: bool
    size: int


class SandboxBackend(Protocol):
    """The thin per-backend seam a :class:`SandboxExecutor` drives — exec + file ops + lifecycle (§2,4).

    One Protocol, two adapters: :class:`~decode.sandbox.docker_backend.DockerBackend` (079, pathlib on
    a bind mount) and the modal backend (080, the remote ``SandboxFilesystem``). Only **byte
    transport** is per-backend; the shared host-side file-tool logic (containment path-math, edit
    search/replace, truncation, rendering) lives *above* this seam (081). ``glob`` / ``grep`` run as
    remote commands via :meth:`exec` (``find`` / ``grep``), not through these file ops.

    * **Lifecycle** — :meth:`create` brings the sandbox up (incl. any one-shot bootstrap) against the
      resolved Workspace; :meth:`export` sweeps the sandbox filesystem back to the host at session end
      (a docker no-op — its mount is live); :meth:`destroy` tears the sandbox down (loop-free,
      best-effort). All idempotent: a no-op when nothing was created.
    * **exec** — run one process (``bash -lc <command>`` for the ``run`` seam) in ``/workspace``,
      bounded by ``timeout_s``, returning an :class:`~decode.tools.exec.ExecResult`. **Never raises for
      an infra failure** — it renders one (the never-crash contract), so the executor's ``run`` stays
      crash-free.
    * **file ops** — on **logical** paths relative to the Workspace root: :meth:`read_bytes` /
      :meth:`write_bytes` / :meth:`make_directory` / :meth:`stat` / :meth:`list_dir` / :meth:`remove`.
      Containment/normalization is the caller's job (above the seam); these operate on the (already
      validated) logical path.
    """

    async def create(self, workspace: Path) -> None:
        """Bring the sandbox up against ``workspace`` (the resolved host Workspace root)."""
        ...

    async def exec(self, *args: str, timeout_s: float) -> ExecResult:
        """Run one process in ``/workspace`` bounded by ``timeout_s``; render (never raise) on failure."""
        ...

    async def read_bytes(self, rel: str) -> bytes:
        """Read the bytes of the logical Workspace path ``rel``."""
        ...

    async def write_bytes(self, rel: str, data: bytes) -> None:
        """Write ``data`` to the logical Workspace path ``rel`` (creating parents)."""
        ...

    async def make_directory(self, rel: str) -> None:
        """Create the logical Workspace directory ``rel`` (parents included, idempotent)."""
        ...

    async def stat(self, rel: str) -> FileStat | None:
        """Return the :class:`FileStat` for the logical path ``rel``, or ``None`` if it is absent."""
        ...

    async def list_dir(self, rel: str) -> list[FileStat]:
        """List the entries of the logical Workspace directory ``rel`` as :class:`FileStat`s."""
        ...

    async def remove(self, rel: str) -> None:
        """Remove the logical Workspace path ``rel`` (a file or a directory tree)."""
        ...

    async def export(self) -> None:
        """Sweep the sandbox filesystem back to the host Workspace (session-end; docker no-op)."""
        ...

    async def destroy(self) -> None:
        """Tear the sandbox down (loop-free, best-effort, idempotent)."""
        ...


class SandboxExecutor:
    """One :class:`~decode.tools.exec.CommandExecutor` over a :class:`SandboxBackend` (ADR-0012 §2).

    Construction is **inert** — no container, no remote sandbox, no import of a backend SDK: the
    backend is created lazily on the first :meth:`run` (or eagerly by :meth:`start`). Not safe for
    concurrent :meth:`run` calls on one instance (decode drives ``bash`` one call at a time). Call
    :meth:`aclose` to reap the session (the interactive exit path + the headless flow ``finally``).

    The optional Credential-Proxy wiring (ADR-0011 §6, retained) lives entirely on the *backend*: the
    headless docker flow constructs ``SandboxExecutor(DockerBackend(network=…, proxy_env=…,
    ca_cert_host_path=…))`` and :meth:`start`s it, so the executor itself stays backend-agnostic.
    """

    def __init__(self, backend: SandboxBackend) -> None:
        self._backend = backend
        # The resolved host Workspace root (``settings.sandbox_workspace_dir``). Set by :meth:`start`
        # (the canonical path) or derived from ``run(cwd=…)`` as a lazy fallback; fixed for the session.
        self._workspace: Path | None = None
        # Memo guard: create the sandbox at most once (so a container/sandbox is not rebuilt per call).
        self._created = False

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Ensure the sandbox exists, then ``bash -lc`` ``command`` in ``/workspace`` (ADR-0012 §2).

        **Fresh-exec** — one ``backend.exec`` per call, no persistent shell (``cd`` / ``export`` do not
        persist; the filesystem does). ``cwd`` is **not** the workdir (a sandbox runs in ``/workspace``);
        it only derives the Workspace when nothing was started (:meth:`_resolve_workspace`). If the
        backend cannot be created (daemon down / bad image), the infra exception is caught and a rendered
        failure :class:`~decode.tools.exec.ExecResult` (exit 125 + a session-lost note + the cause on
        ``stderr``) is returned so the model reacts instead of the tool crashing — and the memo is left
        un-created so a later ``run`` re-attempts from scratch.
        """
        try:
            await self._ensure_created(cwd)
        except (RuntimeError, OSError) as exc:
            # The backend could not start (docker daemon down, bad image, modal creds missing). Leave
            # ``_created`` False so a later ``run`` retries, and render the failure for the model.
            logger.warning("[sandbox] backend create failed; rendering infra failure: %s", exc)
            return ExecResult(
                "",
                str(exc),
                _SANDBOX_UNAVAILABLE_EXIT,
                timed_out=False,
                note=_SANDBOX_UNAVAILABLE_NOTE,
            )
        return await self._backend.exec("bash", "-lc", command, timeout_s=timeout_s)

    async def start(self, workspace: Path) -> None:
        """Eagerly create the sandbox against ``workspace`` — the warm-up hook (idempotent; §2,5).

        Called by :func:`decode.tools.bash.warm_executor` (REPL) and the headless
        :func:`decode.runtime.flow._sandbox_proxy` so the sandbox is live (and, for docker, visible in
        ``docker ps``) from launch instead of materializing mid-first-turn. ``workspace`` is the
        already-resolved host Workspace root (the call site passes
        :func:`~decode.sandbox.workspace.workspace_dir`), so it is stored verbatim — never re-derived
        (which would double-nest a ``.decode/sandbox`` under it). Idempotent: a second ``start`` — or the
        first ``run`` after it — finds the created backend and does nothing new. Failures propagate; the
        call site degrades to the lazy path.
        """
        self._workspace = workspace
        await self._ensure_created(workspace)

    async def export(self) -> None:
        """Sweep the sandbox filesystem back to the host Workspace (ADR-0012 §5,8).

        The standalone hook a mid-session ``/ship`` (083) triggers and the modal backend's session-end
        sweep uses; a **docker no-op** (its bind mount is already the host Workspace). Safe before any
        create (the backend no-ops when nothing is live).
        """
        await self._backend.export()

    async def aclose(self) -> None:
        """Reap the session — ``export()`` then ``destroy()`` (idempotent, best-effort; §2).

        Runs the session-end sandbox→host sweep (docker no-op) and then tears the sandbox down. Safe
        when nothing was ever created (the backends no-op) and safe to call twice. **Loop-independent**:
        fresh-exec holds no loop-bound subprocess, so ``destroy`` (docker ``docker rm -f`` — a fresh
        subprocess) reaps from any caller loop, including the headless reaper's fresh loop. ``export``
        runs before ``destroy`` even if it raises, so a sweep failure never skips the teardown.
        """
        self._created = False
        try:
            await self._backend.export()
        finally:
            await self._backend.destroy()

    async def _ensure_created(self, cwd: Path) -> None:
        """Create the sandbox once: resolve the Workspace, seed skills host-side, then ``backend.create``.

        Skills are seeded into the Workspace **before** ``create`` so a backend whose bootstrap uploads
        the Workspace (modal, 080) carries them; docker's live mount picks them up either way. Guarded by
        ``_created`` so it runs exactly once per session.
        """
        if self._created:
            return
        workspace = self._resolve_workspace(cwd)
        self._workspace = workspace
        seed_skills(workspace)
        await self._backend.create(workspace)
        self._created = True

    def _resolve_workspace(self, cwd: Path) -> Path:
        """The session Workspace: the one set by :meth:`start`, else ``workspace_dir(cwd)`` (lazy fallback).

        The stable resolution contract (ADR-0012 §3): a started executor uses its canonical Workspace;
        an un-started one (a direct ``run`` in a test, or the lazy first ``bash`` after a failed warm-up)
        derives ``harness_home/.decode/sandbox`` from ``cwd`` — the single place that path is computed.
        """
        if self._workspace is not None:
            return self._workspace
        return workspace_dir(cwd)
