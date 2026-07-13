"""The Docker sandbox backend — fresh ``docker exec`` per call + pathlib file ops (ADR-0012 §2,4).

One keeper container per session (``docker run -d --rm -v <workspace>:/workspace … sleep infinity``).
**Fresh-exec**: each command is a new ``docker exec`` (``cd`` / ``export`` do not persist; the
filesystem does); on timeout only the exec client dies — the container and its filesystem survive.
File ops are plain :mod:`pathlib` on the **live bind mount** (always truthful, zero remote plumbing),
so ``export`` is a no-op. A set ``SANDBOX_GIT_TOKEN`` is **direct-injected** into the worker env as
``GITHUB_TOKEN`` (+ git's credential helper) — the same mechanism modal uses (ADR-0016 §2); unset,
the ``docker run`` is byte-identical to the no-token case. Docker is driven via the CLI (no SDK)
with fresh subprocesses per call, so teardown is loop-independent. No docker type leaks past this
module: callers see only :class:`~decode.tools.exec.ExecResult` and
:class:`~decode.sandbox.executor.FileStat`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import shutil
import signal
from pathlib import Path, PurePosixPath

from decode.config.settings import settings
from decode.sandbox.executor import FileStat, WorkspaceEscape
from decode.sandbox.workspace import (
    GIT_CREDENTIAL_HELPER,
    GIT_TOKEN_ENV,
    git_config_pairs,
    sandbox_git_token,
)
from decode.tools.exec import ExecResult

logger = logging.getLogger(__name__)

# The container-side Workspace: the bind-mount target and every command's working directory.
_WORKSPACE = "/workspace"

# Grace between SIGTERM and the SIGKILL escalation for a timed-out ``docker exec`` client.
_KILL_GRACE_S = 2.0

# Killed-by-signal sentinel for a timeout (mirrors ``LocalExecutor``).
_TIMEOUT_EXIT = -signal.SIGKILL

# Rendered when ``docker exec`` cannot even be spawned (the CLI itself is gone): a ``bash`` call
# surfaces a failure (125 = docker's "failed to run" convention), never a crash.
_DAEMON_LOST_EXIT = 125
_DAEMON_LOST_NOTE = "The docker sandbox became unreachable — the session was lost."

# The slim uv base ships no git and no gh; both are installed once per session container (see
# ``_install_git``). ``curl`` + ``ca-certificates`` are pulled in because the gh install needs them.
_GIT_INSTALL_CMD = (
    "apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates"
)
# ``gh`` is not in Debian bookworm — it comes from GitHub's own apt repo (keyring + source list).
# It authenticates off the ``GITHUB_TOKEN`` in the worker env when one is injected (ADR-0016 §2).
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
# Bound (seconds) for that install — ~20s for git+gh on a warm network; only caps a wedged/offline apt.
_GIT_INSTALL_TIMEOUT_S = 180.0


class DockerBackend:
    """Run commands + file ops in one session container, fresh-exec (ADR-0012 §2,4).

    Construction is **inert** — the keeper container starts on :meth:`create` (lazily on the first
    ``run`` or eagerly via ``start``). Not safe for concurrent :meth:`exec` calls on one instance.
    """

    def __init__(self) -> None:
        self._container_id: str | None = None
        # The resolved host Workspace bind-mounted at ``/workspace``; the base of every file op.
        self._workspace: Path | None = None

    # --- lifecycle ------------------------------------------------------------------------------

    async def create(self, workspace: Path) -> None:
        """Start the keeper container, bind-mounting ``workspace`` at ``/workspace``.

        Idempotent. A failed ``docker run`` raises :class:`RuntimeError` (the executor renders it; the
        warm-up call site degrades to lazy). A set ``SANDBOX_GIT_TOKEN`` is handed to the container
        through the docker **client's own env** (:func:`_run_env`), never through the argv.
        """
        if self._container_id is not None:
            return
        workspace = workspace.resolve()
        # Pre-create the mount source host-side so docker does not materialize it as a root-owned dir.
        workspace.mkdir(parents=True, exist_ok=True)
        self._workspace = workspace
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *self._run_args(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_run_env(),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            # The startup guard already proved the daemon reachable — a failure here is a bad image / mount.
            raise RuntimeError(
                f"docker run failed (exit {proc.returncode}): {_decode(stderr).strip()}"
            )
        self._container_id = _decode(stdout).strip()
        logger.info(
            "[sandbox] docker start %s image=%s", self._container_id, settings.sandbox_image
        )
        # Install git + gh in EVERY worker (the slim base ships none), and — when a token was
        # injected — git's credential helper alongside. Best-effort: a failed install leaves the
        # session up with no git.
        await self._install_git(self._container_id)

    async def export(self) -> None:
        """No-op: the bind mount is live, so the host Workspace already IS the sandbox filesystem (§5)."""
        return None

    async def destroy(self) -> None:
        """Force-remove the session container — ``docker rm -f`` (loop-free, idempotent, best-effort).

        A *fresh* subprocess needs no old event loop, so it reaps correctly from the headless reaper's
        fresh loop; ``--rm`` is the crash backstop and a "no such container" race is swallowed.
        """
        container_id, self._container_id = self._container_id, None
        self._workspace = None
        if container_id is None:
            return
        logger.info("[sandbox] docker stop %s", container_id)
        await _run_docker_quiet("rm", "-f", container_id)

    # --- command exec ---------------------------------------------------------------------------

    async def exec(self, *args: str, timeout_s: float) -> ExecResult:
        """Run one fresh ``docker exec -w /workspace <id> <args>``; kill only it on timeout.

        Separate stdout/stderr (no merge). On timeout the ``docker exec`` **client** process group is
        killed (SIGTERM→SIGKILL) — the container and its filesystem survive — and the partial output
        returns with ``timed_out=True``. ``ponytail:`` killing the client does not surgically stop the
        in-container process; it is reaped when the container is destroyed (a per-command cgroup is
        the upgrade path). A spawn :class:`OSError` renders the exit-125 failure (never-crash).
        """
        container_id = self._container_id
        if container_id is None:
            # Defensive: exec is normally reached only after a successful create; render, never crash.
            return ExecResult(
                "",
                "the docker sandbox container is not running",
                _DAEMON_LOST_EXIT,
                timed_out=False,
                note=_DAEMON_LOST_NOTE,
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                "-w",
                _WORKSPACE,
                container_id,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # own process group → kill it as a unit on timeout, no orphans
            )
        except OSError as exc:
            logger.warning("[sandbox] docker exec could not spawn: %s", exc)
            return ExecResult(
                "", str(exc), _DAEMON_LOST_EXIT, timed_out=False, note=_DAEMON_LOST_NOTE
            )

        # Run communicate() as a task we never cancel — cancelling would discard the partial output the
        # child flushed before a timeout (the same discipline as ``LocalExecutor``).
        comm = asyncio.ensure_future(proc.communicate())
        done, _ = await asyncio.wait({comm}, timeout=timeout_s)
        if not done:
            stdout, stderr = await self._terminate(proc, comm)
            logger.debug(
                "[sandbox] $ %s timed out after %gs → exec killed (container survives)",
                args,
                timeout_s,
            )
            return ExecResult(
                _decode(stdout), _decode(stderr), _TIMEOUT_EXIT, timed_out=True, note=""
            )
        stdout, stderr = await comm
        exit_code = proc.returncode if proc.returncode is not None else 0
        logger.debug("[sandbox] exec %s → exit=%d bytes=%d", " ".join(args), exit_code, len(stdout))
        return ExecResult(_decode(stdout), _decode(stderr), exit_code, timed_out=False, note="")

    @staticmethod
    async def _terminate(
        proc: asyncio.subprocess.Process, comm: asyncio.Future[tuple[bytes, bytes]]
    ) -> tuple[bytes, bytes]:
        """Kill the timed-out ``docker exec`` client's process group and drain its partial output.

        SIGTERM, then SIGKILL after :data:`_KILL_GRACE_S`; ``await`` the already-running
        ``communicate`` task (a fresh one would return empty). Mirrors ``LocalExecutor._terminate``.
        """
        _signal_group(proc, signal.SIGTERM)
        done, _ = await asyncio.wait({comm}, timeout=_KILL_GRACE_S)
        if not done:
            _signal_group(proc, signal.SIGKILL)
        return await comm

    # --- file ops (pathlib on the bind mount; ADR-0012 §4) --------------------------------------

    async def read_bytes(self, rel: str) -> bytes:
        """Read the bytes of the logical Workspace path ``rel`` (straight off the mount)."""
        return self._path(rel).read_bytes()

    async def write_bytes(self, rel: str, data: bytes) -> None:
        """Write ``data`` to the logical Workspace path ``rel``, creating parent directories."""
        path = self._path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def make_directory(self, rel: str) -> None:
        """Create the logical Workspace directory ``rel`` (parents included, idempotent)."""
        self._path(rel).mkdir(parents=True, exist_ok=True)

    async def stat(self, rel: str) -> FileStat | None:
        """Return the :class:`~decode.sandbox.executor.FileStat` for ``rel``, or ``None`` if absent."""
        path = self._path(rel)
        try:
            st = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        return FileStat(path=rel, is_dir=path.is_dir(), size=st.st_size)

    async def list_dir(self, rel: str) -> list[FileStat]:
        """List ``rel``'s entries as :class:`~decode.sandbox.executor.FileStat`s (logical paths, sorted)."""
        base = self._path(rel)
        prefix = PurePosixPath(rel) if rel else None
        entries: list[FileStat] = []
        for child in sorted(base.iterdir(), key=lambda p: p.name):
            st = child.stat()
            child_rel = str(prefix / child.name) if prefix is not None else child.name
            entries.append(FileStat(path=child_rel, is_dir=child.is_dir(), size=st.st_size))
        return entries

    async def remove(self, rel: str) -> None:
        """Remove the logical Workspace path ``rel`` — a file, or a directory tree (``rmtree``)."""
        path = self._path(rel)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def _path(self, rel: str) -> Path:
        """Resolve a logical Workspace path ``rel`` to its **contained** host path on the bind mount.

        String math above the seam already rejects ``..`` / absolute escapes, but cannot see a
        **symlink**: the mount is shared with the host, so a symlink planted by sandboxed ``bash``
        could be followed off the mount. Resolve physically (following symlinks) and raise
        :class:`WorkspaceEscape` if the path lands outside the Workspace root (ADR-0012 §4).
        """
        if self._workspace is None:
            raise RuntimeError("DockerBackend file ops require a created workspace")
        resolved = (self._workspace / rel).resolve()
        if resolved != self._workspace and self._workspace not in resolved.parents:
            raise WorkspaceEscape(
                f"path {rel!r} escapes the workspace sandbox (resolves outside the bind mount)"
            )
        return resolved

    # --- docker run argv ------------------------------------------------------------------------

    def _run_args(self, workspace: Path) -> list[str]:
        """Build the ``docker run`` argv for the keeper container — ONE shape (ADR-0016 §1,2).

        Mount ``workspace`` at ``/workspace`` and run ``sleep infinity``. The only variation: a set
        ``SANDBOX_GIT_TOKEN`` adds a **value-less** ``-e GITHUB_TOKEN``, which docker fills from the
        client's env (:func:`_run_env`) — so the secret never enters an argv a host ``ps`` (or a
        rendered error) could read. Unset → byte-identical to the no-token run. No skills mount —
        skills are seeded host-side into the Workspace (ADR-0012 §5).
        """
        args = ["run", "-d", "--rm", "-v", f"{workspace}:{_WORKSPACE}", "-w", _WORKSPACE]
        if sandbox_git_token():
            args += ["-e", GIT_TOKEN_ENV]
        args += [settings.sandbox_image, "sleep", "infinity"]
        return args

    async def _install_git(self, container_id: str) -> None:
        """Best-effort ``apt-get install`` of git + gh, then git config, in the fresh worker.

        One ``sh -c`` installs git, adds GitHub's apt repo and installs gh, then (``&&``-chained, only
        after a successful install) sets the ``SANDBOX_GIT_USER_*`` identity and — when a token was
        injected — git's credential helper (:func:`_git_setup_command`). ``ponytail:`` installed per
        session rather than baking a fatter image — keeps the slim base and the worker's
        ``ancestor=<image>`` identity, at the cost of a one-off ~20 s apt (bake git + gh into a custom
        ``SANDBOX_IMAGE`` to skip it). A failure logs a warning and leaves the session running without
        them, never a crash. Bounded by :data:`_GIT_INSTALL_TIMEOUT_S`.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                container_id,
                "sh",
                "-c",
                _git_setup_command(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # one stream: apt chats on both
            )
        except OSError as exc:
            logger.warning(
                "[sandbox] git+gh install could not spawn: %s (continuing without them)", exc
            )
            return
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_GIT_INSTALL_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()  # reap the killed exec (no zombie / ResourceWarning)
            logger.warning(
                "[sandbox] git+gh install timed out after %gs (continuing without them)",
                _GIT_INSTALL_TIMEOUT_S,
            )
            return
        if proc.returncode != 0:
            logger.warning(
                "[sandbox] git+gh install failed (exit %s; continuing without them): %s",
                proc.returncode,
                _decode(stdout).strip()[-500:],
            )
            return
        logger.info("[sandbox] git + gh installed in worker %s", container_id[:12])


def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    """Send ``sig`` to the ``docker exec`` client's whole process group; tolerate an already-dead one.

    ``start_new_session=True`` makes the client's PID its process-group id (mirrors ``LocalExecutor``).
    ``ponytail:`` the in-container command may outlive it — reaped when the container is destroyed.
    """
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, sig)


async def _run_docker_quiet(*args: str) -> None:
    """Run ``docker <args>`` to completion, discarding output; swallow every failure (best-effort)."""
    with contextlib.suppress(Exception):
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()  # reap the process + close its pipe transports (no leak)


def _decode(raw: bytes) -> str:
    """Decode captured subprocess bytes as UTF-8, replacing undecodable bytes (never crash)."""
    return raw.decode("utf-8", errors="replace")


def _run_env() -> dict[str, str] | None:
    """The env for the ``docker run`` **client** process — the token's only carrier (ADR-0016 §2).

    ``None`` (inherit) when no ``SANDBOX_GIT_TOKEN`` is set: the default path is untouched. When one
    is set, the value rides here and the argv only names the var (``-e GITHUB_TOKEN``), so the token
    is never visible in a process listing nor in any argv a failure could render.
    """
    token = sandbox_git_token()
    if not token:
        return None
    return {**os.environ, GIT_TOKEN_ENV: token}


def _git_setup_command() -> str:
    """The ``sh -c`` line installing git + gh and (only on success) configuring git in the worker.

    ``gh`` ships alongside git because a model that can `git push` is asked, in the same breath, to
    open the PR — and without it the turn dies on ``gh: command not found`` after the push (ADR-0012
    §10); it authenticates off the injected ``GITHUB_TOKEN`` natively. Each identity value is
    ``shlex.quote``d so a spaced / quoted name stays one safe shell token. With a token set, git's
    credential helper is chained on last (the modal image bakes the identical line into a layer) —
    it reads ``$GITHUB_TOKEN`` from the worker env at push time, so no secret is in this string.
    """
    parts = [_GIT_INSTALL_CMD, _GH_INSTALL_CMD]
    parts += [
        f"git config --global {key} {shlex.quote(value)}" for key, value in git_config_pairs()
    ]
    if sandbox_git_token():
        parts.append(GIT_CREDENTIAL_HELPER)
    return " && ".join(parts)
