"""Real-docker integration tests for :class:`DockerExecutor` (ADR-0011 §2).

The living proof that the Docker sandbox executor's contract holds against a **real docker daemon**: a
session-persistent container, one long-lived bash shell (so ``cd`` / ``export`` persist across
``run`` calls), the unique-marker command protocol (spoof-resistant), non-zero exit reporting, the
timeout → kill-and-reset-the-shell rule, container teardown, and the observability log lines.

**Skipped, never failed, without a daemon.** A module-level ``docker info`` probe guards the whole
file with ``@pytest.mark.skipif`` (mirroring the LSP capstone's ``ty``-binary guard), so ``make ci``
stays green on a machine with no Docker — the docker tests SKIP. When a daemon is up they run for
real. Each test gets a fresh container via the ``executor`` fixture, whose teardown ``aclose()`` reaps
the shell subprocess and removes the container — so the suite is hermetic under ``filterwarnings=error``
(no unclosed-subprocess ``ResourceWarning``).
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from decode.sandbox.docker_executor import DockerExecutor


def _docker_available() -> bool:
    """True if a local docker daemon answers a fast ``docker info`` probe (else the file SKIPs)."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5.0, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


_DOCKER_AVAILABLE = _docker_available()

pytestmark = pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="the docker daemon is not reachable")


@pytest.fixture
async def executor() -> AsyncIterator[DockerExecutor]:
    """A fresh :class:`DockerExecutor`; teardown reaps the shell + removes the container (no leak)."""
    ex = DockerExecutor()
    try:
        yield ex
    finally:
        await ex.aclose()


def _container_exists(container_id: str) -> bool:
    """True while the daemon still lists a container with ``container_id`` (used to prove removal)."""
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"id={container_id}"],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    return bool(result.stdout.strip())


