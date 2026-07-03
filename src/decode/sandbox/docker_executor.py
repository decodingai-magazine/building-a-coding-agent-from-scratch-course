"""The Docker sandbox executor — one session container + one persistent bash shell (ADR-0011 §2).

:class:`DockerExecutor` is a :class:`~decode.tools.exec.CommandExecutor` (the ``tools/exec.py``
Protocol) that runs model-chosen commands inside **one session-persistent Docker container**,
driving a **single long-lived bash shell** so ``cd`` / ``export`` / ``pip install`` persist across
``bash`` calls within a session — the canonical DockerSandbox shape. It is a directly-tested class
here; wiring it into ``bash``'s selection seam is task 074, and its optional Credential-Proxy wiring
(``network`` / ``proxy_env`` / ``ca_cert_host_path``) is task 075 (ADR-0011 §6).

**Docker CLI, not the SDK (ADR-0011 Alternatives).** Every docker interaction is the standard
``docker`` CLI shelled out with :mod:`asyncio` subprocesses — dependency-free (the docker Python SDK
is present only transitively via zenml and must not be relied on), mirroring
:class:`~decode.tools.exec.LocalExecutor`'s style, and — the strategic payoff — making gVisor / Kata
zero-code daemon-config upgrades (a Linux operator sets ``--runtime=runsc`` and every sandbox command
inherits it). No docker type ever leaks past this module: callers see only :class:`ExecResult`.

**Lifecycle (lazy, one per session).** On the **first** :meth:`run` the keeper container is started
with ``docker run -d --rm -v <abs cwd>:/workspace -w /workspace <image> sleep infinity`` and its id
captured; every later command reuses it. The persistent shell is ``docker exec -i <id> bash
--noprofile --norc`` (``-i``, **not** ``-it`` → clean pipes, no TTY), held open over its stdin/stdout.
:meth:`aclose` (task 074 calls it on the exit path) stops and removes the container — idempotent,
best-effort; ``--rm`` is the crash backstop.

**Command protocol (the teaching heart).** Per command a unique end marker ``__DECODE_END_<uuid>__``
is generated and the shell stdin gets a **brace group** carrying the command and a marker ``printf``:
``{ <command>\\n printf '\\n%s %s\\n' "<marker>" "$?"\\n} </dev/null``. We read the shell stdout
line-by-line **until** a line beginning ``<marker> ``; everything before it is the output, the trailing
int is the exit code. Three shape choices carry the protocol's robustness:

- **A brace group with stdin from ``/dev/null``.** A brace group (not a subshell) runs in the *current*
  shell so ``cd`` / ``export`` still persist; ``</dev/null`` starves a **stdin-reading** command
  (``cat``, ``read``, a REPL) so it sees EOF instead of stealing the marker ``printf`` off the shared
  stdin pipe (which would eat the marker → a spurious full-timeout hang + shell reset). A heredoc
  (``cat <<EOF``) supplies its own stdin and overrides the redirect. The marker ``printf`` lives
  *inside* the group so it is never the empty ``{ }`` (a bash syntax error) even for an empty or
  comment-only command.
- **The marker ``printf`` starts with a newline** so the marker lands on its own line even when the
  command's output has **no trailing newline** (``echo -n hi``, ``printf 'x'``) — without it the marker
  concatenates onto the command's last output line and is never matched (a full-timeout hang). The
  bytes collected before the marker are then always the command's true output plus exactly one ``\\n``,
  which :func:`_recover_stdout` strips back off to recover the output faithfully.
- **The random per-call uuid** is why a command that *prints* a marker-like string cannot truncate the
  read early.

**Streams are merged**: the shell's first line is ``exec 2>&1`` and the ``docker exec`` subprocess is
created with ``stderr=STDOUT``, so command stdout+stderr arrive on one pipe in the command's own order
and :attr:`ExecResult.stderr` stays ``""`` (``bash._render`` shows one section). ``ponytail:`` a
two-marker / separate-fd scheme could split the streams — merged is the honest simple capture for the
tutorial, and it keeps exactly one pipe to drain (no leak under ``filterwarnings=error``).

**Timeout = kill + restart the shell (ADR-0011 §2).** The read-until-marker is bounded by
``timeout_s``; on expiry the ``docker exec`` **host** subprocess is killed by process group (started
``start_new_session=True`` and signalled with :func:`os.killpg`, exactly like ``LocalExecutor`` — no
orphaned *host* process), the shell is marked for lazy respawn (so the next :meth:`run` starts a fresh
shell back at ``/workspace`` with a cleared env — **state reset**), and an :class:`ExecResult` with
the partial output, ``timed_out=True``, and a shell-reset :attr:`~ExecResult.note` is returned so the
model is told its state was cleared. ``ponytail:`` decode cannot *surgically* kill one hung command
inside the container while keeping the session — restart is the simple honest rule; a per-command
PID/cgroup + ``docker exec … kill`` is the upgrade path.

**Deviation from canonical (ADR-0011 §2).** The canonical Stage-2 example mounts an **empty named
volume** at ``/workspace`` — no host tree. decode-docker deliberately **bind-mounts the cwd** instead,
because decode keeps its file tools host-side and the real repo must be one shared tree with them
(no split-brain). decode-**modal** (task 073) is the canonical empty-scratch shape.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path
from typing import Any
from uuid import uuid4

from decode.config.settings import settings
from decode.tools.exec import ExecResult

logger = logging.getLogger(__name__)

# The container-side workspace: the bind-mount target and the shell's initial (post-reset) cwd.
_WORKSPACE = "/workspace"

# Where the Credential Proxy's mitmproxy CA is bind-mounted inside the worker (ADR-0011 §6). A
# ``.crt`` under ``/usr/local/share/ca-certificates`` is exactly what ``update-ca-certificates`` folds
# into the system trust store, so an outbound HTTPS request through the proxy validates. Only used on
# the proxy path (``ca_cert_host_path`` set); the non-proxy ``docker run`` never references it.
_WORKER_CA_PATH = "/usr/local/share/ca-certificates/mitmproxy-ca-cert.crt"

# Bound (seconds) for the synchronous ``docker exec update-ca-certificates`` that folds the proxy CA
# into the worker's trust store before the first command (ADR-0011 §6). The op itself is sub-second;
# this is a safety net against a wedged ``docker exec`` (no ``sandbox_startup_timeout_s`` setting
# exists — a fixed internal bound, not user-tunable). Proxy path only.
_CA_TRUST_TIMEOUT_S = 60.0

# Grace between SIGTERM and the SIGKILL escalation when the shell's process group ignores the polite
# signal — deliberately short (mirrors ``LocalExecutor._KILL_GRACE_S``; a timed-out shell is already
# over its deadline). Bounds the drain/reap of a torn-down or timed-out shell.
_KILL_GRACE_S = 2.0

# StreamReader per-line buffer for the shell's stdout. Generous so realistic multi-megabyte outputs
# (built line-by-line) never overrun; ``ponytail:`` a single unbroken line longer than this still
# overruns → the shell is reset (degraded, never crashed) — chunked/bounded reads are the upgrade path.
_STREAM_LINE_LIMIT = 8 * 1024 * 1024

# Exit-code sentinels for the two abnormal, no-real-$? paths (the normal path parses ``$?`` from the
# marker line). Timeout mirrors ``LocalExecutor``'s "killed by signal" convention; the shell-ended
# sentinel flags a shell that closed before emitting its marker.
_TIMEOUT_EXIT = -signal.SIGKILL
_SHELL_ENDED_EXIT = -1

# The model-facing :attr:`ExecResult.note` for a timeout: the shell was killed and restarted, so its
# state is gone. Honest about the ceiling (the in-container process may outlive the kill).
_SHELL_RESET_NOTE = (
    "Note: the command exceeded its timeout, so the sandbox shell was killed and restarted. Its "
    "working directory and environment were reset (back to /workspace, with the environment "
    "cleared), and any process it started inside the sandbox may keep running until the sandbox is "
    "torn down."
)

# The note for the defensive path where the shell ends before its completion marker (the command
# exited the shell, or produced an extremely long unbroken line): it is restarted, state reset.
_SHELL_ENDED_NOTE = (
    "Note: the sandbox shell ended before the command's completion marker was seen (for example the "
    "command exited the shell, or produced an extremely long unbroken line). It was restarted, so "
    "its working directory and environment were reset."
)

# The docker "container failed to run" exit-code convention, returned when the daemon becomes
# unreachable mid-session so a ``bash`` call surfaces a rendered failure instead of crashing the tool.
_DAEMON_LOST_EXIT = 125
_DAEMON_LOST_NOTE = "Docker daemon became unreachable — the sandbox session was lost."


class DockerExecutor:
    """Run commands in one session container over one persistent bash shell (ADR-0011 §2).

    Construction is **inert** — no container, no subprocess: the keeper container and shell are
    started lazily on the first :meth:`run`. Not safe for concurrent :meth:`run` calls on one
    instance (one shell, read one command at a time) — decode drives ``bash`` one call at a time.
    Call :meth:`aclose` to tear the container down (task 074 wires it into the exit path).

    **Optional Credential-Proxy wiring (ADR-0011 §6, task 075).** This is :class:`DockerExecutor`'s
    second caller. When the headless flow runs the Credential Proxy it constructs the worker with
    plain-typed proxy params — a docker ``network`` to join, ``proxy_env`` (``http_proxy`` /
    ``https_proxy`` → the proxy container), and ``ca_cert_host_path`` (the host path to the proxy's
    mitmproxy CA, bind-mounted into the worker). On the first :meth:`run` the CA is folded into the
    worker's trust store by a **synchronous** ``docker exec update-ca-certificates`` inside
    :meth:`_ensure_container` — *before* it returns the container as ready — so the very first ``bash``
    (a lazily-created worker) already trusts the CA and an HTTPS tool call validates, with **no race**
    against a still-booting step. **No proxy type leaks in** — they are ``str`` / ``dict`` / ``Path``.
    With all three at their ``None`` defaults (every non-proxy caller — ``select_executor``, the tests)
    the ``docker run`` is **byte-identical** to before this task and no CA step runs.
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
        self._shell: asyncio.subprocess.Process | None = None
        # The event loop the persistent shell subprocess was created on. ``aclose`` compares it to the
        # loop it runs on: on the interactive exit path they match (await the shell cleanly), but the
        # headless flow reaps from a DIFFERENT (fresh) loop — the shell's per-call loop is closed, so
        # its loop-bound futures/transports cannot be awaited ("Event loop is closed") and must be
        # torn down loop-free instead (ADR-0011 §4). Paired with ``_shell``: set on spawn, cleared on reset.
        self._shell_loop: asyncio.AbstractEventLoop | None = None
        # The cwd bind-mounted on first run(); the mount is fixed for the session (the shell owns its
        # own cwd thereafter — that is the whole point of the persistent shell).
        self._mounted_cwd: Path | None = None

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Run ``command`` in the session container; on timeout kill+reset the shell (ADR-0011 §2).

        Lazily starts the container (bind-mounting ``cwd`` on the first call) and the persistent
        shell, writes ``command`` + a unique end-marker ``printf`` to the shell, and reads its
        stdout until the marker line — whose trailing int is the exit code. A normal command returns
        :class:`ExecResult` with ``timed_out=False`` and an empty ``note``. A timeout returns the
        partial output with ``timed_out=True`` and a shell-reset ``note`` (the shell is respawned on
        the next call, resetting cwd/env). Streams are merged, so ``stderr`` is always ``""``.

        If the docker daemon is **unreachable** when starting the container / shell (e.g. Docker Desktop
        quit mid-session; ``docker run`` / ``docker exec`` fail to launch), the known infra-failure
        exceptions this code raises (its :class:`RuntimeError` wrapper, :class:`OSError` /
        :class:`FileNotFoundError` on spawn) are caught and returned as a rendered failure
        :class:`ExecResult` (``exit_code=125``, the failure text on ``stderr``, a daemon-lost ``note``)
        so the model reacts instead of the tool crashing — the never-crash executor contract.
        """
        try:
            container_id = await self._ensure_container(cwd)
            shell = await self._ensure_shell(container_id)
        except (RuntimeError, OSError) as exc:
            # The daemon went away (or docker CLI cannot spawn): surface a model-facing failure and drop
            # the stale session so a later call re-attempts from scratch. Scoped to the KNOWN infra
            # exceptions — not a blanket ``except`` — so real bugs still surface.
            logger.warning("[sandbox] docker unavailable, sandbox session lost: %s", exc)
            # If _ensure_container brought the keeper container up (``sleep infinity``) but _ensure_shell
            # THEN failed (e.g. BrokenPipeError draining ``exec 2>&1``), the id is already captured —
            # force-remove it BEFORE discarding so ``--rm`` (which only reaps on a stopped container, and
            # ``sleep infinity`` never stops) does not orphan it. Reuses aclose's loop-free ``docker rm -f``
            # path (the ONE teardown discipline). A no-op on the common daemon-down path (id is None).
            if self._container_id is not None:
                await _run_docker_quiet("rm", "-f", self._container_id)
            self._discard_session()
            return ExecResult(
                "", str(exc), _DAEMON_LOST_EXIT, timed_out=False, note=_DAEMON_LOST_NOTE
            )

        marker = _make_marker()
        try:
            shell.stdin.write(_build_payload(command, marker))  # type: ignore[union-attr]
            await shell.stdin.drain()  # type: ignore[union-attr]
        except (BrokenPipeError, ConnectionResetError):
            # The shell died before it could take the command; reset and tell the model (defensive —
            # the startup guard keeps a live daemon, so this is a torn-down/OOM container, not routine).
            logger.debug("[sandbox] shell stdin broken; resetting")
            await self._stop_shell()
            return ExecResult("", "", _SHELL_ENDED_EXIT, timed_out=False, note=_SHELL_ENDED_NOTE)

        # The accumulator is external so a timeout that cancels the read still keeps the lines already
        # read (asyncio.wait_for awaits the cancellation before raising, so ``out`` is final here) —
        # the same "don't discard partial output" discipline as LocalExecutor's non-cancelled read.
        out: list[bytes] = []
        try:
            exit_code = await asyncio.wait_for(
                _read_until_marker(shell, marker, out), timeout=timeout_s
            )
        except TimeoutError:
            await (
                self._stop_shell()
            )  # kill the host process group + reap; next run() respawns fresh
            stdout = _decode(b"".join(out))
            logger.debug(
                "[sandbox] $ %s timed out after %gs → shell reset (bytes=%d)",
                command,
                timeout_s,
                len(stdout),
            )
            return ExecResult(stdout, "", _TIMEOUT_EXIT, timed_out=True, note=_SHELL_RESET_NOTE)

        raw = b"".join(out)
        if exit_code is None:
            # The shell closed before its marker (EOF / oversized line): reset and report (defensive).
            # No marker was emitted, so the output carries no marker-printf newline to strip.
            await self._stop_shell()
            stdout = _decode(raw)
            logger.debug("[sandbox] $ %s ended without a marker → shell reset", command)
            return ExecResult(
                stdout, "", _SHELL_ENDED_EXIT, timed_out=False, note=_SHELL_ENDED_NOTE
            )

        # Marker seen: strip the single trailing newline the marker printf added (see _recover_stdout)
        # so the command's true output is recovered — correct whether or not it ended in a newline.
        stdout = _recover_stdout(raw)
        logger.debug(
            "[sandbox] $ %s → exit=%d bytes=%d cwd=%s", command, exit_code, len(stdout), _WORKSPACE
        )
        return ExecResult(stdout, "", exit_code, timed_out=False)

    async def start(self, cwd: Path) -> None:
        """Eagerly start the session container — the REPL warm-up hook (idempotent; ADR-0011 §4).

        Called once by :func:`decode.tools.bash.warm_executor` at REPL launch so the keeper
        container is up (and visible in ``docker ps``) from the start of the session instead of
        materializing invisibly mid-first-turn — and so the first ``bash`` skips the image-pull /
        container-start latency. Only the container comes up; the persistent shell stays lazy
        (created on the first real command, exactly as before). Idempotent: a second ``start`` —
        or the first ``run`` after it — finds the cached container id and starts nothing new.
        Infra failures propagate (same exceptions ``run`` catches); the warm-up call site wraps
        them and degrades to the lazy path.
        """
        await self._ensure_container(cwd)

    async def aclose(self) -> None:
        """Reap the session container + shell — idempotent, best-effort, LOOP-INDEPENDENT (ADR-0011 §2,4).

        Safe to call when nothing was ever started (a no-op) and safe to call twice. Two teardown paths,
        because the shell subprocess + pipe transports are bound to the loop they were created on:

        * **Interactive exit (same loop)** — ``close_executor`` is awaited on the loop the shell was
          created on, so we await it cleanly (drain+close the pipes, reap the host process), exactly as
          before — no regression.
        * **Headless reap (foreign/closed loop)** — a ``decode run`` reaps in a ``finally`` on a *fresh*
          loop, while the shell was created on a kitaru per-call loop that is now closed. Awaiting its
          loop-bound futures there raises ``RuntimeError: Event loop is closed`` / ``Future attached to a
          different loop`` — which previously escaped and left the container **running** (the headline
          074 defect). So there we tear the shell down **loop-free** (SIGKILL its process group + reap
          the zombie via ``os.waitpid`` + best-effort transport close), never awaiting the dead loop.

        Either way the container removal — ``docker rm -f <id>``, a *fresh* subprocess that needs no old
        loop — **always runs** (it is the load-bearing reap and also kills the container-side shell).
        Failures are swallowed; ``--rm`` is the crash backstop.
        """
        shell, self._shell = self._shell, None
        shell_loop, self._shell_loop = self._shell_loop, None
        container_id, self._container_id = self._container_id, None
        self._mounted_cwd = None

        if shell is not None:
            if shell_loop is not None and shell_loop is _running_loop():
                # Same live loop the shell was created on (interactive exit): await it cleanly.
                await self._teardown_shell_clean(shell)
            else:
                # Foreign / closed loop (headless reap): tear it down without touching the dead loop.
                _kill_shell_loop_free(shell)

        if container_id is None:
            return
        logger.info("[sandbox] docker stop %s", container_id)
        # ``docker rm -f`` stops (SIGKILL) and removes in one synchronous call. With ``--rm`` on the
        # container the daemon may also auto-remove it, so a "no such container" race is expected and
        # ignored (best-effort) — either way the container is gone.
        await _run_docker_quiet("rm", "-f", container_id)

    def _discard_session(self) -> None:
        """Drop stale session state loop-free after a daemon loss so a later ``run`` re-attempts (§2).

        Called from :meth:`run` when the daemon became unreachable while starting the container/shell:
        the cached container id + shell handle are unusable, so null them (no await — the daemon is gone
        and, on the first-container path, no shell exists yet). The next :meth:`run` re-runs ``docker
        run`` from scratch.
        """
        self._container_id = None
        self._shell = None
        self._shell_loop = None
        self._mounted_cwd = None

    async def _ensure_container(self, cwd: Path) -> str:
        """Start the keeper container on first use (bind-mounting ``cwd``); return its id (cached)."""
        if self._container_id is not None:
            if self._mounted_cwd is not None and cwd.resolve() != self._mounted_cwd:
                # The container's bind mount is fixed for the session; a later differing cwd is not
                # re-mounted (the shell owns its cwd). Log it rather than silently surprising a reader.
                logger.debug(
                    "[sandbox] cwd %s differs from the mounted %s; mount is fixed for the session",
                    cwd,
                    self._mounted_cwd,
                )
            return self._container_id

        abs_cwd = cwd.resolve()
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *self._docker_run_args(abs_cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            # Exceptional: the task-071 startup guard already proved the daemon reachable, so a
            # failure here is a bad image / mount, not a down daemon. Surface it clearly.
            raise RuntimeError(
                f"docker run failed (exit {proc.returncode}): {_decode(stderr).strip()}"
            )
        self._container_id = _decode(stdout).strip()
        self._mounted_cwd = abs_cwd
        logger.info(
            "[sandbox] docker start %s image=%s%s",
            self._container_id,
            settings.sandbox_image,
            " (proxy-wired)" if self._ca_cert_host_path is not None else "",
        )
        if self._ca_cert_host_path is not None:
            # Fold the mounted mitmproxy CA into the worker's trust store **synchronously, before
            # returning the container as ready** — so the very first ``bash`` (a lazily-created worker)
            # already trusts the proxy CA and an HTTPS tool call validates. Doing it here (not as a
            # PID-1 entry step) closes the race the old ``bash -c "update-ca-certificates && …"`` had:
            # ``docker run -d`` returns before that step finished, so the first ``docker exec`` landed in
            # the untrusted window (ADR-0011 §6). Proxy path only; the non-proxy container never runs it.
            await self._trust_proxy_ca(self._container_id)
        return self._container_id

    def _docker_run_args(self, abs_cwd: Path) -> list[str]:
        """Build the ``docker run`` argv for the keeper container (proxy wiring is additive).

        With no Credential-Proxy wiring (every non-proxy caller) this is **byte-identical** to the
        task-072 command: ``run -d --rm -v <cwd>:/workspace -w /workspace <image> sleep infinity``. On
        the proxy path (task 075) it additionally joins ``--network``, sets each ``proxy_env`` var
        (``http_proxy`` / ``https_proxy`` → the proxy container), and bind-mounts the mitmproxy CA
        read-only. The entry command stays ``sleep infinity`` in **both** cases — the CA is trusted by a
        synchronous :meth:`_trust_proxy_ca` after create (not a PID-1 entry step), which is what closes
        the first-command CA-trust race. Order is fixed so the non-proxy prefix never shifts.
        """
        args = ["run", "-d", "--rm", "-v", f"{abs_cwd}:{_WORKSPACE}", "-w", _WORKSPACE]
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

        Runs ``docker exec <id> update-ca-certificates`` and **waits for it to finish** so the CA is in
        the system trust store before :meth:`_ensure_container` returns — the fix for the first-command
        CA-trust race (a lazily-created worker's first ``bash`` used to race a PID-1
        ``update-ca-certificates``, so the first HTTPS tool call failed ``CERTIFICATE_VERIFY_FAILED``).
        Runs on ``run_sync``'s loop (setup — same loop the shell will use), so there is no cross-loop
        concern. Bounded by :data:`_CA_TRUST_TIMEOUT_S`. On failure (non-zero / timeout) the just-created
        container is reaped and a :class:`RuntimeError` is raised — caught by :meth:`run`'s infra-failure
        handler and rendered for the model (never a silent leak, never a crash). Proxy path only.
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

    async def _ensure_shell(self, container_id: str) -> asyncio.subprocess.Process:
        """Return the live persistent shell, lazily (re)spawning it after a reset (ADR-0011 §2).

        The shell is ``docker exec -i <id> bash --noprofile --norc`` with **merged** streams
        (``stderr=STDOUT`` on the subprocess + ``exec 2>&1`` as its first line). ``start_new_session``
        makes the host process a group leader so a timeout kill reaches it as a unit (no host orphans).
        """
        if self._shell is not None and self._shell.returncode is None:
            return self._shell

        shell = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            container_id,
            "bash",
            "--noprofile",
            "--norc",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # one pipe: command stdout+stderr merged, ordered
            start_new_session=True,  # own process group → kill it as a unit on timeout, no orphans
            limit=_STREAM_LINE_LIMIT,
        )
        # Merge stderr into stdout *inside* the shell too, so a command's own stdout/stderr interleave
        # in the command's order (not the daemon's demux order) before docker ever sees them.
        shell.stdin.write(b"exec 2>&1\n")  # type: ignore[union-attr]
        await shell.stdin.drain()  # type: ignore[union-attr]
        self._shell = shell
        # Record the loop the shell's subprocess + pipe transports are bound to, so ``aclose`` knows
        # whether it can await them (same loop) or must tear them down loop-free (a foreign/closed loop).
        self._shell_loop = asyncio.get_running_loop()
        return shell

    async def _stop_shell(self) -> None:
        """Kill the persistent shell's host process group and reap it; next run() respawns it.

        Setting ``self._shell = None`` first is the **state reset**: a fresh shell starts back at
        ``/workspace`` with a cleared env. Called only from :meth:`run` (a timeout / shell-ended reset),
        which always runs on the shell's own loop, so the clean same-loop teardown
        (:meth:`_teardown_shell_clean`) is correct here. Best-effort and safe if never started.
        """
        shell, self._shell = self._shell, None
        self._shell_loop = None
        if shell is None:
            return
        await self._teardown_shell_clean(shell)

    @staticmethod
    async def _teardown_shell_clean(shell: asyncio.subprocess.Process) -> None:
        """Await the shell's clean teardown — MUST run on the loop the shell was created on.

        Kills the shell's host process group (SIGTERM → SIGKILL after :data:`_KILL_GRACE_S`), then drains
        stdout to EOF and closes stdin so no pipe transport is left unclosed (``filterwarnings=error``
        hermeticity). All the awaits touch the shell's loop-bound futures/transports, so this is only
        valid on that loop (the ``run`` reset + the interactive ``aclose`` path); the headless
        cross-loop path uses :func:`_kill_shell_loop_free` instead.
        """
        if shell.returncode is None:
            _signal_group(shell, signal.SIGTERM)
            try:
                await asyncio.wait_for(shell.wait(), timeout=_KILL_GRACE_S)
            except TimeoutError:
                _signal_group(shell, signal.SIGKILL)
                await shell.wait()
        if shell.stdout is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(shell.stdout.read(), timeout=_KILL_GRACE_S)
        if shell.stdin is not None:
            with contextlib.suppress(Exception):
                shell.stdin.close()


def _make_marker() -> str:
    """A unique per-call end marker (``__DECODE_END_<uuid4hex>__``); its randomness defeats spoofing."""
    return f"__DECODE_END_{uuid4().hex}__"


def _build_payload(command: str, marker: str) -> bytes:
    """The bytes written to the shell: ``command`` in a stdin-starved brace group + a marker ``printf``.

    Shape: ``{ <command>\\n printf '\\n%s %s\\n' "<marker>" "$?"\\n} </dev/null``. The brace group runs
    in the *current* shell (so ``cd`` / ``export`` persist) with stdin from ``/dev/null`` so a
    stdin-reading command sees EOF instead of stealing the marker ``printf`` off the shared pipe; a
    heredoc overrides the redirect. The ``printf`` lives inside the group (so an empty / comment-only
    command is not an empty ``{ }`` syntax error) and starts with ``\\n`` so the marker lands on its own
    line even when the command's output has no trailing newline (:func:`_recover_stdout` strips that one
    newline back off). ``$?`` is read the instant the command finishes — the ``printf`` is its immediate
    successor in the group — so it is the command's (or compound's) real exit status.
    """
    return f'{{ {command}\nprintf \'\\n%s %s\\n\' "{marker}" "$?"\n}} </dev/null\n'.encode()


def _recover_stdout(raw: bytes) -> str:
    """Recover a command's true stdout from the bytes collected before the marker line.

    :func:`_build_payload`'s marker ``printf`` starts with a newline, so the collected bytes are always
    the command's real output plus exactly one trailing ``\\n``. Removing that single newline recovers
    the output faithfully: output that already ended in ``\\n`` keeps it, output with none is not handed
    a spurious one. ``removesuffix`` (not ``[:-1]``) is a no-op if the newline is somehow absent, so it
    can never over-strip. Undecodable bytes are UTF-8-replaced (never crash).
    """
    return _decode(raw.removesuffix(b"\n"))


def _parse_exit_code(line: bytes, marker: str) -> int:
    """Parse the exit code off a marker line (``<marker> <int>``); fall back to a sentinel if malformed."""
    tail = line.decode("utf-8", errors="replace").removeprefix(f"{marker} ").strip()
    try:
        return int(tail)
    except ValueError:
        return _SHELL_ENDED_EXIT


async def _read_until_marker(
    shell: asyncio.subprocess.Process, marker: str, out: list[bytes]
) -> int | None:
    """Read shell stdout lines into ``out`` until the marker line; return its exit code (``None`` on EOF).

    A line is the marker line only when it **starts** with ``<marker> `` — a command that merely
    *prints* a marker-like string mid-line does not match (and cannot guess the per-call uuid), so the
    read never truncates early. Returns ``None`` if the pipe closes before the marker (the shell ended)
    or a single line overruns the buffer limit — the caller resets the shell in both cases.
    """
    marker_prefix = f"{marker} ".encode()
    reader = shell.stdout
    assert reader is not None  # created with stdout=PIPE
    while True:
        try:
            line = await reader.readline()
        except (asyncio.LimitOverrunError, ValueError):
            # A single unbroken line exceeded _STREAM_LINE_LIMIT; marker sync is lost. ponytail: the
            # honest ceiling — the shell is reset by the caller. Chunked reads are the upgrade path.
            return None
        if not line:
            return None  # EOF: the shell closed before emitting the marker
        if line.startswith(marker_prefix):
            return _parse_exit_code(line, marker)
        out.append(line)


def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    """Send ``sig`` to the shell host process's whole group; tolerate an already-dead process.

    With ``start_new_session=True`` the host process's PID is its process-group id, so
    ``os.killpg(pid, sig)`` reaches it and any host child in one call (mirrors ``LocalExecutor``).
    This kills the ``docker exec`` **client**; ``ponytail:`` the in-container command may outlive it
    (decode cannot surgically kill it while keeping the session) — it is reaped when the container is.
    """
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, sig)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """The current running loop, or ``None`` if not inside one (never raises)."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _kill_shell_loop_free(shell: asyncio.subprocess.Process) -> None:
    """Tear down the host ``docker exec`` shell WITHOUT touching its (foreign/closed) loop (ADR-0011 §4).

    The headless reap path runs on a *different* loop than the one the shell's subprocess + pipe
    transports were created on (a kitaru per-call loop, now closed), so awaiting or ``transport.close()``
    would raise ``Event loop is closed``. Instead we tear the shell down through its **underlying
    ``subprocess.Popen``** — whose ``kill`` / ``wait`` / file-``close`` are plain OS calls that need no
    event loop. ``popen.wait`` sets ``popen.returncode``, so neither the ``Popen`` nor the asyncio
    subprocess transport emits a stale-state ``ResourceWarning`` at GC, and closing ``popen``'s file
    objects releases the pipe FDs. If the Popen handle is unreachable we fall back to killing by pid.
    All best-effort — the container ``docker rm -f`` (aclose's next step) is what truly ends the session.
    """
    # 1. Kill the whole process group (loop-free) so the ``docker exec`` client + any host child die.
    if shell.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(shell.pid, signal.SIGKILL)

    # 2. Reap the process, loop-free. Prefer the underlying ``Popen.wait`` (it sets ``popen.returncode``,
    #    which silences the "subprocess still running" ``ResourceWarning``); else a bare ``os.waitpid``.
    #    The asyncio child watcher may win the reap race → ``wait`` raises ``ChildProcessError``;
    #    suppressed.
    popen = _underlying_popen(shell)
    if popen is not None:
        with contextlib.suppress(Exception):
            popen.wait(timeout=_KILL_GRACE_S)
    else:
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(shell.pid, 0)

    # 3. Release the pipe FDs + neutralize the orphaned asyncio transports (all loop-free, best-effort)
    #    so no ``ResourceWarning`` fires at GC on the dead loop — the warnings print as
    #    ``Exception ignored ... Traceback`` on stderr, so a real ``decode run`` must be clean of them.
    _neutralize_shell_transports(shell)


