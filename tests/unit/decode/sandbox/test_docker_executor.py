"""Hermetic unit tests for the Docker sandbox executor (``decode.sandbox.docker_executor``).

These exercise the parts of :class:`DockerExecutor` that need **no docker daemon** (ADR-0011 §2): the
marker/command protocol helpers, the read-until-marker loop (driven by a fake stdout so the
marker-spoof-resistance and EOF contracts are proven offline), and the construction/teardown laziness
(no subprocess is spawned until the first ``run``). The real end-to-end contract — a live container, a
persistent shell, state persistence, the timeout reset — lives in the ``@skipif``-guarded
``tests/integration/test_docker_executor.py`` (it needs a real daemon and SKIPs cleanly without one).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from decode.sandbox.docker_executor import (
    _DAEMON_LOST_EXIT,
    _SHELL_ENDED_EXIT,
    DockerExecutor,
    _build_payload,
    _make_marker,
    _parse_exit_code,
    _read_until_marker,
    _recover_stdout,
)


class _FakeReader:
    """A minimal stdout stand-in: ``readline`` drains queued lines, then yields ``b""`` (EOF)."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _RaisingReader:
    """A stdout stand-in whose ``readline`` raises — models an oversized-line buffer overrun."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def readline(self) -> bytes:
        raise self._error


class _FakeShell:
    """A process stand-in exposing just the ``.stdout`` the read loop touches."""

    def __init__(self, reader: _FakeReader | _RaisingReader) -> None:
        self.stdout = reader
        self.returncode: int | None = None


# --- marker + payload helpers ----------------------------------------------------------------


def test_make_marker_has_the_expected_shape():
    marker = _make_marker()

    assert marker.startswith("__DECODE_END_")
    assert marker.endswith("__")


def test_make_marker_is_unique_per_call():
    # Uniqueness (a fresh uuid4 each call) is what makes the marker unspoofable by command output.
    assert _make_marker() != _make_marker()


def test_build_payload_wraps_the_command_in_a_stdin_starved_group():
    payload = _build_payload("echo hi", "MARKER")

    # The command runs in a brace group with stdin from /dev/null (so a stdin-reader sees EOF, not the
    # marker printf), and the marker printf — inside the group, leading with \n so the marker lands on
    # its own line even for no-trailing-newline output — emits `<marker> $?` after the command's output.
    assert payload == b'{ echo hi\nprintf \'\\n%s %s\\n\' "MARKER" "$?"\n} </dev/null\n'


def test_recover_stdout_strips_exactly_one_trailing_newline():
    # The marker printf's leading \n means the collected bytes are always the real output plus one \n.
    assert (
        _recover_stdout(b"hi\n") == "hi"
    )  # echo -n hi: no-trailing-newline output recovered exactly
    assert (
        _recover_stdout(b"hi\n\n") == "hi\n"
    )  # echo hi: a real trailing newline is kept (no double)
    assert _recover_stdout(b"\n") == ""  # empty output → empty string
    assert _recover_stdout(b"") == ""  # nothing collected stays empty
    assert _recover_stdout(b"hi") == "hi"  # defensive: no trailing \n → no-op, never over-strips
    assert _recover_stdout(b"caf\xc3\xa9\n") == "café"  # valid UTF-8 preserved through the strip


def test_parse_exit_code_reads_a_zero_status():
    assert _parse_exit_code(b"MARKER 0\n", "MARKER") == 0


def test_parse_exit_code_reads_a_non_zero_status():
    assert _parse_exit_code(b"MARKER 127\n", "MARKER") == 127


def test_parse_exit_code_falls_back_to_a_sentinel_when_malformed():
    # A marker line without a trailing int should not crash the executor.
    assert _parse_exit_code(b"MARKER notanint\n", "MARKER") == _SHELL_ENDED_EXIT


# --- read-until-marker loop (the command protocol, proven without docker) ---------------------


async def test_read_until_marker_collects_output_and_returns_exit_code():
    marker = _make_marker()
    shell = _FakeShell(_FakeReader([b"hello\n", b"world\n", f"{marker} 0\n".encode()]))
    out: list[bytes] = []

    exit_code = await _read_until_marker(shell, marker, out)  # type: ignore[arg-type]

    assert exit_code == 0
    assert out == [b"hello\n", b"world\n"]  # everything before the marker line, marker excluded


async def test_read_until_marker_reports_a_non_zero_exit():
    marker = _make_marker()
    shell = _FakeShell(_FakeReader([f"{marker} 3\n".encode()]))
    out: list[bytes] = []

    assert await _read_until_marker(shell, marker, out) == 3  # type: ignore[arg-type]


async def test_read_until_marker_ignores_marker_like_output_not_at_line_start():
    # A command that PRINTS a marker-like token mid-line must not truncate the read: only a line that
    # *starts* with our exact per-call marker ends it. This is the spoof-resistance contract, offline.
    marker = _make_marker()
    shell = _FakeShell(_FakeReader([b"echo __DECODE_END_fake__ here\n", f"{marker} 0\n".encode()]))
    out: list[bytes] = []

    exit_code = await _read_until_marker(shell, marker, out)  # type: ignore[arg-type]

    assert exit_code == 0
    assert out == [b"echo __DECODE_END_fake__ here\n"]


async def test_read_until_marker_ignores_a_different_marker_at_line_start():
    # Even a line that starts with a *different* __DECODE_END_ marker (not this call's uuid) is output,
    # not the sentinel — the real exit code comes from our own marker line.
    marker = _make_marker()
    shell = _FakeShell(_FakeReader([b"__DECODE_END_other__ 7\n", f"{marker} 0\n".encode()]))
    out: list[bytes] = []

    exit_code = await _read_until_marker(shell, marker, out)  # type: ignore[arg-type]

    assert exit_code == 0
    assert out == [b"__DECODE_END_other__ 7\n"]


async def test_no_newline_output_recovers_faithfully_via_the_marker_and_strip():
    # Regression (blocker): `echo -n hi` output has NO trailing newline. With the marker printf's
    # leading \n the shell emits `hi\n<marker> 0\n`, so readline sees ["hi\n", "<marker> 0\n"]: the
    # marker is on its own line (detected), the read collects ["hi\n"], and the one-newline strip in
    # _recover_stdout recovers "hi" exactly — no hang, no spurious timeout.
    marker = _make_marker()
    shell = _FakeShell(_FakeReader([b"hi\n", f"{marker} 0\n".encode()]))
    out: list[bytes] = []

    exit_code = await _read_until_marker(shell, marker, out)  # type: ignore[arg-type]

    assert exit_code == 0
    assert out == [b"hi\n"]  # the one line before the marker
    assert (
        _recover_stdout(b"".join(out)) == "hi"
    )  # the trailing newline the printf added is stripped


async def test_trailing_newline_output_is_not_double_stripped():
    # `echo hi` output DOES end in \n: the shell emits `hi\n\n<marker> 0\n`, so readline sees
    # ["hi\n", "\n", "<marker> 0\n"]. The single strip yields "hi\n" — byte-identical to before the
    # fix, so commands with a real trailing newline (and the existing integration tests) stay green.
    marker = _make_marker()
    shell = _FakeShell(_FakeReader([b"hi\n", b"\n", f"{marker} 0\n".encode()]))
    out: list[bytes] = []

    exit_code = await _read_until_marker(shell, marker, out)  # type: ignore[arg-type]

    assert exit_code == 0
    assert _recover_stdout(b"".join(out)) == "hi\n"


async def test_read_until_marker_returns_none_on_eof_before_marker():
    # The shell closed before its marker (e.g. the command exited the shell): the caller resets it.
    marker = _make_marker()
    shell = _FakeShell(_FakeReader([b"partial\n"]))
    out: list[bytes] = []

    exit_code = await _read_until_marker(shell, marker, out)  # type: ignore[arg-type]

    assert exit_code is None
    assert out == [b"partial\n"]  # the partial output read before EOF is preserved


async def test_read_until_marker_returns_none_on_an_oversized_line():
    # A single line overrunning the stream buffer loses marker sync → None (caller resets the shell).
    marker = _make_marker()
    shell = _FakeShell(_RaisingReader(ValueError("Separator is not found, chunk exceed the limit")))
    out: list[bytes] = []

    assert await _read_until_marker(shell, marker, out) is None  # type: ignore[arg-type]


# --- construction / teardown laziness (no subprocess without a run) ---------------------------


async def test_construction_starts_no_container_or_shell(mocker):
    spawn = mocker.patch("asyncio.create_subprocess_exec")

    executor = DockerExecutor()

    assert executor._container_id is None
    assert executor._shell is None
    spawn.assert_not_called()  # nothing runs until the first run()


async def test_aclose_is_a_safe_noop_when_never_started(mocker):
    spawn = mocker.patch("asyncio.create_subprocess_exec")
    executor = DockerExecutor()

    await executor.aclose()
    await executor.aclose()  # double aclose must not raise

    spawn.assert_not_called()  # no container was started, so none is torn down


def test_read_loop_survives_a_module_import_without_docker():
    # Importing the executor module (and constructing it) must not require docker — the whole point of
    # the hermetic split. A trivial guard that the symbols above imported and are callable.
    assert asyncio.iscoroutinefunction(DockerExecutor.run)
    assert asyncio.iscoroutinefunction(DockerExecutor.aclose)


# --- loop-independent teardown (the headline reap bug), proven with a real subprocess, no docker ------


def _pid_alive(pid: int) -> bool:
    """True while ``pid`` names a live process; False once it is gone (fully reaped, no zombie)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _spawn_sleeper() -> asyncio.subprocess.Process:
    """A real, loop-bound child that stands in for the docker-exec shell (own session → killpg reaches it).

    Spawned with the same pipe + ``start_new_session`` shape ``_ensure_shell`` uses, so it exercises the
    identical loop-bound transports + process-group teardown — the only difference is ``sleep`` instead of
    ``docker exec`` (so the test needs no daemon).
    """
    return await asyncio.create_subprocess_exec(
        "sleep",
        "30",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )


