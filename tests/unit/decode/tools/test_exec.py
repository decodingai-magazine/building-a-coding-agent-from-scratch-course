"""Unit tests for the command-executor seam (``decode.tools.exec``).

ADR-0002 §7,10: ``bash`` runs commands through a :class:`CommandExecutor`; M1 ships
:class:`LocalExecutor` (a local ``asyncio`` subprocess), and M8 swaps a sandbox in behind the
same ``run`` method. These tests pin the executor's contract directly — no model, no agent —
with **real short commands** so they are hermetic and fast:

* stdout / stderr / exit-code capture for a normal command;
* a non-zero exit reported faithfully;
* **a timeout kills the process *and a child it spawned*** (the orphaned child must not keep
  running after the deadline) and returns ``timed_out=True`` with partial output;
* undecodable bytes do not crash the decode path.

The timeout tests use a tiny ``0.2s`` deadline and a child that would otherwise outlive it, so
the "no orphaned process" guarantee is provable in well under a second with no flakiness.
"""

import asyncio
import sys
import time
from pathlib import Path

from decode.tools.exec import ExecResult, LocalExecutor


async def test_run_captures_stdout_and_exit_code(tmp_path: Path):
    result = await LocalExecutor().run("echo hello", cwd=tmp_path, timeout_s=5.0)

    assert isinstance(result, ExecResult)
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False


async def test_run_captures_stderr_separately(tmp_path: Path):
    result = await LocalExecutor().run("printf oops 1>&2", cwd=tmp_path, timeout_s=5.0)

    assert result.stdout == ""
    assert result.stderr == "oops"
    assert result.exit_code == 0
    assert result.timed_out is False


async def test_run_reports_non_zero_exit_code(tmp_path: Path):
    result = await LocalExecutor().run("false", cwd=tmp_path, timeout_s=5.0)

    assert result.exit_code != 0
    assert result.timed_out is False


async def test_run_executes_in_the_given_cwd(tmp_path: Path):
    sub = tmp_path / "workdir"
    sub.mkdir()

    result = await LocalExecutor().run("pwd", cwd=sub, timeout_s=5.0)

    # The shell's working directory is the cwd we passed (resolve to dodge /var → /private/var).
    assert Path(result.stdout.strip()).resolve() == sub.resolve()


async def test_run_supports_shell_features(tmp_path: Path):
    # Pipes / && must work — the model uses a real shell, not a bare exec.
    result = await LocalExecutor().run("echo one && echo two | cat", cwd=tmp_path, timeout_s=5.0)

    assert result.stdout == "one\ntwo\n"
    assert result.exit_code == 0


async def test_run_times_out_and_returns_timed_out(tmp_path: Path):
    start = time.monotonic()
    result = await LocalExecutor().run(
        f"{sys.executable} -c 'import time; time.sleep(30)'",
        cwd=tmp_path,
        timeout_s=0.2,
    )
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    # Returned promptly after the deadline (not after the 30s sleep).
    assert elapsed < 10.0


async def test_run_returns_partial_output_captured_before_timeout(tmp_path: Path):
    """A child that flushes output *before* the deadline then hangs must return that output.

    The executor docstring promises ``timed_out=True`` results carry "whatever partial output
    was captured before the kill". This pins that contract: the child writes a sentinel line to
    BOTH streams, flushes, then sleeps past the deadline. The kill must drain — not discard —
    the bytes the child already buffered, so the sentinels survive into the returned streams.
    """
    command = (
        f"{sys.executable} -c "
        "'import sys, time; "
        'sys.stdout.write("EARLY-OUT\\n"); sys.stdout.flush(); '
        'sys.stderr.write("EARLY-ERR\\n"); sys.stderr.flush(); '
        "time.sleep(30)'"
    )

    result = await LocalExecutor().run(command, cwd=tmp_path, timeout_s=0.4)

    assert result.timed_out is True
    assert "EARLY-OUT" in result.stdout
    assert "EARLY-ERR" in result.stderr


async def test_timeout_kills_a_child_the_command_spawned(tmp_path: Path):
    """A timed-out command's *child* must die with it — no orphaned process left writing.

    The command backgrounds a python child that, every 50ms, appends a line to a sentinel
    file and would keep doing so for 30s. We time out the parent at 0.2s; killing the whole
    process group must stop the child too. We snapshot the sentinel right after the timeout,
    wait past when the child would have written more, and assert the file did NOT keep growing.
    """
    sentinel = tmp_path / "child-alive.log"
    child_script = tmp_path / "child.py"
    child_script.write_text(
        "import time\n"
        f"f = open({str(sentinel)!r}, 'a')\n"
        "while True:\n"
        "    f.write('tick\\n'); f.flush()\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    # Background the child so the parent shell returns control to the group; the group is what
    # the executor kills on timeout. `wait` keeps the parent alive past the deadline.
    command = f"{sys.executable} {child_script} & wait"

    result = await LocalExecutor().run(command, cwd=tmp_path, timeout_s=0.2)
    assert result.timed_out is True

    size_at_kill = sentinel.stat().st_size if sentinel.exists() else 0
    # The child wrote every 50ms; wait well past several intervals. A surviving orphan would
    # grow the file; a correctly killed group leaves it frozen.
    await asyncio.sleep(0.5)
    size_after = sentinel.stat().st_size if sentinel.exists() else 0

    assert size_after == size_at_kill, "the spawned child outlived the timed-out command"


async def test_run_decodes_undecodable_bytes_without_crashing(tmp_path: Path):
    # A raw invalid UTF-8 byte on stdout must be replaced, not raise (filterwarnings=error).
    result = await LocalExecutor().run(r"printf '\377'", cwd=tmp_path, timeout_s=5.0)

    assert result.exit_code == 0
    assert "�" in result.stdout  # the Unicode replacement character


# --- ExecResult.note (ADR-0011 §2): optional, backward-compatible, unused by LocalExecutor --------


def test_exec_result_note_defaults_to_empty():
    # The new field is optional; a construction without it leaves ``note`` empty (byte-identical path).
    assert ExecResult(stdout="", stderr="", exit_code=0, timed_out=False).note == ""


def test_exec_result_accepts_four_positional_args_for_backward_compat():
    # Existing positional callers (pre-``note``) keep working — ``note`` is a trailing default.
    result = ExecResult("out", "err", 1, True)

    assert (result.stdout, result.stderr, result.exit_code, result.timed_out) == (
        "out",
        "err",
        1,
        True,
    )
    assert result.note == ""


async def test_local_executor_never_sets_a_note(tmp_path: Path):
    # ``none`` mode leaves ``note`` empty; only the sandbox executors populate it (on a timeout reset).
    result = await LocalExecutor().run("echo hi", cwd=tmp_path, timeout_s=5.0)

    assert result.note == ""
