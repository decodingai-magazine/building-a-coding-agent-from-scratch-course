"""The Modal sandbox backend — fresh ``sb.exec`` + direct SandboxFilesystem file ops (ADR-0012 §2,4,5).

:class:`ModalBackend` is the second :class:`~decode.sandbox.executor.SandboxBackend` driven by the one
:class:`~decode.sandbox.executor.SandboxExecutor`. It replaces the ADR-0011 §3 ``ModalExecutor`` —
folding its verified modal facts (``.aio`` everywhere, ``text=False`` + ``errors="replace"``, the ``-1``
per-exec-timeout sentinel, ``poll()`` liveness, the ``sleep infinity`` entrypoint, the lazy
:func:`_load_modal` seam) into the unified fresh-exec shape and **adding file ops + a bootstrap upload +
an export sweep** so file/search tools route their byte transport through the same seam as docker (081).

**The core ADR-0012 change vs the retired executor.** ADR-0011 ran modal as an *empty remote scratch*
with the local tree absent and skills layered on via ``add_local_dir``. ADR-0012 makes ``/workspace`` an
**isolated Workspace**: file ops go **directly against the remote via the SandboxFilesystem API** (no
host mirror — ``read`` never lies, so the deletion-blind mtime-sync is rejected), and the only bytes that
cross the wire are ONE bootstrap upload of the host Workspace at :meth:`create` and ONE end-of-session
sweep at :meth:`export`. ``add_local_dir`` is gone.

* **create** — ``App.lookup`` → ``Sandbox.create("sleep","infinity", image=from_registry(sandbox_image),
  timeout=…)`` → ``mkdir -p /workspace`` → the **bootstrap upload** (see below). The keeper entrypoint is
  ``sleep infinity`` (the docker keeper's exact shape): without it modal runs the image's own ``CMD`` —
  the astral uv default (``Cmd=[uv]``) prints help and exits, taking the sandbox down moments after
  create.
* **bootstrap upload (host → remote), tar-over-exec** — the whole host Workspace (the cloned repo +
  host-seeded ``.decode/skills``) is packed into ONE in-memory tar (:func:`~decode.sandbox.workspace.tar_dir`),
  streamed to a ``/tmp`` tar on the remote via ``filesystem.write_bytes`` (which chunks the whole payload
  internally — no per-call size cap), then unpacked with a single remote ``tar -x`` into ``/workspace``.
  ``ponytail:`` a whole-tree tar — a huge repo pays a proportional upload; a content-addressed / git-diff
  transport is the documented upgrade path (ADR-0012 Future work). Chosen over ``copy_from_local`` (which
  is **single-file** on modal 1.5.1 — a whole tree would be N round-trips) and over hand-driving
  ``ContainerProcess.stdin`` (``write_bytes`` already does the stdin drain/``write_eof``/``ConflictError``
  dance robustly), so the mechanism is one ``write_bytes`` + one ``tar -x`` per direction.
* **exec** — a fresh ``sb.exec("bash","-lc", command, workdir="/workspace", timeout=…, text=False)`` per
  call: **filesystem changes persist** across calls (one sandbox), but ``cd`` / ``export`` reset per call
  (each exec is a brand-new process — the same semantics as ``none``). Streams are drained **concurrently
  while the command runs** (``asyncio.gather``, before reading the exit) so a high-output command never
  deadlocks on an undrained pipe, and decoded with ``errors="replace"`` so binary output never crashes
  the turn. A timeout kills only the exec (modal returns ``-1``); the sandbox and its fs survive, with an
  empty ``note`` (nothing session-level was lost).
* **file ops = the SandboxFilesystem API** against ``/workspace/<rel>`` — direct against the remote, no
  mirror, always truthful (a file ``bash`` wrote is returned by :meth:`read_bytes`; a :meth:`remove` is
  reflected by a later :meth:`stat`). ``glob`` / ``grep`` run as remote commands via :meth:`exec` (081) —
  never through these. Missing-FILE semantics are normalized to match
  :class:`~decode.sandbox.docker_backend.DockerBackend`: :meth:`stat` → ``None``; :meth:`read_bytes` /
  :meth:`list_dir` → ``FileNotFoundError`` (modal's ``SandboxFilesystemNotFoundError`` does **not**
  subclass it — verified on modal 1.5.1 — so it is caught and re-raised), and :meth:`remove` tolerates a
  missing path. And like :meth:`exec`, every op **revives a dead sandbox**: a max-lifetime-expired /
  terminated remote makes the op raise ``NotFoundError`` (a raw ``GRPCError``, distinct from the missing
  -file error), so :meth:`_run_file_op` drops the dead handle, recreates + re-bootstraps, and retries the
  op ONCE — a second death surfaces a clean :class:`RuntimeError`, never a raw GRPC type. A file op has no
  ``note`` channel, so its one-shot restore note rides the NEXT :meth:`exec` result (the same flag).
* **export** — the ONE end-of-session sweep ``/workspace`` → host Workspace: a remote ``tar -c`` of
  ``/workspace``, ``filesystem.read_bytes`` the tar down, then host-side
  :func:`~decode.sandbox.workspace.extract_tar`. **Standalone-callable** — the sandbox stays alive — so a
  mid-session ``/ship`` (083) can sweep while work continues; :meth:`destroy` is separate. Best-effort: a
  sweep failure logs and returns (teardown must never block the exit path).
* **revival (max-lifetime expiry)** — the modal ``timeout`` is the sandbox's max *lifetime* from create,
  so a long session can outlive it. :meth:`exec` probes ``poll()`` and, on a dead sandbox, recreates +
  **re-bootstraps from the host ``.decode/sandbox`` state** (file ops are direct, so the host still holds
  the last cloned/exported state — not the in-flight remote changes), surfacing a one-shot ``note`` that
  the Workspace was restored from the last local state and that in-sandbox changes since the last export
  may be lost — honest, still better than the old total reset (ADR-0012 revival ceiling).
* **destroy** — ``sandbox.terminate()``, idempotent + best-effort + **loop-independent for free**
  (``synchronicity`` proxies ``terminate.aio()`` onto modal's own background loop, so the headless
  reaper's fresh loop reaps a handle created on a now-dead per-call loop — ADR-0011 §4, retained).

**The modal SDK, imported lazily.** ``modal`` is a first-class dependency but imported **inside**
:func:`_load_modal`, never at module top level, so the ``none`` / ``docker`` / interactive-REPL paths —
and importing ``decode.cli`` / ``decode.sandbox`` — never pull it in. No modal type ever leaks past this
module: callers see only :class:`~decode.tools.exec.ExecResult` and
:class:`~decode.sandbox.executor.FileStat`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from decode.config.settings import settings
from decode.sandbox.executor import FileStat
from decode.sandbox.workspace import extract_tar, tar_dir
from decode.tools.exec import ExecResult

logger = logging.getLogger(__name__)

# The return type of a wrapped file op, so :meth:`ModalBackend._run_file_op` stays type-preserving
# across ops that return ``bytes`` / ``FileStat | None`` / ``list[FileStat]`` / ``None``.
_T = TypeVar("_T")

# The remote sandbox's Workspace: created once on startup, the workdir of every command and the base of
# every file op (ADR-0012 §3 — ``/workspace`` ≡ the host ``settings.sandbox_workspace_dir``).
_WORKSPACE = "/workspace"

# The modal App the sandbox is looked up / created under (``create_if_missing=True`` on first use).
_APP_NAME = "decode-sandbox"

# The remote ``/tmp`` staging paths for the ONE tar bootstrap upload and the ONE export sweep — absolute
# (the SandboxFilesystem API requires absolute remote paths) and OUTSIDE ``/workspace`` so neither is
# swept into itself. Written/read via ``write_bytes`` / ``read_bytes``; unpacked/packed via a ``tar`` exec.
_BOOTSTRAP_TAR = "/tmp/decode-bootstrap.tar"
_EXPORT_TAR = "/tmp/decode-export.tar"

# modal signals a per-exec timeout by returning ``-1`` from ``ContainerProcess.wait()`` (an internal
# ``ExecTimeoutError`` mapped to ``-1``; verified against modal 1.5.1). We normalize that to
# :data:`_TIMEOUT_EXIT` so ``bash`` sees the same killed-by-signal convention every executor uses
# (``LocalExecutor`` / ``DockerBackend`` both use ``-signal.SIGKILL`` on timeout).
_MODAL_TIMEOUT_RETURNCODE = -1
_TIMEOUT_EXIT = -signal.SIGKILL

# The exit code + note rendered when the remote sandbox could not be brought back (a revival create /
# bootstrap failed): the never-crash contract — a ``bash`` call gets a rendered failure, not an exception
# crashing the turn (125 = the generic "sandbox unavailable" code the sibling backends use).
_SANDBOX_LOST_EXIT = 125
_SANDBOX_LOST_NOTE = "The modal sandbox became unreachable — the session was lost."

# The one-shot ``note`` when a dead remote sandbox (max-lifetime expiry / external terminate) was
# recreated + re-bootstrapped from the host state. Honest per the ADR-0012 revival ceiling: the Workspace
# is restored from the last LOCAL state, so remote-only changes since the last export may be lost.
_SANDBOX_RECREATED_NOTE = (
    "The remote sandbox's lifetime expired; a fresh one was created and its workspace was restored "
    "from the last local state on the host. Any changes made inside the sandbox since the last export "
    "may be lost."
)


def _load_modal() -> Any:
    """Import the ``modal`` SDK lazily (ADR-0012 §2); the unit tests patch this seam with a fake.

    Kept out of module import so the ``none`` / ``docker`` / interactive-REPL paths — and importing
    ``decode.cli`` / ``decode.sandbox`` at all — never pull in ``modal``. Returns the module.
    """
    import modal

    return modal


class ModalBackend:
    """Run commands + file ops in one session-persistent remote ``modal.Sandbox`` (ADR-0012 §2,4,5).

    Construction is **inert** — no lookup, no create, no modal import: the sandbox is created on
    :meth:`create` (called by the :class:`~decode.sandbox.executor.SandboxExecutor` lazily on the first
    ``run`` or eagerly via ``start``). Not safe for concurrent :meth:`exec` calls on one instance (decode
    drives ``bash`` one call at a time).
    """

    def __init__(self) -> None:
        # The live remote sandbox (a ``modal.Sandbox``), created on :meth:`create`; ``Any`` so no modal
        # type leaks into this module's annotations. ``None`` until then and after :meth:`destroy`.
        self._sandbox: Any = None
        # The resolved host Workspace root — the bootstrap-upload source and the export-sweep target. Set
        # on :meth:`create`; re-used on revival to re-bootstrap from the last local state.
        self._workspace: Path | None = None
        # Set by :meth:`exec` when it revived a remotely-ended sandbox; the same call pops it into the
        # result ``note`` so the model learns the Workspace was restored (one-shot, never sticky).
        self._recreated = False

    # --- lifecycle ------------------------------------------------------------------------------

    async def create(self, workspace: Path) -> None:
        """Bring the remote sandbox up + bootstrap-upload ``workspace`` into ``/workspace`` (ADR-0012 §2,5).

        Stores the resolved host Workspace (the bootstrap source / export target), then — unless a
        sandbox is already live (idempotent) — spawns one and uploads the Workspace. A modal / bootstrap
        failure raises (the executor renders it as exit-125; the warm-up call site degrades to lazy) and
        the half-built sandbox is reaped, never leaked.
        """
        self._workspace = workspace.resolve()
        if self._sandbox is not None:
            return
        await self._spawn_and_bootstrap()

    async def export(self) -> None:
        """Sweep ``/workspace`` back to the host Workspace, leaving the sandbox alive (ADR-0012 §5,8).

        The ONE end-of-session (or mid-session ``/ship``) sweep: a remote ``tar -c`` of ``/workspace``,
        ``read_bytes`` the tar down, then host-side :func:`~decode.sandbox.workspace.extract_tar` so the
        final Workspace is host-visible for the git hand-back. **Standalone** — it does NOT
        :meth:`destroy` (``/ship`` continues the session). A no-op when nothing was created; **best-effort**
        — a sweep failure logs and returns so it never blocks teardown (``aclose`` still reaps).
        """
        sandbox = self._sandbox
        if sandbox is None or self._workspace is None:
            return
        try:
            _, stderr, code = await self._exec(
                sandbox, "bash", "-lc", f"tar -cf {_EXPORT_TAR} -C {_WORKSPACE} .", timeout=None
            )
            if code != 0:
                logger.warning(
                    "[sandbox] modal export tar failed (exit %d): %s", code, stderr.strip()
                )
                return
            data = await sandbox.filesystem.read_bytes.aio(_EXPORT_TAR)
            extract_tar(data, self._workspace)
            with contextlib.suppress(Exception):
                await self._exec(sandbox, "bash", "-lc", f"rm -f {_EXPORT_TAR}", timeout=None)
            logger.info("[sandbox] modal export swept %s → %s", _WORKSPACE, self._workspace)
        except Exception as exc:  # best-effort: a sweep failure must never block the exit path
            logger.warning("[sandbox] modal export failed: %s", exc)

    async def destroy(self) -> None:
        """Terminate the session sandbox — idempotent, best-effort, loop-independent (ADR-0012 §2).

        ``sandbox.terminate.aio()`` through ``synchronicity`` reaps from any caller loop (including the
        headless reaper's fresh loop) even though the handle was created on a now-dead per-call loop. Safe
        when nothing was created (a no-op that imports no modal) and safe to call twice; a terminate
        failure is swallowed (teardown must never block the exit path; the modal ``timeout`` is the crash
        backstop).
        """
        sandbox, self._sandbox = self._sandbox, None
        self._recreated = False
        if sandbox is None:
            return
        logger.info("[sandbox] modal terminate %s", sandbox.object_id)
        with contextlib.suppress(Exception):
            await sandbox.terminate.aio()

    # --- command exec ---------------------------------------------------------------------------

    async def exec(self, *args: str, timeout_s: float) -> ExecResult:
        """Run one fresh ``sb.exec`` in ``/workspace``; kill only it on timeout, revive if dead (§2,3).

        Ensures the sandbox is live first (:meth:`_ensure_live` transparently recreates + re-bootstraps a
        remotely-ended one, then this call carries the one-shot restore ``note``). Runs ``args`` (``bash
        -lc <command>`` for the ``run`` seam) as a fresh process bounded by modal's int-second per-exec
        ``timeout`` (floored to 1 so a sub-second value is not read as "no timeout"). A normal command
        returns :class:`ExecResult` (``timed_out=False``); a timeout returns the partial output with
        ``timed_out=True`` and the killed-by-signal sentinel — only the exec died, the sandbox + its fs
        persist.

        **Two revival triggers, verified against real modal 1.5.1.** ``poll()`` reliably reports a
        max-lifetime *expiry* (returns the exit code ~1s after the deadline), so the common case — the
        sandbox expired *between* commands — is caught by :meth:`_ensure_live` before the exec. But a
        sandbox that expires *during* the narrow window between that probe and the exec makes ``sb.exec``
        raise ``modal.exception.NotFoundError`` ("… has already shut down"); this catches that, revives +
        re-bootstraps from the host state, and retries **once**. Either way the result carries the restore
        ``note``. **Never raises for an infra failure** — a revival failure renders a session-lost exit-125
        (the never-crash contract) instead of crashing the tool.
        """
        exec_timeout = max(1, int(timeout_s))
        try:
            await self._ensure_live()
            stdout, stderr, exit_code = await self._exec(
                self._sandbox, *args, workdir=_WORKSPACE, timeout=exec_timeout
            )
        except (RuntimeError, OSError) as exc:
            return self._render_lost(exc)
        except Exception as exc:
            if not _is_sandbox_gone(exc):
                raise  # not an infra death (a genuine bug) — never mask it as a rendered failure
            # The sandbox expired between the liveness probe and this exec: revive + retry ONCE.
            logger.info(
                "[sandbox] modal exec hit a shut-down sandbox; recreating + re-bootstrapping"
            )
            self._sandbox = None
            self._recreated = True
            try:
                await self._ensure_live()
                stdout, stderr, exit_code = await self._exec(
                    self._sandbox, *args, workdir=_WORKSPACE, timeout=exec_timeout
                )
            except (RuntimeError, OSError) as retry_exc:
                return self._render_lost(retry_exc)
            except Exception as retry_exc:
                if _is_sandbox_gone(retry_exc):
                    return self._render_lost(retry_exc)
                raise
        # Settle the one-shot restore note (set by a poll-detected OR an exec-error revival) after the run.
        recreated, self._recreated = self._recreated, False
        note = _SANDBOX_RECREATED_NOTE if recreated else ""
        if exit_code == _MODAL_TIMEOUT_RETURNCODE:
            logger.debug(
                "[sandbox] $ %s timed out after %ds → exec killed (sandbox survives)",
                " ".join(args),
                exec_timeout,
            )
            return ExecResult(stdout, stderr, _TIMEOUT_EXIT, timed_out=True, note=note)
        logger.debug("[sandbox] exec %s → exit=%d bytes=%d", " ".join(args), exit_code, len(stdout))
        return ExecResult(stdout, stderr, exit_code, timed_out=False, note=note)

    def _render_lost(self, exc: Exception) -> ExecResult:
        """Render a session-lost failure (exit-125 + a note) for an unreachable sandbox (never-crash)."""
        logger.warning("[sandbox] modal became unreachable; rendering infra failure: %s", exc)
        return ExecResult(
            "", str(exc), _SANDBOX_LOST_EXIT, timed_out=False, note=_SANDBOX_LOST_NOTE
        )

    # --- file ops (the SandboxFilesystem API, direct against the remote; ADR-0012 §4) -----------

    async def read_bytes(self, rel: str) -> bytes:
        """Read the bytes of the logical Workspace path ``rel`` (direct from the remote fs).

        Normalizes modal's ``SandboxFilesystemNotFoundError`` (which does **not** subclass
        ``FileNotFoundError`` — verified on modal 1.5.1) to :class:`FileNotFoundError`, so the shared
        file-tool layer (081) catches the same exception it catches for docker's pathlib read. A dead
        sandbox (a distinct ``NotFoundError``) is revived by :meth:`_run_file_op`, not caught here.
        """
        modal = _load_modal()
        remote = self._remote(rel)
        try:
            return await self._run_file_op(lambda: self._fs().read_bytes.aio(remote))
        except modal.exception.SandboxFilesystemNotFoundError as exc:
            raise FileNotFoundError(remote) from exc

    async def write_bytes(self, rel: str, data: bytes) -> None:
        """Write ``data`` to the logical Workspace path ``rel`` (parents created by modal)."""
        # modal's signature is ``write_bytes(data, remote_path)`` — data first (the reversed order is a trap).
        remote = self._remote(rel)
        await self._run_file_op(lambda: self._fs().write_bytes.aio(data, remote))

    async def make_directory(self, rel: str) -> None:
        """Create the logical Workspace directory ``rel`` (parents included, idempotent)."""
        remote = self._remote(rel)
        await self._run_file_op(lambda: self._fs().make_directory.aio(remote, create_parents=True))

    async def stat(self, rel: str) -> FileStat | None:
        """Return the :class:`FileStat` for ``rel``, or ``None`` if absent (DockerBackend parity).

        modal's ``SandboxFilesystemNotFoundError`` / ``SandboxFilesystemNotADirectoryError`` map to
        ``None`` (mirroring docker's ``FileNotFoundError`` / ``NotADirectoryError`` catch). ``ponytail:`` a
        symlink is reported as modal's distinct ``SYMLINK`` type, so ``is_dir`` is ``False`` for a
        symlink-to-a-dir — unlike docker's ``Path.is_dir`` which follows the link (rare in a repo clone).
        A dead sandbox is revived by :meth:`_run_file_op` (the missing-path ``None`` is a live-fs answer).
        """
        modal = _load_modal()
        not_found = (
            modal.exception.SandboxFilesystemNotFoundError,
            modal.exception.SandboxFilesystemNotADirectoryError,
        )
        remote = self._remote(rel)
        try:
            info = await self._run_file_op(lambda: self._fs().stat.aio(remote))
        except not_found:
            return None
        return FileStat(path=rel, is_dir=_is_dir(info), size=info.size)

    async def list_dir(self, rel: str) -> list[FileStat]:
        """List ``rel``'s entries as :class:`FileStat`s (logical paths, sorted by name).

        A missing directory is normalized to :class:`FileNotFoundError` (modal's
        ``SandboxFilesystemNotFoundError`` does **not** subclass it — verified on modal 1.5.1), matching
        :meth:`~decode.sandbox.docker_backend.DockerBackend.list_dir` (whose pathlib ``iterdir`` raises it)
        so the shared file layer (081) meets ONE contract across backends. A dead sandbox is revived by
        :meth:`_run_file_op`.
        """
        modal = _load_modal()
        remote = self._remote(rel)
        try:
            infos = await self._run_file_op(lambda: self._fs().list_files.aio(remote))
        except modal.exception.SandboxFilesystemNotFoundError as exc:
            raise FileNotFoundError(remote) from exc
        prefix = PurePosixPath(rel) if rel else None
        entries: list[FileStat] = []
        for info in sorted(infos, key=lambda i: i.name):
            child_rel = str(prefix / info.name) if prefix is not None else info.name
            entries.append(FileStat(path=child_rel, is_dir=_is_dir(info), size=info.size))
        return entries

    async def remove(self, rel: str) -> None:
        """Remove the logical Workspace path ``rel`` — a file or a directory tree (missing-ok)."""
        modal = _load_modal()
        remote = self._remote(rel)
        try:
            await self._run_file_op(lambda: self._fs().remove.aio(remote, recursive=True))
        except modal.exception.SandboxFilesystemNotFoundError:
            # Docker's ``unlink(missing_ok=True)`` / ``rmtree`` tolerate an already-gone path; match it.
            return

    # --- internals ------------------------------------------------------------------------------

    def _fs(self) -> Any:
        """The live sandbox's ``SandboxFilesystem``; raise if no sandbox was created (docker parity)."""
        if self._sandbox is None:
            raise RuntimeError("ModalBackend file ops require a created sandbox")
        return self._sandbox.filesystem

    def _remote(self, rel: str) -> str:
        """Resolve a logical Workspace path ``rel`` to its absolute remote path under ``/workspace``.

        Containment (no ``..`` escape) is the caller's job above the seam (081); this only joins the
        (already validated) logical path onto ``/workspace``. The SandboxFilesystem API requires an
        absolute remote path, so ``rel==""`` maps to ``/workspace`` itself.
        """
        return str(PurePosixPath(_WORKSPACE) / rel) if rel else _WORKSPACE

    async def _run_file_op(self, op: Callable[[], Awaitable[_T]]) -> _T:
        """Run one SandboxFilesystem ``op``, reviving a dead remote sandbox ONCE (ADR-0012 §3,4).

        The file-op analogue of :meth:`exec`'s ``NotFoundError`` backstop. File ops go **direct** against
        the remote (:meth:`_fs`), so a max-lifetime-expired / terminated sandbox makes ``op`` raise
        ``modal.exception.NotFoundError`` — a raw ``GRPCError``, **not** ``FileNotFoundError`` / ``OSError``
        (verified on modal 1.5.1) — exactly as ``sb.exec`` does. Without this a file tool called right
        after an expiry would crash the turn with that raw type while ``bash`` self-healed. So this gives
        the file path the SAME revival ``exec`` has: on a sandbox-gone error it drops the dead handle,
        recreates + re-bootstraps from the host state (:meth:`_ensure_live`), and retries ``op`` **once**.
        A second death (or a re-bootstrap onto a still-dead sandbox) surfaces a **clean**
        :class:`RuntimeError` — never a raw modal / GRPC type — so 081's shared file layer meets one
        normalized contract, with no spin.

        **The restore note rides the NEXT ``exec``.** A file op returns bytes / a :class:`FileStat` /
        ``None`` — it has no ``note`` channel — so a file-op-triggered revival only sets the one-shot
        ``self._recreated`` flag; the next :meth:`exec` pops it into its result ``note`` (the same flag
        exec's own revival sets), so the model still learns the Workspace was restored.

        A **missing-FILE** error (``SandboxFilesystemNotFoundError``) is NOT a sandbox death — a distinct
        modal class that does not subclass ``NotFoundError`` (verified) — so it (and any genuine non-modal
        bug) propagates untouched to each op's own missing-path normalization.
        """
        try:
            return await op()
        except Exception as exc:
            if not _is_sandbox_gone(exc):
                raise  # a missing FILE / no-sandbox RuntimeError / a genuine bug — not this seam's concern
            logger.info(
                "[sandbox] modal file op hit a shut-down sandbox; recreating + re-bootstrapping"
            )
        # The remote sandbox is gone: drop the dead handle, revive from the host state, retry ``op`` ONCE.
        # The one-shot flag makes the restore note ride the next exec (file ops carry no note of their own).
        self._sandbox = None
        self._recreated = True
        try:
            await self._ensure_live()
            return await op()
        except Exception as retry_exc:
            if _is_sandbox_gone(retry_exc):
                # A second death (or a re-bootstrap onto a still-dead sandbox): a CLEAN RuntimeError, never
                # a raw modal / GRPC type — 081's shared layer sees one contract, no spin.
                raise RuntimeError(_SANDBOX_LOST_NOTE) from retry_exc
            raise

    async def _ensure_live(self) -> None:
        """Return with a live sandbox: reuse the cached one, or recreate a remotely-ended one (§3 revival).

        Probes ``poll()`` (``None`` while running; it reliably returns the exit code ~1s after a
        **max-lifetime expiry** — verified on modal 1.5.1). A dead cached handle is dropped and a fresh
        sandbox created + re-bootstrapped from the host state; the recreate flag makes the next
        :meth:`exec` carry the restore ``note``. A create failure propagates (:meth:`exec` renders it as
        session-lost). The narrower "died between this probe and the exec" race is caught by :meth:`exec`'s
        ``NotFoundError`` backstop, not here.
        """
        if self._sandbox is not None:
            if await self._sandbox.poll.aio() is None:
                return
            logger.info(
                "[sandbox] modal sandbox %s ended remotely; recreating + re-bootstrapping",
                self._sandbox.object_id,
            )
            self._sandbox = None
            self._recreated = True
        await self._spawn_and_bootstrap()

    async def _spawn_and_bootstrap(self) -> None:
        """Look up the App, create the sandbox, ``mkdir /workspace``, then bootstrap-upload the host tree.

        The shared create core for both :meth:`create` (first spawn) and :meth:`_ensure_live` (revival).
        On a bootstrap failure the just-created sandbox is reaped and the error re-raised, so a failed
        spawn never leaves ``self._sandbox`` set to a half-built handle nor leaks a remote sandbox.
        """
        modal = _load_modal()
        app = await modal.App.lookup.aio(_APP_NAME, create_if_missing=True)
        image = modal.Image.from_registry(settings.sandbox_image)
        # An explicit long-lived entrypoint (the docker keeper's exact shape): without it modal runs the
        # image's own CMD — the astral uv default (``Cmd=[uv]``) prints help and quits, taking the sandbox
        # with it. NO ``add_local_dir`` — skills ride the bootstrap upload host-side now (ADR-0012 §5).
        sandbox = await modal.Sandbox.create.aio(
            "sleep", "infinity", app=app, image=image, timeout=int(settings.sandbox_timeout_s)
        )
        logger.info("[sandbox] modal create %s image=%s", sandbox.object_id, settings.sandbox_image)
        try:
            _, mkderr, mkcode = await self._exec(sandbox, "mkdir", "-p", _WORKSPACE, timeout=None)
            if mkcode != 0:
                raise RuntimeError(
                    f"modal /workspace create failed (exit {mkcode}): {mkderr.strip()}"
                )
            await self._bootstrap_upload(sandbox)
        except Exception:
            with contextlib.suppress(Exception):
                await sandbox.terminate.aio()  # no leaked half-built sandbox
            raise
        self._sandbox = sandbox

    async def _bootstrap_upload(self, sandbox: Any) -> None:
        """Upload the host Workspace into ``/workspace`` as ONE tar (write_bytes + a remote ``tar -x``).

        Packs the whole host Workspace (:func:`~decode.sandbox.workspace.tar_dir` — the cloned repo + the
        host-seeded ``.decode/skills``) into an in-memory tar, ``write_bytes`` it to :data:`_BOOTSTRAP_TAR`
        (modal chunks the whole payload through stdin internally), then unpacks it with a single remote
        ``tar -x`` into ``/workspace``, cleaning the staging tar up. A no-op when the host Workspace is
        absent/empty (a direct/test caller); a non-zero ``tar -x`` raises :class:`RuntimeError`.
        """
        workspace = self._workspace
        if workspace is None or not workspace.is_dir():
            return
        data = tar_dir(workspace)
        await sandbox.filesystem.write_bytes.aio(data, _BOOTSTRAP_TAR)
        _, stderr, code = await self._exec(
            sandbox,
            "bash",
            "-lc",
            f"tar -xpf {_BOOTSTRAP_TAR} -C {_WORKSPACE} && rm -f {_BOOTSTRAP_TAR}",
            timeout=None,
        )
        if code != 0:
            raise RuntimeError(
                f"modal workspace bootstrap failed (tar exit {code}): {stderr.strip()}"
            )
        logger.info("[sandbox] modal bootstrap uploaded %s → %s", workspace, _WORKSPACE)

    @staticmethod
    async def _exec(
        sandbox: Any, *args: str, workdir: str | None = None, timeout: int | None = None
    ) -> tuple[str, str, int]:
        """Exec one process, drain both streams concurrently, then read the exit code (§3).

        Draining stdout and stderr **while the process runs** (never after ``wait``) is what stops a
        high-output command from deadlocking on an undrained pipe. Runs with ``text=False`` so modal
        yields **raw bytes** — decoded here with :func:`_decode` (``errors="replace"``), which upholds the
        :class:`ExecResult` contract that undecodable output is replaced, never crashes (modal's
        ``text=True`` reader decodes *strict* UTF-8 and raises on the first invalid byte). Both the normal
        and timeout branches route through here, so partial timeout output is decoded with replace too.
        """
        proc = await sandbox.exec.aio(*args, workdir=workdir, timeout=timeout, text=False)
        stdout, stderr = await asyncio.gather(proc.stdout.read.aio(), proc.stderr.read.aio())
        exit_code = await proc.wait.aio()
        return _decode(stdout), _decode(stderr), exit_code


def _is_sandbox_gone(exc: Exception) -> bool:
    """Whether a modal exception signals the remote sandbox is gone (a ``NotFoundError`` on its exec).

    modal raises ``modal.exception.NotFoundError`` ("Modal Sandbox … not found … has already shut down")
    when a command is exec'd against a sandbox that expired since the last ``poll()`` liveness probe — the
    mid-command race the :meth:`ModalBackend.exec` revival backstop catches (verified against modal 1.5.1).
    Referenced through the lazily-loaded module so the ``none`` / ``docker`` laziness stays intact.
    """
    modal = _load_modal()
    return isinstance(exc, modal.exception.NotFoundError)


def _is_dir(info: Any) -> bool:
    """Whether a modal ``FileInfo`` names a directory — read via the ``FileType`` enum's ``.value``.

    modal's ``FileInfo.type`` is a ``FileType`` enum whose ``.value`` is ``"file"`` / ``"directory"`` /
    ``"symlink"`` (verified on modal 1.5.1). Reading ``.value`` avoids importing the enum (keeping the
    lazy-modal seam intact) and treats only a true directory as one — a symlink is its own type.
    """
    return info.type.value == "directory"


def _decode(raw: bytes) -> str:
    """Decode captured stream bytes as UTF-8, replacing undecodable bytes (never crash).

    Mirrors :func:`decode.tools.exec._decode` and :func:`decode.sandbox.docker_backend._decode` — the
    shared replace-don't-crash discipline every :class:`~decode.tools.exec.CommandExecutor` upholds.
    """
    return raw.decode("utf-8", errors="replace")