def test_aclose_reaps_a_loop_bound_shell_from_a_fresh_closed_loop():
    # THE headline regression (task 074, ADR-0011 §4). The headless runtime reaps the executor on a FRESH
    # event loop (``_reap_runtime_executor``) while the persistent shell subprocess was created on a kitaru
    # per-call loop that has since CLOSED. The old aclose awaited the shell's loop-bound futures/transports
    # there and raised ``RuntimeError: Event loop is closed`` — which escaped and left the container
    # running (the leaked-container headline). This reproduces that exact two-loop bridge hermetically with
    # a real ``sleep`` child (NO docker) and asserts aclose neither raises NOR leaves the process alive.
    # A loop-agnostic ``AsyncMock`` (the round-1 spy) passes on the buggy code, so only a real loop-bound
    # child guards this. ``_container_id`` stays None so only the loop-free shell teardown runs (no docker).
    executor = DockerExecutor()

    loop1 = asyncio.new_event_loop()
    shell = loop1.run_until_complete(_spawn_sleeper())
    pid = shell.pid
    assert _pid_alive(pid)  # sanity: the child is running before teardown

    executor._shell = shell
    executor._shell_loop = loop1
    executor._container_id = None  # exercise only the shell teardown path — no ``docker rm``
    loop1.close()  # the per-call loop is gone: awaiting the shell's futures here would now raise

    loop2 = asyncio.new_event_loop()
    try:
        loop2.run_until_complete(
            executor.aclose()
        )  # the buggy code raised "Event loop is closed" here
    finally:
        loop2.close()

    assert not _pid_alive(pid)  # the child was killed loop-free — not leaked
    assert executor._shell is None  # handles cleared so a double-close / later run is safe
    assert executor._shell_loop is None


