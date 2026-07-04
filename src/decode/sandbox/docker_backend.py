"""The Docker sandbox backend — fresh ``docker exec`` per call + pathlib file ops (ADR-0012 §2,4).

:class:`DockerBackend` is a :class:`~decode.sandbox.executor.SandboxBackend` driven by the one
:class:`~decode.sandbox.executor.SandboxExecutor`. It replaces the ADR-0011 §2
``DockerExecutor`` — deleting its persistent bash shell, its marker/``$?`` command protocol, and its
kill-and-restart-on-timeout shell-reset machinery — with the far simpler **fresh-exec** shape ADR-0012
settles on:

* **create** — ``docker run -d --rm -v <workspace>:/workspace -w /workspace <image> sleep infinity``:
  one keeper container per session, the host Workspace (``settings.sandbox_workspace_dir``)
  bind-mounted at ``/workspace``. The Credential-Proxy wiring is kept intact (ADR-0011 §6, retained):
  an optional ``--network`` + ``proxy_env`` + a read-only CA mount, with a **synchronous**
  ``update-ca-certificates`` after create so the very first command already trusts the proxy CA. A
  default (unwired) worker then gets a best-effort ``apt-get install git`` — the slim base ships none.
* **exec** — a fresh ``docker exec -w /workspace <id> bash -lc <command>`` per call, with **separate**
  stdout/stderr (no merge, unlike the old shell), bounded by ``timeout_s``. On timeout only the one
  ``docker exec`` **client** process group is killed — the **container and its filesystem survive**
  (mirroring modal's exec-dies-sandbox-survives rule), ``timed_out=True`` with an empty ``note``.
* **file ops** — plain :mod:`pathlib` on the bind-mounted Workspace (``self._workspace / rel``). The
  mount makes the host directory *be* the sandbox filesystem, so ``read_bytes`` / ``write_bytes`` /
  ``stat`` / ``list_dir`` / ``make_directory`` / ``remove`` are always truthful with **zero** remote
  plumbing — a file written by ``bash`` is immediately visible via ``read_bytes`` and vice-versa.
* **export** — a no-op (the mount is already live); **destroy** — ``docker rm -f <id>``.

**Docker CLI, not the SDK (ADR-0011 Alternatives, retained).** Every docker interaction is the
standard ``docker`` CLI shelled out with :mod:`asyncio` subprocesses — dependency-free, mirroring
:class:`~decode.tools.exec.LocalExecutor`, and making gVisor / Kata zero-code daemon-config upgrades.
No docker type ever leaks past this module: callers see only :class:`~decode.tools.exec.ExecResult`
and :class:`~decode.sandbox.executor.FileStat`.

**Loop-independence, for free.** Fresh-exec holds **no** subprocess or pipe transport across ``run``
calls — each ``docker exec`` is spawned and fully reaped inside one :meth:`exec`, on that call's own
loop. So :meth:`destroy` (``docker rm -f``, a fresh subprocess) needs no old event loop and reaps
correctly from the headless reaper's fresh loop — retiring the ADR-0011 §4 loop-free shell-teardown
helpers (the whole family that tore a loop-bound persistent shell down from a foreign loop) entirely.
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
from decode.sandbox.workspace import git_config_pairs
from decode.tools.exec import ExecResult

logger = logging.getLogger(__name__)

# The container-side Workspace: the bind-mount target and every command's working directory.
_WORKSPACE = "/workspace"

# Where the Credential Proxy's mitmproxy CA is bind-mounted inside the worker (ADR-0011 §6, retained).
# A ``.crt`` under ``/usr/local/share/ca-certificates`` is exactly what ``update-ca-certificates`` folds
# into the system trust store, so an outbound HTTPS request through the proxy validates. Proxy path only.
_WORKER_CA_PATH = "/usr/local/share/ca-certificates/mitmproxy-ca-cert.crt"

# Bound (seconds) for the synchronous ``docker exec update-ca-certificates`` that folds the proxy CA
# into the worker's trust store before the first command (ADR-0011 §6). Sub-second in practice; this is
# a safety net against a wedged ``docker exec``. Proxy path only.
_CA_TRUST_TIMEOUT_S = 60.0

# Grace between SIGTERM and the SIGKILL escalation for a timed-out ``docker exec`` client — short
# (mirrors ``LocalExecutor._KILL_GRACE_S``; a timed-out command is already over its deadline).
_KILL_GRACE_S = 2.0

# Killed-by-signal sentinel for a timeout (mirrors ``LocalExecutor`` / the retired docker executor).
_TIMEOUT_EXIT = -signal.SIGKILL

# The docker "container failed to run" exit-code convention, rendered when ``docker exec`` cannot even
# be spawned (the ``docker`` CLI itself is gone) so a ``bash`` call surfaces a failure, never a crash.
_DAEMON_LOST_EXIT = 125
_DAEMON_LOST_NOTE = "The docker sandbox became unreachable — the session was lost."

# The slim uv base image ships no git, so a model ``git`` command in the Workspace would fail with
# ``command not found``. ``_install_git`` runs this once per default (unwired) session container — cheap
# next to baking a 5x-larger full image, and it keeps the worker's ``ancestor=<image>`` identity intact.
_GIT_INSTALL_CMD = "apt-get update && apt-get install -y --no-install-recommends git"
# Bound (seconds) for that install — ~15s on a warm network; this only caps a wedged / offline apt.
_GIT_INSTALL_TIMEOUT_S = 120.0


class DockerBackend:
    """Run commands + file ops in one session container, fresh-exec (ADR-0012 §2,4).

    Construction is **inert** — no container, no subprocess: the keeper container starts on
    :meth:`create` (called by the :class:`~decode.sandbox.executor.SandboxExecutor` lazily on the first
    ``run`` or eagerly via ``start``). Not safe for concurrent :meth:`exec` calls on one instance.

    **Optional Credential-Proxy wiring (ADR-0011 §6, retained).** When the headless flow runs the
    Credential Proxy it constructs the backend with plain-typed proxy params — a docker ``network`` to
    join, ``proxy_env`` (``http_proxy`` / ``https_proxy`` → the proxy container), and
    ``ca_cert_host_path`` (the host path to the proxy's mitmproxy CA, bind-mounted into the worker). On
    :meth:`create` the CA is folded into the worker's trust store by a **synchronous** ``docker exec
    update-ca-certificates`` — *before* create returns — so the very first ``bash`` already trusts the
    CA and an HTTPS tool call validates, with no race. With all three at their ``None`` defaults (every
    non-proxy caller) the ``docker run`` is byte-identical and no CA step runs. **No proxy type leaks
    in** — they are ``str`` / ``dict`` / ``Path``.
    """

    def __init__(
        self,
        *,
        network: str | None = None,
        proxy_env: dict[str, str] | None = None,
        ca_cert_host_path: Path | None = None,
    ) -> None:
        # Optional Credential-Proxy wiring (all ``None`` off the proxy path → byte-identical run).
        self._network = network
        self._proxy_env = proxy_env
        self._ca_cert_host_path = ca_cert_host_path
        self._container_id: str | None = None
        # The resolved host Workspace bind-mounted at ``/workspace``; the base of every file op. Set on
        # :meth:`create`, cleared on :meth:`destroy`.
        self._workspace: Path | None = None

    # --- lifecycle ------------------------------------------------------------------------------

    async def create(self, workspace: Path) -> None:
        """Start the keeper container, bind-mounting ``workspace`` at ``/workspace`` (ADR-0012 §2).

        ``docker run -d --rm -v <workspace>:/workspace -w /workspace <image> sleep infinity`` (plus the
        optional proxy flags). Idempotent — a second call with a container already up returns. A failed
        ``docker run`` raises :class:`RuntimeError` (the executor renders it; the warm-up call site
        degrades to lazy). On the proxy path the CA is trusted synchronously after create; if that fails
        the just-created container is reaped and the error re-raised (no leak, no untrusted worker).
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
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            # The task-071 startup guard already proved the daemon reachable, so a failure here is a bad
            # image / mount, not a down daemon — surface it clearly (the executor renders it).
            raise RuntimeError(
                f"docker run failed (exit {proc.returncode}): {_decode(stderr).strip()}"
            )
        self._container_id = _decode(stdout).strip()
        logger.info(
            "[sandbox] docker start %s image=%s%s",
            self._container_id,
            settings.sandbox_image,
            " (proxy-wired)" if self._ca_cert_host_path is not None else "",
        )
        if self._ca_cert_host_path is not None:
            try:
                await self._trust_proxy_ca(self._container_id)
            except Exception:
                # The CA step reaped the container; drop the id so a later run re-creates from scratch.
                self._container_id = None
                raise
        # A default (unwired) worker gets git so a model ``git`` command in the Workspace works; a
        # proxy-wired worker is skipped — it sits on an isolated egress network where apt cannot reach
        # Debian mirrors, and the credential-proxy path does not need git.
        if self._network is None and self._proxy_env is None and self._ca_cert_host_path is None:
            await self._install_git(self._container_id)

    async def export(self) -> None:
        """No-op: the bind mount is live, so the host Workspace already IS the sandbox filesystem (§5)."""
        return None

    async def destroy(self) -> None:
        """Force-remove the session container — ``docker rm -f`` (loop-free, best-effort; ADR-0012 §2).

        A *fresh* subprocess that needs no old event loop, so it reaps correctly from the headless
        reaper's fresh loop. Idempotent (a no-op when nothing was created) and best-effort (``--rm`` is
        the crash backstop; a "no such container" race with ``--rm`` is expected and swallowed).
        """
        container_id, self._container_id = self._container_id, None
        self._workspace = None
        if container_id is None:
            return
        logger.info("[sandbox] docker stop %s", container_id)
        await _run_docker_quiet("rm", "-f", container_id)

    # --- command exec ---------------------------------------------------------------------------

    async def exec(self, *args: str, timeout_s: float) -> ExecResult:
        """Run one fresh ``docker exec -w /workspace <id> <args>``; kill only it on timeout (ADR-0012 §2).

        Separate stdout/stderr (no merge). On timeout the ``docker exec`` **client** process group is
        killed (SIGTERM→SIGKILL) — the container and its filesystem survive — and the partial output is
        returned with ``timed_out=True`` and an empty ``note`` (mirroring modal; nothing session-level
        was lost). ``ponytail:`` killing the client does not surgically stop the in-container process; it
        is reaped when the container is destroyed — the simple honest rule (a per-command cgroup +
        ``docker exec … kill`` is the upgrade path). A spawn :class:`OSError` (the ``docker`` CLI itself
        vanished) renders the exit-125 failure instead of crashing the tool (the never-crash contract).
        """
        container_id = self._container_id
        if container_id is None:
            # Defensive: the executor renders create failures itself, so exec is normally reached only
            # after a successful create. Render rather than crash if it is ever called cold.
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

        SIGTERM the group first, SIGKILL after :data:`_KILL_GRACE_S` if it lingers, then ``await`` the
        already-running ``communicate`` task (not a fresh one — that would return empty) so the partial
        output the client buffered before the kill is returned. Mirrors ``LocalExecutor._terminate``.
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

        Containment is layered (ADR-0012 §4). ``_resolve_logical`` above the seam already rejected ``..``
        / absolute escapes with string math for both backends — but string math cannot see a **symlink**:
        because the mount is shared with the host, a symlink planted inside the Workspace (by sandboxed
        ``bash``) could otherwise be *followed* off the mount onto the host by a plain ``self._workspace /
        rel`` pathlib op (a host ``/etc/passwd`` read, a host-file write). So this adds the physical
        layer: resolve the joined path (following symlinks) and raise :class:`WorkspaceEscape` if it
        lands outside the Workspace root — the file layer renders that as a model-readable refusal (it is
        an :class:`OSError`). ``self._workspace`` is already ``.resolve()``d in :meth:`create`, so this is
        a resolved-vs-resolved comparison; a brand-new nested path (nothing on disk yet) resolves
        lexically and stays contained, and an in-workspace symlink pointing INSIDE resolves to its real
        (contained) target.
        """
        if self._workspace is None:
            raise RuntimeError("DockerBackend file ops require a created workspace")
        resolved = (self._workspace / rel).resolve()
        if resolved != self._workspace and self._workspace not in resolved.parents:
            raise WorkspaceEscape(
                f"path {rel!r} escapes the workspace sandbox (resolves outside the bind mount)"
            )
        return resolved

    # --- docker run argv + proxy CA trust -------------------------------------------------------

    def _run_args(self, workspace: Path) -> list[str]:
        """Build the ``docker run`` argv for the keeper container (proxy wiring is additive).

        The base mounts ``workspace`` at ``/workspace`` and runs ``sleep infinity``. On the proxy path
        (ADR-0011 §6) it additionally joins ``--network``, sets each ``proxy_env`` var, and bind-mounts
        the mitmproxy CA read-only — the entry stays ``sleep infinity`` (the CA is trusted by a
        synchronous :meth:`_trust_proxy_ca` after create, which is what closes the first-command
        CA-trust race). No skills mount: skills are seeded host-side into the Workspace by
        :func:`~decode.sandbox.workspace.seed_skills` (ADR-0012 §5). Order is fixed so the base prefix
        never shifts.
        """
        args = ["run", "-d", "--rm", "-v", f"{workspace}:{_WORKSPACE}", "-w", _WORKSPACE]
        if self._network is not None:
            args += ["--network", self._network]
        for key, value in (self._proxy_env or {}).items():
            args += ["-e", f"{key}={value}"]
        if self._ca_cert_host_path is not None:
            args += ["-v", f"{self._ca_cert_host_path}:{_WORKER_CA_PATH}:ro"]
        args += [settings.sandbox_image, "sleep", "infinity"]
        return args

    async def _trust_proxy_ca(self, container_id: str) -> None:
        """Fold the mounted mitmproxy CA into the worker's trust store, synchronously (ADR-0011 §6).

        Runs ``docker exec <id> update-ca-certificates`` and **waits** so the CA is trusted before
        :meth:`create` returns — the fix for the first-command CA-trust race. Bounded by
        :data:`_CA_TRUST_TIMEOUT_S`. On failure (non-zero / timeout) the just-created container is reaped
        and a :class:`RuntimeError` is raised (rendered by the executor; never a silent leak). Proxy
        path only.
        """
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            container_id,
            "update-ca-certificates",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # one stream: update-ca-certificates chats on stderr
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_CA_TRUST_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()  # reap the killed exec (no zombie / ResourceWarning)
            await _run_docker_quiet("rm", "-f", container_id)  # don't leak the worker
            raise RuntimeError(
                f"update-ca-certificates timed out after {_CA_TRUST_TIMEOUT_S:g}s"
            ) from None
        if proc.returncode != 0:
            await _run_docker_quiet("rm", "-f", container_id)  # don't leak the worker
            raise RuntimeError(
                f"update-ca-certificates failed (exit {proc.returncode}): {_decode(stdout).strip()}"
            )
        logger.info("[sandbox] proxy CA trusted in worker %s", container_id[:12])

    async def _install_git(self, container_id: str) -> None:
        """Best-effort ``apt-get install git`` **+ git-identity config** in the fresh keeper container.

        The slim base ships no git, so one ``sh -c`` installs it and (chained with ``&&``, so only after a
        successful install) sets the ``SANDBOX_GIT_USER_*`` identity via ``git config --global`` — folded
        into this same exec so it adds no extra ``docker exec`` (see :func:`_git_setup_command`), and a
        model ``git commit`` in the Workspace then works out of the box.

        ``ponytail:`` installed per session into the container rather than baking a fatter image — this
        keeps the 278 MB slim base AND the worker's ``ancestor=<image>`` identity (so every hygiene reap
        filter and ``docker run`` argv test stays valid), at the cost of a one-off ~15 s apt at session
        start (bake git into a custom ``SANDBOX_IMAGE`` to skip it). **Best-effort** — a failure (offline
        / restricted apt) logs a warning and leaves the session running with no git (the model would see
        ``git: command not found``, exactly today's behavior), never a crash. Bounded by
        :data:`_GIT_INSTALL_TIMEOUT_S`.
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
                "[sandbox] git install could not spawn: %s (continuing without git)", exc
            )
            return
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_GIT_INSTALL_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()  # reap the killed exec (no zombie / ResourceWarning)
            logger.warning(
                "[sandbox] git install timed out after %gs (continuing without git)",
                _GIT_INSTALL_TIMEOUT_S,
            )
            return
        if proc.returncode != 0:
            logger.warning(
                "[sandbox] git install failed (exit %s; continuing without git): %s",
                proc.returncode,
                _decode(stdout).strip()[-500:],
            )
            return
        logger.info("[sandbox] git installed in worker %s", container_id[:12])


def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    """Send ``sig`` to the ``docker exec`` client's whole process group; tolerate an already-dead one.

    With ``start_new_session=True`` the client's PID is its process-group id, so ``os.killpg(pid, sig)``
    reaches it (mirrors ``LocalExecutor``). This kills the ``docker exec`` **client**; ``ponytail:`` the
    in-container command may outlive it — it is reaped when the container is destroyed.
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


def _git_setup_command() -> str:
    """The ``sh -c`` line that installs git and (if configured) sets its identity, ``&&``-chained.

    Config runs only after a successful install (no git → nothing to configure). The identity comes from
    :func:`~decode.sandbox.workspace.git_config_pairs` (default ``decode`` / ``decode@localhost``), each
    value ``shlex.quote``d so a name with spaces / quotes stays one safe shell token — so a model ``git
    commit`` in the Workspace works out of the box.
    """
    parts = [_GIT_INSTALL_CMD]
    parts += [
        f"git config --global {key} {shlex.quote(value)}" for key, value in git_config_pairs()
    ]
    return " && ".join(parts)
