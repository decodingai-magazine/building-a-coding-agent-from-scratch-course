"""Real-modal integration tests for the fresh-exec Modal sandbox (ADR-0012 §2,4,5).

The living proof that the unified :class:`~decode.sandbox.executor.SandboxExecutor` over a
:class:`~decode.sandbox.modal_backend.ModalBackend` holds against a **real modal account**: one
session-persistent remote sandbox, a **fresh** ``sb.exec`` per call (so ``cd`` / ``export`` do NOT
persist, but the filesystem does), the ONE bootstrap upload of the host Workspace at create, **direct
SandboxFilesystem file ops** against the remote (a file ``bash`` writes is returned by ``read_bytes``
with no mirror; a ``remove`` is reflected by a later ``stat`` — no deletion-blindness), the per-exec
timeout that kills only the command (sandbox + fs survive), the ONE export sweep back to the host, sandbox
teardown, and revival of a remotely-ended sandbox.

**Skipped, never failed, without credentials.** A module-level presence check (the task-071 predicate:
the ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET`` pair, or a ``~/.modal.toml``) guards the whole file with
``@pytest.mark.skipif``, so ``make ci`` stays green on a machine with no modal account — these tests
SKIP. When credentials are present they run for real (a few cents of Modal compute). Each test reaps its
remote sandbox in the ``executor`` fixture's ``aclose`` ``finally`` — so the suite leaks no remote sandbox
and is hermetic under ``filterwarnings=error``.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from decode.config.settings import settings
from decode.sandbox.executor import FileStat, SandboxExecutor
from decode.sandbox.modal_backend import ModalBackend

# Modal's minimum sandbox lifetime is 10s (``timeout`` must be 10-86400s); a run at this floor lets the
# revival test wait out a REAL max-lifetime expiry (poll() reports it ~1s past the deadline) instead of
# an external terminate, which — verified on modal 1.5.1 — poll() does not reflect.
_MIN_SANDBOX_LIFETIME_S = 10
_EXPIRY_WAIT_S = 45.0


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

# A real remote sandbox cold-start (image pull + spawn) + bootstrap upload can take a while; give room.
_TIMEOUT_S = 120.0


@pytest.fixture
async def executor() -> AsyncIterator[SandboxExecutor]:
    """A fresh ``SandboxExecutor(ModalBackend())``; teardown terminates the remote sandbox (no leak)."""
    ex = SandboxExecutor(ModalBackend())
    try:
        yield ex
    finally:
        await ex.aclose()


def _host_workspace(tmp_path: Path) -> Path:
    """A populated host Workspace root with one marker file (bootstrap-upload source)."""
    workspace = tmp_path / ".decode" / "sandbox"
    workspace.mkdir(parents=True)
    (workspace / "marker.txt").write_text("bootstrapped\n", encoding="utf-8")
    return workspace


async def test_run_echo_round_trips_through_a_real_sandbox(
    executor: SandboxExecutor, tmp_path: Path
):
    result = await executor.run("echo hi", cwd=tmp_path, timeout_s=_TIMEOUT_S)

    assert result.stdout.strip() == "hi"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stderr == ""  # a bare echo writes nothing to stderr
    assert result.note == ""  # a normal command carries no out-of-band note


async def test_create_bootstrap_uploads_the_host_workspace_once(
    executor: SandboxExecutor, tmp_path: Path
):
    # ADR-0012 §5: the host Workspace (with its marker file) is uploaded into /workspace at create, so a
    # bash command sees it — proving the ONE tar bootstrap upload landed (no add_local_dir).
    workspace = _host_workspace(tmp_path)
    await executor.start(workspace)

    seen = await executor.run("cat /workspace/marker.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)

    assert seen.stdout.strip() == "bootstrapped"
    assert seen.exit_code == 0


async def test_file_ops_read_write_directly_against_the_remote(
    executor: SandboxExecutor, tmp_path: Path
):
    # ADR-0012 §4: file ops go straight through the SandboxFilesystem API — no host mirror, always
    # truthful. A file bash writes in /workspace is returned by read_bytes, and vice-versa.
    workspace = _host_workspace(tmp_path)
    await executor.start(workspace)
    backend = executor._backend

    # A file written by bash is visible via read_bytes (direct against the remote, no mirror) ...
    await executor.run("echo from-bash > b.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert (await backend.read_bytes("b.txt")).decode().strip() == "from-bash"

    # ... and a file written via write_bytes is visible to bash.
    await backend.write_bytes("sub/w.txt", b"from-fileop\n")
    cat = await executor.run("cat sub/w.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert cat.stdout.strip() == "from-fileop"

    # stat / make_directory / list_dir round-trip against the live remote fs.
    await backend.make_directory("made")
    stat = await backend.stat("b.txt")
    assert isinstance(stat, FileStat) and stat.is_dir is False and stat.size > 0
    names = {entry.path for entry in await backend.list_dir("")}
    assert {"b.txt", "sub", "made"} <= names


async def test_remove_is_reflected_by_a_later_stat_no_deletion_blindness(
    executor: SandboxExecutor, tmp_path: Path
):
    # THE rejected-mirror proof (ADR-0012 §4): because file ops are direct, a remove is immediately
    # reflected — stat returns None and read_bytes raises FileNotFoundError. A stale mirror could not.
    workspace = _host_workspace(tmp_path)
    await executor.start(workspace)
    backend = executor._backend

    await backend.write_bytes("doomed.txt", b"bye")
    assert await backend.stat("doomed.txt") is not None  # it exists ...

    await backend.remove("doomed.txt")

    assert await backend.stat("doomed.txt") is None  # ... and the deletion is seen immediately
    with pytest.raises(FileNotFoundError):
        await backend.read_bytes("doomed.txt")
    # bash agrees — the remote really lost the file.
    gone = await executor.run(
        "test -e doomed.txt && echo present || echo gone", cwd=tmp_path, timeout_s=_TIMEOUT_S
    )
    assert gone.stdout.strip() == "gone"


async def test_filesystem_persists_but_cd_and_export_do_not(
    executor: SandboxExecutor, tmp_path: Path
):
    # Fresh-exec (ADR-0012 §2): the filesystem persists across run()s (one sandbox), but each command is
    # a FRESH process, so ``cd`` / ``export`` do NOT carry over.
    await executor.run(
        "echo kept > f.txt && export DECODE_X=42 && cd /tmp", cwd=tmp_path, timeout_s=_TIMEOUT_S
    )

    persisted = await executor.run("cat f.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert persisted.stdout.strip() == "kept"  # the file survived — fs persists

    fresh = await executor.run("echo [$DECODE_X] && pwd", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert "[]" in fresh.stdout  # DECODE_X did NOT carry over (fresh process)
    assert "/workspace" in fresh.stdout  # cwd is /workspace again, NOT /tmp (fresh exec)


async def test_binary_output_does_not_crash(executor: SandboxExecutor, tmp_path: Path):
    # Regression (blocking): non-UTF-8 output must NOT crash run(). Raw random bytes over the real stream
    # (text=False + errors="replace") come back as a str, never a UnicodeDecodeError.
    result = await executor.run("head -c 16 /dev/urandom", cwd=tmp_path, timeout_s=_TIMEOUT_S)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert isinstance(result.stdout, str)  # decoded (replacement chars), not a crash


async def test_timeout_kills_the_exec_but_the_sandbox_and_fs_survive(
    executor: SandboxExecutor, tmp_path: Path
):
    # A per-exec timeout kills the command but leaves the sandbox (and its fs) alive, with NO reset note.
    await executor.run("echo survivor > keep.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)

    timed = await executor.run("sleep 100", cwd=tmp_path, timeout_s=1.0)
    assert timed.timed_out is True
    assert timed.note == ""  # unlike docker's retired shell reset — only the exec died

    after = await executor.run("cat keep.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert after.stdout.strip() == "survivor"  # the fs survived the timeout
    assert after.exit_code == 0
    assert after.timed_out is False


async def test_export_sweeps_the_workspace_to_the_host_and_leaves_the_sandbox_alive(
    executor: SandboxExecutor, tmp_path: Path
):
    # ADR-0012 §5,8: export sweeps /workspace → host .decode/sandbox (standalone /ship), leaving the
    # sandbox alive. A file created only in the remote /workspace appears host-side after export.
    workspace = _host_workspace(tmp_path)
    await executor.start(workspace)
    await executor.run("echo shipped > only-remote.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert not (workspace / "only-remote.txt").exists()  # not host-visible yet

    await executor.export()  # the standalone sweep

    assert (workspace / "only-remote.txt").read_text(encoding="utf-8").strip() == "shipped"
    # The sandbox stayed alive — a follow-up command still works on the same fs.
    alive = await executor.run("cat only-remote.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert alive.stdout.strip() == "shipped"


async def test_aclose_exports_then_terminates_the_sandbox(
    executor: SandboxExecutor, tmp_path: Path
):
    # aclose = export + destroy (ADR-0012 §2): a file created in /workspace lands host-side (the export
    # sweep) AND the remote sandbox is terminated (a later run creates a FRESH one — no leak).
    workspace = _host_workspace(tmp_path)
    await executor.start(workspace)
    await executor.run("echo final > result.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    first_id = executor._backend._sandbox.object_id

    await executor.aclose()

    assert (workspace / "result.txt").read_text(encoding="utf-8").strip() == "final"  # exported
    assert executor._backend._sandbox is None  # terminated + cleared
    await executor.aclose()  # a double aclose must not raise

    # A run after aclose creates a FRESH sandbox (the old one was terminated).
    revived = await executor.run("echo again", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert revived.stdout.strip() == "again"
    assert executor._backend._sandbox.object_id != first_id


async def test_a_max_lifetime_expiry_is_recreated_and_rebootstrapped(
    executor: SandboxExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # ADR-0012 revival ceiling: a sandbox that ended by MAX-LIFETIME EXPIRY (the real poll()-dead signal,
    # forced here with the 10s minimum lifetime) is recreated + RE-BOOTSTRAPPED from the host state on
    # the next run, with a one-shot note that the workspace was restored from the last local state (and
    # later changes may be lost). This is the reliable expiry path — an external terminate is NOT
    # reflected by poll() on real modal, so it is not used.
    monkeypatch.setattr(settings, "sandbox_timeout_s", _MIN_SANDBOX_LIFETIME_S)
    workspace = _host_workspace(tmp_path)
    await executor.start(workspace)
    backend = executor._backend
    first_id = backend._sandbox.object_id

    # Wait out the real max-lifetime expiry: poll() reports the exit code ~1s past the 10s deadline.
    deadline = time.monotonic() + _EXPIRY_WAIT_S
    while time.monotonic() < deadline:
        if await backend._sandbox.poll.aio() is not None:
            break
        await asyncio.sleep(1.0)
    else:
        pytest.fail("the modal sandbox did not hit its max-lifetime expiry within the wait window")

    revived = await executor.run("cat /workspace/marker.txt", cwd=tmp_path, timeout_s=_TIMEOUT_S)

    assert backend._sandbox.object_id != first_id  # a fresh sandbox replaced the expired one
    assert revived.stdout.strip() == "bootstrapped"  # re-bootstrapped from the host state
    assert "restored from the last local state" in revived.note  # the honest restore note ...
    assert "may be lost" in revived.note  # ... with the in-flight-loss caveat


async def test_a_file_op_on_an_expired_sandbox_revives_and_re_bootstraps(
    executor: SandboxExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # ADR-0012 §4 revival on the FILE-OP path (the fix for the "exec revives but file ops crash" gap): a
    # max-lifetime-expired sandbox makes a direct SandboxFilesystem op raise NotFoundError (a raw
    # GRPCError, NOT FileNotFoundError). The op must self-heal exactly like exec — recreate + re-bootstrap
    # from the host — and return the restored content, instead of leaking the raw type. The one-shot
    # restore note then rides the NEXT exec (file ops carry no note channel).
    monkeypatch.setattr(settings, "sandbox_timeout_s", _MIN_SANDBOX_LIFETIME_S)
    workspace = _host_workspace(tmp_path)
    await executor.start(workspace)
    backend = executor._backend
    first_id = backend._sandbox.object_id

    # Wait out the real max-lifetime expiry: poll() reports the exit code ~1s past the 10s deadline.
    deadline = time.monotonic() + _EXPIRY_WAIT_S
    while time.monotonic() < deadline:
        if await backend._sandbox.poll.aio() is not None:
            break
        await asyncio.sleep(1.0)
    else:
        pytest.fail("the modal sandbox did not hit its max-lifetime expiry within the wait window")

    # A FILE OP on the dead sandbox must revive (not crash with a raw NotFoundError) and return the
    # re-bootstrapped host content — the exact behaviour bash already had, now on read/write/edit's path.
    restored = await backend.read_bytes("marker.txt")

    assert restored.decode().strip() == "bootstrapped"  # re-bootstrapped from the host state
    assert backend._sandbox.object_id != first_id  # a fresh sandbox replaced the expired one
    assert backend._recreated is True  # the restore note is pending (a file op carries no note) ...

    # ... and it rides the NEXT exec's result, then clears (one-shot).
    after = await executor.run("echo ok", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert "restored from the last local state" in after.note
    assert "may be lost" in after.note
    again = await executor.run("echo ok", cwd=tmp_path, timeout_s=_TIMEOUT_S)
    assert again.note == ""  # the flag was one-shot