def _underlying_popen(shell: asyncio.subprocess.Process) -> Any:
    """The raw ``subprocess.Popen`` behind an asyncio subprocess, or ``None`` (best-effort, private)."""
    return getattr(getattr(shell, "_transport", None), "_proc", None)


def _neutralize_shell_transports(shell: asyncio.subprocess.Process) -> None:
    """Close the shell's pipe FDs + defuse the orphaned asyncio transports, loop-free (ADR-0011 §4).

    The subprocess + pipe transports are bound to a closed per-call loop, so their own ``close()`` (which
    schedules on that loop) raises. We reach each pipe transport's underlying OS pipe and close it
    directly (a plain file ``close`` — loop-free, releases the fd), then defuse the objects' ``__del__``
    finalizers so no ``ResourceWarning`` is emitted at GC: null the pipe transport's ``_pipe`` (silences
    ``_Unix*PipeTransport.__del__``'s "unclosed transport"), mark it ``_closing`` (silences
    ``StreamWriter.__del__``'s "loop is closed"), and mark the subprocess transport ``_closed`` (silences
    ``BaseSubprocessTransport.__del__``'s "unclosed transport"). Every step is suppressed — this is pure
    cleanliness (keeping ``decode run`` stderr + ``filterwarnings=error`` clean); it degrades to a
    harmless GC warning if a future CPython renames an internal, and never affects the container reap.
    """
    transport = getattr(shell, "_transport", None)
    if transport is None:
        return
    for fd in (0, 1, 2):
        pipe_transport = None
        with contextlib.suppress(Exception):
            pipe_transport = transport.get_pipe_transport(fd)
        if pipe_transport is None:
            continue
        with contextlib.suppress(Exception):
            pipe = pipe_transport.get_extra_info("pipe")
            if pipe is not None:
                pipe.close()  # release the fd (plain file close — loop-free)
        with contextlib.suppress(Exception):
            pipe_transport._pipe = None  # _Unix*PipeTransport.__del__: nothing left to warn about
        with contextlib.suppress(Exception):
            pipe_transport._closing = (
                True  # StreamWriter.__del__: is_closing() → skip "loop is closed"
            )
    with contextlib.suppress(Exception):
        transport._closed = True  # BaseSubprocessTransport.__del__: skip "unclosed transport"


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