def _wait_until_gone(container_id: str, timeout_s: float = 5.0) -> bool:
    """Poll (bounded) until the container is no longer listed; return whether it is gone."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _container_exists(container_id):
            return True
        time.sleep(0.2)
    return not _container_exists(container_id)


async def test_run_echo_round_trips_through_a_real_container(
    executor: DockerExecutor, tmp_path: Path
):
    result = await executor.run("echo hi", cwd=tmp_path, timeout_s=30.0)

    assert "hi" in result.stdout
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stderr == ""  # streams are merged into stdout
    assert result.note == ""  # a normal command carries no out-of-band note


async def test_shell_state_persists_across_run_calls(executor: DockerExecutor, tmp_path: Path):
    # The persistent-shell mechanism (not a per-call ``docker exec``): cd/export survive between runs.
    await executor.run("export DECODE_X=42 && cd /tmp", cwd=tmp_path, timeout_s=30.0)

    result = await executor.run("echo $DECODE_X && pwd", cwd=tmp_path, timeout_s=30.0)

    assert "42" in result.stdout
    assert "/tmp" in result.stdout


async def test_marker_protocol_resists_a_spoofed_marker_in_output(
    executor: DockerExecutor, tmp_path: Path
):
    # A command that prints a marker-like line must not truncate the read: the per-call uuid marker is
    # unguessable, so the printed fake is captured as output and the real exit code (0) is returned.
    result = await executor.run("echo '__DECODE_END_deadbeef__ 999'", cwd=tmp_path, timeout_s=30.0)

    assert "__DECODE_END_deadbeef__ 999" in result.stdout
    assert result.exit_code == 0  # NOT 999 — the spoofed line's trailing int is not trusted


async def test_run_reports_a_non_zero_exit_code(executor: DockerExecutor, tmp_path: Path):
    result = await executor.run("false", cwd=tmp_path, timeout_s=30.0)

    assert result.exit_code != 0
    assert result.timed_out is False


async def test_run_handles_output_without_a_trailing_newline(
    executor: DockerExecutor, tmp_path: Path
):
    # Regression (blocker): a command whose stdout lacks a trailing newline used to hang for the full
    # timeout and falsely report timed_out=True — the marker printf concatenated onto the command's
    # last output line, so the marker was never detected. The leading-newline printf fixes it.
    await executor.run("true", cwd=tmp_path, timeout_s=30.0)  # warm the container/shell first

    start = time.monotonic()
    result = await executor.run("echo -n hi", cwd=tmp_path, timeout_s=10.0)
    elapsed = time.monotonic() - start

    assert result.stdout == "hi"  # exact: no marker garbage, no spurious trailing newline
    assert result.exit_code == 0
    assert result.timed_out is False  # the bug reported True after a full-timeout hang
    assert elapsed < 5.0  # fast — did not hang to the 10s deadline

    # A second no-trailing-newline shape (printf) recovers exactly too.
    again = await executor.run("printf 'x'", cwd=tmp_path, timeout_s=10.0)
    assert again.stdout == "x"
    assert again.exit_code == 0
    assert again.timed_out is False


async def test_run_starves_stdin_readers_instead_of_hanging(
    executor: DockerExecutor, tmp_path: Path
):
    # Regression (secondary): a stdin-reading command (bare `cat`) used to consume the marker printf
    # from the shell's shared stdin pipe → the marker was eaten and the read hung to the timeout. Each
    # command now runs in a brace group with stdin from /dev/null, so `cat` sees EOF and returns fast.
    await executor.run("true", cwd=tmp_path, timeout_s=30.0)  # warm the container/shell first

    start = time.monotonic()
    result = await executor.run("cat", cwd=tmp_path, timeout_s=10.0)
    elapsed = time.monotonic() - start

    assert result.stdout == ""  # cat read EOF from /dev/null, not the marker printf
    assert result.exit_code == 0
    assert result.timed_out is False
    assert elapsed < 5.0  # did not hang to the deadline

    # A heredoc supplies its own stdin, so it overrides the group's /dev/null redirect and still works.
    heredoc = await executor.run("cat <<EOF\nl1\nl2\nEOF", cwd=tmp_path, timeout_s=10.0)
    assert heredoc.stdout == "l1\nl2\n"
    assert heredoc.exit_code == 0
    assert heredoc.timed_out is False


async def test_timeout_kills_and_resets_the_persistent_shell(
    executor: DockerExecutor, tmp_path: Path
):
    # Seed session state, then time out a hang: the shell is killed+respawned, so a later run sees a
    # fresh shell (env cleared, cwd back to /workspace) — proving the reset AND that no old shell leaks.
    await executor.run("export DECODE_Y=99 && cd /tmp", cwd=tmp_path, timeout_s=30.0)

    timed = await executor.run("sleep 100", cwd=tmp_path, timeout_s=1.0)

    assert timed.timed_out is True
    assert timed.note != ""  # the model is told its state was reset
    assert "reset" in timed.note.lower()

    after = await executor.run("echo [$DECODE_Y] && pwd", cwd=tmp_path, timeout_s=30.0)

    assert "[]" in after.stdout  # DECODE_Y is gone — the env was cleared by the respawn
    assert "/workspace" in after.stdout  # cwd reset to the container workdir
    assert after.timed_out is False


async def test_aclose_removes_the_container_and_is_idempotent(
    executor: DockerExecutor, tmp_path: Path
):
    await executor.run("echo start", cwd=tmp_path, timeout_s=30.0)
    container_id = executor._container_id
    assert container_id is not None
    assert _container_exists(container_id)

    await executor.aclose()

    assert _wait_until_gone(container_id), "aclose() must stop and remove the session container"
    await executor.aclose()  # a double aclose() must not raise


async def test_observability_logs_container_lifecycle_and_each_command(
    executor: DockerExecutor, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.DEBUG, logger="decode.sandbox.docker_executor"):
        await executor.run("echo observ", cwd=tmp_path, timeout_s=30.0)
        container_id = executor._container_id
        assert container_id is not None
        await executor.aclose()

    text = caplog.text
    # Container start: id + image at INFO.
    assert f"[sandbox] docker start {container_id}" in text
    assert "image=python:3.12-slim" in text
    # Per command: the command, its exit code, and the byte count at DEBUG (never the output itself).
    assert "[sandbox] $ echo observ" in text
    assert "exit=0" in text
    assert "bytes=" in text
    # Teardown: the container stop at INFO.
    assert f"[sandbox] docker stop {container_id}" in text
