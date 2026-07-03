"""Real-docker integration tests for the fresh-exec Docker sandbox (ADR-0012 §2,4).

The living proof that the unified :class:`~decode.sandbox.executor.SandboxExecutor` over a
:class:`~decode.sandbox.docker_backend.DockerBackend` holds against a **real docker daemon**: one
session container, a **fresh** ``docker exec`` per call (so ``cd`` / ``export`` do NOT persist, but the
filesystem does), separate stdout/stderr, the timeout that kills only the one command (container + fs
survive), the pathlib file ops on the bind-mounted Workspace (a file written by ``bash`` is visible via
``read_bytes`` and vice-versa — the mount is one truthful tree), container teardown, and the
``[sandbox]`` observability lines.

**Skipped, never failed, without a daemon.** A module-level ``docker info`` probe guards the whole
file with ``@pytest.mark.skipif`` (mirroring the LSP capstone's ``ty`` guard), so ``make ci`` stays
green on a machine with no Docker. Each test reaps its container in a ``finally`` (the ``executor``
fixture's ``aclose``), so the suite is hermetic under ``filterwarnings=error`` and leaves no docker
litter (cost hygiene: ``docker ps -a`` shows nothing leaked afterwards).
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from decode.sandbox.docker_backend import DockerBackend
from decode.sandbox.executor import FileStat, SandboxExecutor


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
async def executor() -> AsyncIterator[SandboxExecutor]:
    """A fresh ``SandboxExecutor(DockerBackend())``; teardown removes the container (no leak)."""
    ex = SandboxExecutor(DockerBackend())
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
    executor: SandboxExecutor, tmp_path: Path
):
    result = await executor.run("echo hi", cwd=tmp_path, timeout_s=30.0)

    assert "hi" in result.stdout
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.note == ""  # a normal command carries no out-of-band note


async def test_run_reports_a_non_zero_exit_code(executor: SandboxExecutor, tmp_path: Path):
    result = await executor.run("false", cwd=tmp_path, timeout_s=30.0)

    assert result.exit_code != 0
    assert result.timed_out is False


async def test_streams_are_kept_separate(executor: SandboxExecutor, tmp_path: Path):
    # Fresh ``docker exec`` keeps stdout/stderr split (unlike the retired merged shell, ADR-0012 §2).
    result = await executor.run("echo out; echo err >&2", cwd=tmp_path, timeout_s=30.0)

    assert "out" in result.stdout
    assert "err" in result.stderr
    assert "err" not in result.stdout  # not merged into stdout


async def test_filesystem_persists_but_cd_and_export_do_not(
    executor: SandboxExecutor, tmp_path: Path
):
    # THE deleted-persistent-shell proof (ADR-0012 §2). The filesystem persists across run() calls
    # (one container), but each command is a FRESH exec, so ``cd`` / ``export`` do NOT carry over.
    await executor.run(
        "echo kept > f.txt && export DECODE_X=42 && cd /tmp", cwd=tmp_path, timeout_s=30.0
    )

    persisted = await executor.run("cat f.txt", cwd=tmp_path, timeout_s=30.0)
    assert persisted.stdout.strip() == "kept"  # the file survived — fs persists

    fresh = await executor.run("echo [$DECODE_X] && pwd", cwd=tmp_path, timeout_s=30.0)
    assert "[]" in fresh.stdout  # DECODE_X did NOT carry over (fresh process)
    assert "/workspace" in fresh.stdout  # cwd is /workspace again, NOT /tmp (fresh exec)


async def test_timeout_kills_the_command_but_the_container_and_fs_survive(
    executor: SandboxExecutor, tmp_path: Path
):
    # A timeout kills only the one command; the container and a previously-written file survive, and
    # NO shell-reset note is set (fresh-exec — nothing session-level was lost).
    await executor.run("echo survivor > keep.txt", cwd=tmp_path, timeout_s=30.0)
    container_id = executor._backend._container_id
    assert container_id is not None

    timed = await executor.run("sleep 100", cwd=tmp_path, timeout_s=1.0)
    assert timed.timed_out is True
    assert timed.note == ""  # no reset note — only the command died

    assert _container_exists(container_id)  # the container is still up
    after = await executor.run("cat keep.txt", cwd=tmp_path, timeout_s=30.0)
    assert after.stdout.strip() == "survivor"  # the fs survived
    assert after.timed_out is False


async def test_file_ops_and_bash_share_one_truthful_tree(executor: SandboxExecutor, tmp_path: Path):
    # The bind mount makes the host Workspace BE the sandbox fs, so backend file ops and ``bash`` see
    # exactly the same tree (ADR-0012 §4). Warm the container first so the workspace + mount exist.
    await executor.run("true", cwd=tmp_path, timeout_s=30.0)
    backend = executor._backend

    # A file written by bash is visible via read_bytes ...
    await executor.run("echo from-bash > b.txt", cwd=tmp_path, timeout_s=30.0)
    assert (await backend.read_bytes("b.txt")).decode().strip() == "from-bash"

    # ... and a file written via write_bytes is visible to bash.
    await backend.write_bytes("sub/w.txt", b"from-fileop\n")
    cat = await executor.run("cat sub/w.txt", cwd=tmp_path, timeout_s=30.0)
    assert cat.stdout.strip() == "from-fileop"

    # make_directory / stat / list_dir / remove round-trip against the live mount.
    await backend.make_directory("made")
    assert (
        await executor.run("test -d made && echo yes", cwd=tmp_path, timeout_s=30.0)
    ).stdout.strip() == "yes"
    stat = await backend.stat("b.txt")
    assert isinstance(stat, FileStat) and stat.is_dir is False and stat.size > 0
    names = {entry.path for entry in await backend.list_dir("")}
    assert {"b.txt", "sub", "made"} <= names
    await backend.remove("b.txt")
    assert (
        await executor.run(
            "test -e b.txt && echo present || echo gone", cwd=tmp_path, timeout_s=30.0
        )
    ).stdout.strip() == "gone"


async def test_aclose_removes_the_container_and_is_idempotent(
    executor: SandboxExecutor, tmp_path: Path
):
    await executor.run("echo start", cwd=tmp_path, timeout_s=30.0)
    container_id = executor._backend._container_id
    assert container_id is not None
    assert _container_exists(container_id)

    await executor.aclose()

    assert _wait_until_gone(container_id), "aclose() must stop and remove the session container"
    await executor.aclose()  # a double aclose() must not raise


async def test_observability_logs_container_lifecycle_and_each_command(
    executor: SandboxExecutor, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.DEBUG, logger="decode.sandbox.docker_backend"):
        await executor.run("echo observ", cwd=tmp_path, timeout_s=30.0)
        container_id = executor._backend._container_id
        assert container_id is not None
        await executor.aclose()

    text = caplog.text
    # Container start: id + image at INFO.
    assert f"[sandbox] docker start {container_id}" in text
    assert "image=ghcr.io/astral-sh/uv:python3.12-bookworm-slim" in text
    # Per command: the command + exit + byte count at DEBUG (never the output itself).
    assert "echo observ" in text
    assert "exit=0" in text
    assert "bytes=" in text
    # Teardown: the container stop at INFO.
    assert f"[sandbox] docker stop {container_id}" in text
