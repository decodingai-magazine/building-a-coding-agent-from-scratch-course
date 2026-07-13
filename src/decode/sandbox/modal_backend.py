"""The Modal sandbox backend — fresh ``sb.exec`` + direct SandboxFilesystem file ops (ADR-0012 §2-5,10).

One session-persistent ``modal.Sandbox`` behind the :class:`~decode.sandbox.executor.SandboxBackend`
seam. **Fresh-exec**: each command is a brand-new process (``cd`` / ``export`` reset per call; the
filesystem persists). File ops go **directly against the remote SandboxFilesystem** — no host mirror —
and ``glob`` / ``grep`` run as remote commands via :meth:`ModalBackend.exec`. The only bulk transfers
are ONE tar **bootstrap upload** of the host Workspace at create and ONE tar **export sweep** down at
session end. A dead sandbox (max-lifetime expiry) is revived + re-bootstrapped from the last local
state — in-sandbox changes since the last export may be lost. ``modal`` is imported lazily
(:func:`_load_modal`); no modal type leaks past this module — callers see only
:class:`~decode.tools.exec.ExecResult` and :class:`~decode.sandbox.executor.FileStat`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from decode.config.settings import settings
from decode.sandbox.executor import FileStat
from decode.sandbox.workspace import extract_tar, git_config_pairs, tar_dir
from decode.tools.exec import ExecResult

logger = logging.getLogger(__name__)

# Return type of a wrapped file op, keeping :meth:`ModalBackend._run_file_op` type-preserving.
_T = TypeVar("_T")

# The remote Workspace: workdir of every command, base of every file op (≡ the host workspace dir).
_WORKSPACE = "/workspace"

# The modal App the sandbox is looked up / created under (``create_if_missing=True`` on first use).
_APP_NAME = "decode-sandbox"

# Baked into the image when ``SANDBOX_GIT_TOKEN`` is set (modal only, ADR-0012 §10): echoes the runtime
# ``$GITHUB_TOKEN`` (from the ``modal.Secret``) as the HTTPS password. Single-quoted so the token is
# read only at push time — never expanded into the cached image layer.
_GIT_CREDENTIAL_HELPER = (
    "git config --global credential.helper "
    "'!f() { echo username=x-access-token; echo \"password=$GITHUB_TOKEN\"; }; f'"
)

# ``gh`` is not in Debian bookworm — it comes from GitHub's own apt repo. Baked as a cached image
# layer (unlike docker, which installs it per session). ``gh`` authenticates off the ``GITHUB_TOKEN``
# the ``modal.Secret`` injects at runtime, so no decoy token is needed here (ADR-0012 §10).
_GH_INSTALL_CMD = (
    "mkdir -p -m 755 /etc/apt/keyrings && "
    "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg "
    "-o /etc/apt/keyrings/githubcli-archive-keyring.gpg && "
    "chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && "
    'echo "deb [arch=$(dpkg --print-architecture) '
    "signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] "
    'https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list && '
    "apt-get update && apt-get install -y --no-install-recommends gh"
)

# Remote ``/tmp`` staging tars for the bootstrap upload / export sweep — absolute (the SandboxFilesystem
# API requires it) and OUTSIDE ``/workspace`` so neither is swept into itself.
_BOOTSTRAP_TAR = "/tmp/decode-bootstrap.tar"
_EXPORT_TAR = "/tmp/decode-export.tar"

# modal returns ``-1`` from ``ContainerProcess.wait()`` on a per-exec timeout; normalized to the
# killed-by-signal sentinel every executor uses on timeout.
_MODAL_TIMEOUT_RETURNCODE = -1
_TIMEOUT_EXIT = -signal.SIGKILL

# Rendered when the sandbox could not be brought back: the never-crash contract — a ``bash`` call gets
# a rendered failure (125 = the generic "sandbox unavailable" code), never an exception.
_SANDBOX_LOST_EXIT = 125
_SANDBOX_LOST_NOTE = "The modal sandbox became unreachable — the session was lost."

# One-shot ``note`` after a dead sandbox was recreated + re-bootstrapped from the host state (remote-only
# changes since the last export may be lost — the ADR-0012 revival ceiling).
_SANDBOX_RECREATED_NOTE = (
    "The remote sandbox's lifetime expired; a fresh one was created and its workspace was restored "
    "from the last local state on the host. Any changes made inside the sandbox since the last export "
    "may be lost."
)


def _load_modal() -> Any:
    """Import the ``modal`` SDK lazily so non-modal paths never pull it in; tests patch this seam."""
    import modal

    return modal


class ModalBackend:
    """Run commands + file ops in one session-persistent remote ``modal.Sandbox`` (ADR-0012 §2,4,5).

    Construction is **inert** — no lookup, no create, no modal import: the sandbox is created on
    :meth:`create` (lazily on the first ``run`` or eagerly via ``start``). Not safe for concurrent
    :meth:`exec` calls on one instance.
    """

    def __init__(self) -> None:
        # The live remote sandbox; ``Any`` so no modal type leaks into this module's annotations.
        self._sandbox: Any = None
        # The resolved host Workspace root — bootstrap-upload source and export-sweep target.
        self._workspace: Path | None = None
        # One-shot revival flag; the next :meth:`exec` pops it into the result ``note``.
        self._recreated = False

    # --- lifecycle ------------------------------------------------------------------------------

    async def create(self, workspace: Path) -> None:
        """Bring the remote sandbox up + bootstrap-upload ``workspace`` into ``/workspace``.

        Idempotent when a sandbox is already live. A modal / bootstrap failure raises (the executor
        renders it; the warm-up call site degrades to lazy) and the half-built sandbox is reaped.
        """
        self._workspace = workspace.resolve()
        if self._sandbox is not None:
            return
        await self._spawn_and_bootstrap()

    async def export(self) -> None:
        """Sweep ``/workspace`` back to the host Workspace, leaving the sandbox alive (ADR-0012 §5,8).

        Remote ``tar -c`` → ``read_bytes`` down → host-side extract. **Standalone** — it does NOT
        :meth:`destroy` (``/ship`` continues the session). Best-effort: a sweep failure logs and
        returns, never blocking teardown.
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
        """Terminate the session sandbox — idempotent, best-effort, loop-independent.

        ``terminate.aio()`` through ``synchronicity`` reaps from any caller loop; a failure is
        swallowed (the modal ``timeout`` is the crash backstop).
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
        """Run one fresh ``sb.exec`` in ``/workspace``; kill only it on timeout, revive if dead.

        Fresh process per call: filesystem changes persist, ``cd`` / ``export`` do not. A timeout
        returns the partial output with ``timed_out=True`` — only the exec died; the sandbox and its
        fs survive. Two revival triggers: :meth:`_ensure_live`'s ``poll()`` probe catches an expiry
        between commands; an expiry between that probe and the exec raises modal ``NotFoundError``,
        caught here to revive + re-bootstrap from the host state and retry ONCE. Either way the result
        carries the restore ``note``. **Never raises for an infra failure** — renders exit-125.
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

        modal's ``SandboxFilesystemNotFoundError`` does **not** subclass ``FileNotFoundError``, so it
        is normalized to one (docker parity). A dead sandbox is revived by :meth:`_run_file_op`.
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

        ``ponytail:`` a symlink is reported as modal's distinct ``SYMLINK`` type, so ``is_dir`` is
        ``False`` for a symlink-to-a-dir — unlike docker's ``Path.is_dir`` which follows the link.
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

        A missing directory is normalized to :class:`FileNotFoundError` (modal's error does not
        subclass it), matching :meth:`~decode.sandbox.docker_backend.DockerBackend.list_dir`.
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
        """Absolute remote path under ``/workspace`` for the (already validated) logical ``rel``.

        Containment is the caller's job above the seam; ``rel==""`` maps to ``/workspace`` itself.
        """
        return str(PurePosixPath(_WORKSPACE) / rel) if rel else _WORKSPACE

    async def _run_file_op(self, op: Callable[[], Awaitable[_T]]) -> _T:
        """Run one SandboxFilesystem ``op``, reviving a dead remote sandbox ONCE.

        A dead sandbox makes ``op`` raise modal ``NotFoundError`` (a raw GRPC type, not an
        ``OSError``): drop the handle, recreate + re-bootstrap from the host state, retry ``op`` once.
        A second death surfaces a clean :class:`RuntimeError` — never a raw modal / GRPC type. A file
        op has no ``note`` channel, so a revival here only sets the one-shot flag and the restore note
        rides the NEXT :meth:`exec`. A missing-FILE error is NOT a sandbox death; it propagates
        untouched to each op's own normalization.
        """
        try:
            return await op()
        except Exception as exc:
            if not _is_sandbox_gone(exc):
                raise  # a missing FILE / no-sandbox RuntimeError / a genuine bug — not this seam's concern
            logger.info(
                "[sandbox] modal file op hit a shut-down sandbox; recreating + re-bootstrapping"
            )
        self._sandbox = None
        self._recreated = True
        try:
            await self._ensure_live()
            return await op()
        except Exception as retry_exc:
            if _is_sandbox_gone(retry_exc):
                raise RuntimeError(_SANDBOX_LOST_NOTE) from retry_exc
            raise

    async def _ensure_live(self) -> None:
        """Return with a live sandbox: reuse the cached one, or recreate a remotely-ended one.

        ``poll()`` is ``None`` while running; a dead cached handle is dropped and a fresh sandbox
        created + re-bootstrapped from the host state (the flag makes the next :meth:`exec` carry the
        restore ``note``). The probe-to-exec death race is caught by :meth:`exec`, not here.
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

        Shared by :meth:`create` and :meth:`_ensure_live` (revival). On a bootstrap failure the
        just-created sandbox is reaped and the error re-raised — never a half-built handle or a leak.
        """
        modal = _load_modal()
        app = await modal.App.lookup.aio(_APP_NAME, create_if_missing=True)
        # The slim base ships no git and no gh — bake both into the image (cached layers, no
        # per-session cost). gh rides along because a model that pushes a branch is asked, in the same
        # breath, to open the PR; without it the turn dies on ``gh: command not found`` (ADR-0012 §10).
        # gh is not in Debian bookworm, so it comes from GitHub's own apt repo. Unlike docker, modal
        # needs no decoy token: the real ``GITHUB_TOKEN`` is already in this sandbox's env (below) and
        # gh reads it natively.
        image = modal.Image.from_registry(settings.sandbox_image).apt_install(
            "git", "curl", "ca-certificates"
        )
        image = image.run_commands(_GH_INSTALL_CMD)
        # Bake the ``SANDBOX_GIT_USER_*`` identity into a cached layer so a model ``git commit`` works.
        for key, value in git_config_pairs():
            image = image.run_commands(f"git config --global {key} {shlex.quote(value)}")
        # Direct credential injection — MODAL ONLY (docker keeps the Credential Proxy; ADR-0012 §10):
        # ``SANDBOX_GIT_TOKEN`` rides a ``modal.Secret`` as ``GITHUB_TOKEN`` + a credential-helper layer
        # that reads it at push time, so the token never lands in a cached image layer.
        secrets: list[Any] = []
        token = (
            settings.sandbox_git_token.get_secret_value()
            if settings.sandbox_git_token is not None
            else ""
        )
        if token:
            image = image.run_commands(_GIT_CREDENTIAL_HELPER)
            secrets = [modal.Secret.from_dict({"GITHUB_TOKEN": token})]
        # Explicit long-lived entrypoint: without it modal runs the image's own CMD (the astral uv
        # default prints help and quits, taking the sandbox with it).
        sandbox = await modal.Sandbox.create.aio(
            "sleep",
            "infinity",
            app=app,
            image=image,
            timeout=int(settings.sandbox_timeout_s),
            secrets=secrets,
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

        A no-op when the host Workspace is absent/empty; a non-zero ``tar -x`` raises. ``ponytail:`` a
        whole-tree tar — a huge repo pays a proportional upload; a content-addressed / git-diff
        transport is the documented upgrade path (ADR-0012 Future work).
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
        """Exec one process, drain both streams concurrently, then read the exit code.

        Draining **while the process runs** stops a high-output command deadlocking on an undrained
        pipe. ``text=False`` + :func:`_decode` (``errors="replace"``) so undecodable output — partial
        timeout output included — never crashes (modal's ``text=True`` reader is strict UTF-8).
        """
        proc = await sandbox.exec.aio(*args, workdir=workdir, timeout=timeout, text=False)
        stdout, stderr = await asyncio.gather(proc.stdout.read.aio(), proc.stderr.read.aio())
        exit_code = await proc.wait.aio()
        return _decode(stdout), _decode(stderr), exit_code


def _is_sandbox_gone(exc: Exception) -> bool:
    """Whether ``exc`` is modal's ``NotFoundError`` — the remote sandbox is gone (expired / terminated).

    Referenced through the lazily-loaded module so the ``none`` / ``docker`` laziness stays intact.
    """
    modal = _load_modal()
    return isinstance(exc, modal.exception.NotFoundError)


def _is_dir(info: Any) -> bool:
    """Whether a modal ``FileInfo`` names a directory — read via ``.value`` so the ``FileType`` enum
    is never imported (lazy-modal seam); a symlink is its own type, not a directory."""
    return info.type.value == "directory"


def _decode(raw: bytes) -> str:
    """Decode captured stream bytes as UTF-8, replacing undecodable bytes (never crash)."""
    return raw.decode("utf-8", errors="replace")