async def test_aclose_cleanly_reaps_a_same_loop_shell():
    # The interactive-exit branch (REPL path): when aclose runs on the SAME loop the shell was created on,
    # it awaits a clean teardown (``_teardown_shell_clean`` — SIGTERM→SIGKILL, drain, close). Proven with a
    # real child on the running pytest loop so both aclose branches (same-loop clean vs cross-loop
    # loop-free) have a hermetic guard. ``_container_id`` stays None so no docker is needed.
    executor = DockerExecutor()
    shell = await _spawn_sleeper()
    pid = shell.pid
    assert _pid_alive(pid)

    executor._shell = shell
    executor._shell_loop = asyncio.get_running_loop()  # same loop aclose will run on
    executor._container_id = None

    await executor.aclose()

    assert not _pid_alive(pid)  # cleanly killed + reaped, no leak
    assert executor._shell is None
    assert executor._shell_loop is None


# --- daemon-death mid-session → a rendered failure, never a crash (the secondary bug) -----------------


async def test_run_returns_a_rendered_failure_when_the_container_cannot_start(mocker):
    # Regression (task 074 secondary): if the docker daemon goes away mid-session (Docker Desktop quit),
    # ``docker run`` / ``docker exec`` fail and _ensure_container/_ensure_shell raise. run() must CATCH the
    # known infra exceptions and return a rendered ExecResult (exit 125 + a daemon-lost note + the failure
    # text on stderr) so the model reacts — never let the exception escape and crash the bash tool.
    executor = DockerExecutor()
    mocker.patch.object(
        executor,
        "_ensure_container",
        side_effect=RuntimeError(
            "docker run failed (exit 1): Cannot connect to the Docker daemon at "
            "unix:///var/run/docker.sock. Is the docker daemon running?"
        ),
    )

    result = await executor.run("echo hi", cwd=Path("/repo"), timeout_s=30.0)

    assert (
        result.exit_code == _DAEMON_LOST_EXIT
    )  # 125 — docker's container-failed-to-run convention
    assert result.timed_out is False
    assert (
        "Docker daemon became unreachable" in result.note
    )  # the model is told the session was lost
    assert (
        "Cannot connect to the Docker daemon" in result.stderr
    )  # the underlying failure is surfaced
    assert (
        executor._container_id is None
    )  # the stale session is discarded so a later run re-attempts
    assert executor._shell is None


async def test_run_survives_a_missing_docker_binary(mocker):
    # OSError / FileNotFoundError on spawn (the ``docker`` CLI itself is gone) is an infra failure too —
    # caught on the same path and rendered, not raised. FileNotFoundError is an OSError subclass.
    executor = DockerExecutor()
    mocker.patch.object(executor, "_ensure_container", side_effect=FileNotFoundError("docker"))

    result = await executor.run("echo hi", cwd=Path("/repo"), timeout_s=30.0)

    assert result.exit_code == _DAEMON_LOST_EXIT
    assert result.note  # the daemon-lost note is set (never an empty, silent failure)


async def test_run_lets_an_unexpected_error_surface(mocker):
    # The daemon-death catch is scoped to the KNOWN infra exceptions (RuntimeError / OSError) — a genuine
    # bug (here a ValueError) must still surface as a crash, not be swallowed into a fake ExecResult that
    # would hide the defect from the model and the logs.
    executor = DockerExecutor()
    mocker.patch.object(executor, "_ensure_container", side_effect=ValueError("a real bug"))

    with pytest.raises(ValueError, match="a real bug"):
        await executor.run("echo hi", cwd=Path("/repo"), timeout_s=30.0)
