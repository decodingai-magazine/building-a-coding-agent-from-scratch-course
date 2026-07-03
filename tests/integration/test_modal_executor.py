"""Real-modal integration tests for :class:`ModalExecutor` (ADR-0011 §3).

The living proof that the Modal sandbox executor's contract holds against a **real modal account**: a
session-persistent remote sandbox, empty ``/workspace`` scratch (no local-tree sync), filesystem
persistence across ``run`` calls, the per-exec timeout that kills the command but keeps the sandbox,
sandbox teardown, and the observability log lines.

**Skipped, never failed, without credentials.** A module-level presence check (the task-071 predicate:
the ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET`` pair, or a ``~/.modal.toml``) guards the whole file
with ``@pytest.mark.skipif``, so ``make ci`` stays green on a machine with no modal account — these
tests SKIP. When credentials are present they run for real (a few cents of Modal compute). Each test
gets a fresh sandbox via the ``executor`` fixture, whose teardown ``aclose()`` terminates it — so the
suite leaks no remote sandbox and is hermetic under ``filterwarnings=error``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from decode.sandbox.modal_executor import ModalExecutor


def _modal_credentials_present() -> bool:
    """True if modal account credentials are present (mirrors the task-071 startup guard predicate).

    Presence only, no network call and no ``modal`` import: the ``MODAL_TOKEN_ID`` +
    ``MODAL_TOKEN_SECRET`` account-token pair in the environment, or a ``~/.modal.toml`` written by
    ``modal token set``. A bad token fails at the first sandbox call — not this skip gate.
    """
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return (Path.home() / ".modal.toml").exists()


_MODAL_AVAILABLE = _modal_credentials_present()

pytestmark = pytest.mark.skipif(
    not _MODAL_AVAILABLE, reason="modal account credentials are not present"
)

# A real remote sandbox cold-start (image pull + spawn) can take a while; give each command room.
_TIMEOUT_S = 120.0


@pytest.fixture
async def executor() -> AsyncIterator[ModalExecutor]:
    """A fresh :class:`ModalExecutor`; teardown terminates the remote sandbox (no leak)."""
    ex = ModalExecutor()
    try:
        yield ex
    finally:
        await ex.aclose()


async def test_run_echo_round_trips_and_logs_create_and_command(
    executor: ModalExecutor, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.DEBUG, logger="decode.sandbox.modal_executor"):
        result = await executor.run("echo hi", cwd=tmp_path, timeout_s=_TIMEOUT_S)

    assert result.stdout.strip() == "hi"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stderr == ""  # a bare echo writes nothing to stderr
    assert result.note == ""  # a normal command carries no out-of-band note

    sandbox_id = executor._sandbox.object_id
    text = caplog.text
    assert f"[sandbox] modal create {sandbox_id}" in text  # id on create (INFO)
    assert "image=ghcr.io/astral-sh/uv:python3.12-bookworm-slim" in text  # image on create
    assert "[sandbox] $ echo hi" in text  # the command (DEBUG)
    assert "exit=0" in text
    assert "bytes=" in text  # byte count, never the output itself


async def test_filesystem_persists_and_the_local_tree_is_absent(
    executor: ModalExecutor, tmp_path: Path
):
    # One sandbox: a file written in one run() is readable in the next (fs persists across execs).
    await executor.run("echo data > /workspace/f.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)

    readback = await executor.run("cat /workspace/f.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert readback.stdout.strip() == "data"
    assert readback.exit_code == 0

    # Empty remote scratch: a file that exists in the host cwd (pyproject.toml) is NOT in the sandbox —
    # there is no local-tree sync. ``ls`` on it fails.
    host_file = await executor.run("ls pyproject.toml", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert host_file.exit_code != 0
    assert host_file.timed_out is False


async def test_binary_output_does_not_crash(executor: ModalExecutor, tmp_path: Path):
    # Regression (blocking): non-UTF-8 output must NOT crash run(). Raw random bytes over the real
    # stream (text=False + errors="replace") come back as a str, never a UnicodeDecodeError. Also
    # exercises stderr bytes on the same sandbox.
    result = await executor.run("head -c 16 /dev/urandom", cwd=tmp_path, timeout_s=_TIMEOUT_S)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert isinstance(result.stdout, str)  # decoded (replacement chars), not a crash

    err = await executor.run("head -c 16 /dev/urandom >&2", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert err.exit_code == 0
    assert isinstance(err.stderr, str)  # stderr bytes decode with replace too


async def test_timeout_kills_the_exec_not_the_sandbox(executor: ModalExecutor, tmp_path: Path):
    # A per-exec timeout kills the command but leaves the sandbox (and its fs) alive.
    timed = await executor.run("sleep 100", cwd=tmp_path, timeout_s=1.0)

    assert timed.timed_out is True
    assert timed.note == ""  # unlike docker, no session reset — the sandbox persists

    after = await executor.run("echo alive", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert after.stdout.strip() == "alive"  # the same sandbox still works
    assert after.exit_code == 0
    assert after.timed_out is False


async def test_aclose_terminates_the_sandbox_and_is_idempotent(
    executor: ModalExecutor, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    await executor.run("echo start", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    first_id = executor._sandbox.object_id

    with caplog.at_level(logging.INFO, logger="decode.sandbox.modal_executor"):
        await executor.aclose()

    assert f"[sandbox] modal terminate {first_id}" in caplog.text  # terminate logged (INFO)
    assert executor._sandbox is None  # state cleared
    await executor.aclose()  # a double aclose() must not raise

    # A run after aclose() creates a FRESH sandbox (the old one was terminated + cleared).
    revived = await executor.run("echo again", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert revived.stdout.strip() == "again"
    assert executor._sandbox.object_id != first_id
