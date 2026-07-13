"""The one unified sandbox executor + the thin backend seam it drives (ADR-0012 §2,4).

One :class:`SandboxExecutor` (a :class:`~decode.tools.exec.CommandExecutor` behind the ``run`` seam)
over a thin :class:`SandboxBackend` Protocol carrying exec + file ops + lifecycle, with two adapters:
docker (pathlib on a live bind mount) and modal (the remote ``SandboxFilesystem``). **Fresh-exec, one
sandbox per session**: the sandbox is created once and its filesystem persists, but each command is a
brand-new process — ``cd`` / ``export`` do **not** carry over. Commands always run in ``/workspace``
(backed by the host ``settings.sandbox_workspace_dir``); skills are seeded host-side into that
Workspace. Teardown (``export`` then ``destroy``) is loop-independent — fresh-exec holds no
loop-bound handle — and idempotent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from decode.sandbox.workspace import seed_skills, workspace_dir
from decode.tools.exec import ExecResult

logger = logging.getLogger(__name__)

# Exit code + note rendered when the backend could not be created (daemon down / bad image / missing
# creds): the never-crash contract — 125 is docker's "failed to run" convention, and the backend's own
# error text rides on ``stderr`` so the model still sees the specific cause.
_SANDBOX_UNAVAILABLE_EXIT = 125
_SANDBOX_UNAVAILABLE_NOTE = "The sandbox backend became unreachable — the session was lost."


@dataclass(frozen=True, slots=True)
class FileStat:
    """Metadata for one Workspace entry on a **logical** (workspace-relative, POSIX) path (ADR-0012 §4).

    The backend-agnostic shape the file/search tools read through the seam: ``path`` is never a host
    path; ``size`` is ``0`` for a directory. Frozen so a stat result is an immutable snapshot.
    """

    path: str
    is_dir: bool
    size: int


class WorkspaceEscape(OSError):
    """A logical path resolved (following symlinks) **outside** the Workspace root (ADR-0012 §4).

    The physical containment layer: string math above the seam rejects ``..`` / absolute escapes but
    cannot see a symlink planted inside the Workspace that points onto the host, so a backend whose
    file ops are plain pathlib on a host-shared mount (docker) resolves physically and raises this;
    modal is naturally host-safe. Subclasses :class:`OSError` **on purpose**: the file-tool layer
    renders it through its existing ``(RuntimeError, OSError)`` boundary without importing this class,
    so the ``none`` path pulls in no sandbox module (the laziness invariant).
    """


class SandboxBackend(Protocol):
    """The thin per-backend seam a :class:`SandboxExecutor` drives — exec + file ops + lifecycle (§2,4).

    One Protocol, two adapters: :class:`~decode.sandbox.docker_backend.DockerBackend` (pathlib on a
    bind mount) and the modal backend (the remote ``SandboxFilesystem``). Only **byte transport** is
    per-backend; the shared file-tool logic (containment path-math, edit search/replace, truncation,
    rendering) lives *above* this seam. ``glob`` / ``grep`` run as remote commands via :meth:`exec`.
    :meth:`exec` **never raises for an infra failure** — it renders one (the never-crash contract).
    File ops take **logical** Workspace-relative paths already validated above the seam; a
    real-filesystem backend (docker) additionally contains *physically*, raising
    :class:`WorkspaceEscape` on a symlink escape. Lifecycle (:meth:`create` / :meth:`export` /
    :meth:`destroy`) is idempotent and no-ops when nothing was created.
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

    Construction is **inert** — no container, no remote sandbox, no backend-SDK import: the backend is
    created lazily on the first :meth:`run` (or eagerly by :meth:`start`). Not safe for concurrent
    :meth:`run` calls on one instance. Call :meth:`aclose` to reap the session. Every backend-specific
    concern (credential injection included) lives on the *backend*, so the executor stays agnostic.
    """

    def __init__(self, backend: SandboxBackend) -> None:
        self._backend = backend
        # The resolved host Workspace root; set by :meth:`start` or derived lazily, fixed per session.
        self._workspace: Path | None = None
        # Memo guard: create the sandbox at most once.
        self._created = False

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Ensure the sandbox exists, then ``bash -lc`` ``command`` in ``/workspace``.

        **Fresh-exec** — no persistent shell (``cd`` / ``export`` do not persist; the filesystem
        does). ``cwd`` is **not** the workdir; it only derives the Workspace when nothing was started.
        A backend create failure is rendered as exit-125 (never a crash), and the memo is left
        un-created so a later ``run`` re-attempts from scratch.
        """
        try:
            await self._ensure_created(cwd)
        except (RuntimeError, OSError) as exc:
            # The backend could not start (daemon down / bad image / missing creds); render it.
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
        """Eagerly create the sandbox against ``workspace`` — the warm-up hook (idempotent).

        ``workspace`` is the already-resolved host Workspace root, stored verbatim — never re-derived
        (which would double-nest a ``.decode/sandbox`` under it). Failures propagate; the call site
        degrades to the lazy path.
        """
        self._workspace = workspace
        await self._ensure_created(workspace)

    async def file_backend(self, cwd: Path) -> SandboxBackend:
        """Return the **created** backend for the file/search tools' byte transport (ADR-0012 §4).

        Shares the **one** backend ``bash`` runs through, so a file written by a tool is visible to
        ``bash`` and vice-versa. Ensures the sandbox exists first (reusing the memo, exactly as
        :meth:`run` does); ``cwd`` only derives the Workspace when nothing was started.
        """
        await self._ensure_created(cwd)
        return self._backend

    async def export(self) -> None:
        """Sweep the sandbox filesystem back to the host Workspace (ADR-0012 §5,8).

        The standalone hook a mid-session ``/ship`` triggers; a **docker no-op** (its bind mount is
        already the host Workspace). Safe before any create.
        """
        await self._backend.export()

    async def aclose(self) -> None:
        """Reap the session — ``export()`` then ``destroy()`` (idempotent, best-effort).

        ``export`` runs before ``destroy`` even if it raises, so a sweep failure never skips the
        teardown; loop-independent (fresh-exec holds no loop-bound subprocess).
        """
        self._created = False
        try:
            await self._backend.export()
        finally:
            await self._backend.destroy()

    async def _ensure_created(self, cwd: Path) -> None:
        """Create the sandbox once: resolve the Workspace, seed skills host-side, then ``backend.create``.

        Skills are seeded **before** ``create`` so a bootstrap-uploading backend (modal) carries them.
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

        The single place ``harness_home/.decode/sandbox`` is derived from ``cwd`` (a direct ``run`` in
        a test, or the lazy first ``bash`` after a failed warm-up) — ADR-0012 §3.
        """
        if self._workspace is not None:
            return self._workspace
        return workspace_dir(cwd)
