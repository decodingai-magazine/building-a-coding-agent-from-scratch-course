"""Hermetic unit tests for the Modal sandbox executor (``decode.sandbox.modal_executor``).

These exercise :class:`ModalExecutor` with **no modal account and no network** (ADR-0011 §3): the
``modal`` SDK is imported through a single lazy seam (:func:`_load_modal`) that the tests patch with a
fake ``modal`` module + a fake ``Sandbox`` double. They prove the executor's contract offline —
create-once-and-reuse, the ``bash -lc`` / ``workdir`` / per-exec ``timeout`` exec shape, the
stdout/stderr/exit mapping, the timeout-kills-the-exec-not-the-sandbox rule, ``aclose`` idempotency,
and that importing ``decode.sandbox`` / ``decode.cli`` never imports ``modal``. The real end-to-end
contract (a live remote sandbox, fs persistence, the real per-exec timeout) lives in the
``@skipif``-guarded ``tests/integration/test_modal_executor.py``.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import types

import pytest

from decode.sandbox.modal_executor import ModalExecutor, _load_modal

# --- fake modal surface (only what the executor touches) -------------------------------------


class _Aio:
    """Mimics modal's synchronicity double: the executor only ever calls the ``.aio`` async variant."""

    def __init__(self, fn) -> None:
        self.aio = fn


class _Recording:
    """An async callable exposed as ``.aio`` that records its calls and returns a fixed value."""

    def __init__(self, return_value=None) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self._return_value = return_value
        self.aio = self._aio

    async def _aio(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._return_value


class _RecordingSync:
    """A plain sync callable that records its calls (``modal.Image.from_registry`` is sync)."""

    def __init__(self, return_value=None) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self._return_value = return_value

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._return_value


class _FakeStream:
    """A ``StreamReader`` stand-in: ``.read.aio()`` yields the whole payload once, as **bytes**.

    The executor runs ``exec(..., text=False)``, so modal's real reader yields *raw bytes* (no strict
    UTF-8 decode). The fake mirrors that faithfully — returning bytes, not str — so it can never again
    mask the decode contract (undecodable bytes must be replaced, not crash) the way a str fake did.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.read = _Aio(self._read)

    async def _read(self) -> bytes:
        return self._data


class _FakeProc:
    """A ``ContainerProcess`` stand-in exposing ``.stdout`` / ``.stderr`` / ``.wait`` (no terminate)."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", exit_code: int = 0) -> None:
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self._exit_code = exit_code
        self.wait = _Aio(self._wait)

    async def _wait(self) -> int:
        return self._exit_code


class _FakeSandbox:
    """A ``modal.Sandbox`` stand-in: records ``exec`` calls, returns a per-command proc, tracks terminate."""

    def __init__(self, object_id: str = "sb-fake", bash_proc: _FakeProc | None = None) -> None:
        self.object_id = object_id
        self.bash_proc = bash_proc or _FakeProc()
        self.exec_calls: list[tuple[tuple, dict]] = []
        self.terminate_count = 0
        self.exec = _Aio(self._exec)
        self.terminate = _Aio(self._terminate)

    async def _exec(self, *args, **kwargs) -> _FakeProc:
        self.exec_calls.append((args, kwargs))
        if args[:1] == ("mkdir",):  # the /workspace bootstrap: empty output, exit 0
            return _FakeProc()
        return self.bash_proc

    async def _terminate(self) -> None:
        self.terminate_count += 1

    @property
    def bash_calls(self) -> list[tuple[tuple, dict]]:
        """Only the command execs (the ``mkdir`` bootstrap excluded) — what ``run`` issued."""
        return [c for c in self.exec_calls if c[0][:1] != ("mkdir",)]


def _make_fake_modal(sandbox: _FakeSandbox) -> tuple[types.SimpleNamespace, dict]:
    """Build a fake ``modal`` module exposing exactly the surface the executor uses.

    Returns the module and a dict of the recording callables so a test can assert how it was called.
    """
    lookup = _Recording(return_value="app-obj")
    from_registry = _RecordingSync(return_value="image-obj")
    create = _Recording(return_value=sandbox)
    fake = types.SimpleNamespace(
        App=types.SimpleNamespace(lookup=lookup),
        Image=types.SimpleNamespace(from_registry=from_registry),
        Sandbox=types.SimpleNamespace(create=create),
    )
    return fake, {"lookup": lookup, "from_registry": from_registry, "create": create}


@pytest.fixture
def sandbox() -> _FakeSandbox:
    return _FakeSandbox()


@pytest.fixture
def fake_modal(mocker, sandbox: _FakeSandbox) -> dict:
    """Patch the lazy import seam so the executor gets the fake ``modal`` module (no account/network)."""
    fake, recorders = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_executor._load_modal", return_value=fake)
    return recorders


# --- construction is inert -------------------------------------------------------------------


def test_construction_creates_no_sandbox_and_imports_no_modal(mocker):
    load = mocker.patch("decode.sandbox.modal_executor._load_modal")

    executor = ModalExecutor()

    assert executor._sandbox is None
    load.assert_not_called()  # nothing imports/creates modal until the first run()


# --- create-once + reuse ---------------------------------------------------------------------


async def test_run_creates_the_sandbox_once_and_reuses_it(fake_modal, sandbox):
    executor = ModalExecutor()

    await executor.run("echo one", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]
    await executor.run("echo two", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    assert len(fake_modal["create"].calls) == 1  # the sandbox is created exactly once
    assert len(fake_modal["lookup"].calls) == 1  # App.lookup happens once
    assert len(sandbox.bash_calls) == 2  # both commands ran on the one reused sandbox


async def test_ensure_sandbox_bootstraps_the_workspace_directory(fake_modal, sandbox):
    executor = ModalExecutor()

    await executor.run("echo hi", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    # python:3.12-slim has no /workspace; the executor mkdir -p's it once before any command.
    assert sandbox.exec_calls[0][0] == ("mkdir", "-p", "/workspace")


async def test_create_uses_the_configured_image_and_lifetime(fake_modal, sandbox):
    executor = ModalExecutor()

    await executor.run("echo hi", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    assert fake_modal["lookup"].calls[0] == (("decode-sandbox",), {"create_if_missing": True})
    assert fake_modal["from_registry"].calls[0] == (("python:3.12-slim",), {})
    _, create_kwargs = fake_modal["create"].calls[0]
    assert create_kwargs["app"] == "app-obj"
    assert create_kwargs["image"] == "image-obj"
    assert create_kwargs["timeout"] == 600  # int(settings.sandbox_timeout_s) default


# --- exec shape + result mapping -------------------------------------------------------------


async def test_run_execs_bash_lc_with_workspace_workdir_and_int_timeout(fake_modal, sandbox):
    executor = ModalExecutor()

    await executor.run("echo hi", cwd=None, timeout_s=5.0)  # type: ignore[arg-type]

    (args, kwargs) = sandbox.bash_calls[0]
    assert args == ("bash", "-lc", "echo hi")
    assert kwargs["workdir"] == "/workspace"
    assert kwargs["timeout"] == 5  # modal wants an int-second per-exec timeout
    assert kwargs["text"] is False  # bytes streams → decode with errors="replace" (never crash)


async def test_run_maps_stdout_stderr_and_exit_code(mocker, sandbox):
    sandbox.bash_proc = _FakeProc(stdout=b"out\n", stderr=b"err\n", exit_code=3)
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_executor._load_modal", return_value=fake)
    executor = ModalExecutor()

    result = await executor.run("whatever", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    assert result.stdout == "out\n"
    assert result.stderr == "err\n"  # streams stay separate (unlike docker's merge)
    assert result.exit_code == 3
    assert result.timed_out is False
    assert result.note == ""  # a normal command carries no out-of-band note


async def test_run_floors_a_sub_second_timeout_to_one_second(fake_modal, sandbox):
    executor = ModalExecutor()

    await executor.run("echo hi", cwd=None, timeout_s=0.5)  # type: ignore[arg-type]

    # int(0.5) == 0, which modal reads as "no timeout"; the floor keeps a real 1s deadline.
    assert sandbox.bash_calls[0][1]["timeout"] == 1


# --- timeout: kill the exec, keep the sandbox ------------------------------------------------


async def test_run_timeout_reports_timed_out_without_terminating_the_sandbox(mocker, sandbox):
    # modal signals a per-exec timeout by returning -1 from wait() (verified against modal 1.5.1).
    sandbox.bash_proc = _FakeProc(stdout=b"partial", exit_code=-1)
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_executor._load_modal", return_value=fake)
    executor = ModalExecutor()

    result = await executor.run("sleep 100", cwd=None, timeout_s=1.0)  # type: ignore[arg-type]

    assert result.timed_out is True
    assert result.exit_code == -signal.SIGKILL  # normalized to the sibling executors' sentinel
    assert result.stdout == "partial"  # partial output is preserved
    assert result.note == ""  # the sandbox + its fs survive — no session-level reset happened
    assert sandbox.terminate_count == 0  # the sandbox is NOT torn down on an exec timeout


async def test_sandbox_survives_a_timeout_and_the_next_run_reuses_it(mocker, sandbox):
    sandbox.bash_proc = _FakeProc(exit_code=-1)  # every command "times out" here
    fake, recorders = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_executor._load_modal", return_value=fake)
    executor = ModalExecutor()

    first = await executor.run("sleep 100", cwd=None, timeout_s=1.0)  # type: ignore[arg-type]
    second = await executor.run("echo alive", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    assert first.timed_out is True
    assert len(recorders["create"].calls) == 1  # same sandbox reused — no re-create after a timeout
    assert second is not None


# --- decode contract: non-UTF-8 output is replaced, never crashes -----------------------------


async def test_run_replaces_undecodable_bytes_on_both_streams(mocker, sandbox):
    # Regression (blocking): binary / non-UTF-8 output must NOT crash run(). The ExecResult contract
    # (tools/exec.py) says undecodable bytes are replaced, never crash — LocalExecutor / DockerExecutor
    # both honor it. modal's text=True reader decodes STRICT UTF-8 and raised UnicodeDecodeError on the
    # first invalid byte (e.g. `head -c 16 /dev/urandom`); text=False + errors="replace" fixes it.
    sandbox.bash_proc = _FakeProc(stdout=b"\xff\xfehi", stderr=b"\xff", exit_code=0)
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_executor._load_modal", return_value=fake)
    executor = ModalExecutor()

    result = await executor.run("head -c 4 /dev/urandom", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    assert result.stdout == "��hi"  # invalid bytes → U+FFFD, the valid ascii tail kept
    assert result.stderr == "�"  # stderr decodes with replace too (and stays split from stdout)
    assert result.exit_code == 0
    assert result.timed_out is False


async def test_run_round_trips_utf8_multibyte_output(mocker, sandbox):
    # Valid multibyte UTF-8 (café, a check mark) must decode EXACTLY — the replace decoder never mangles
    # well-formed bytes. Guards against a naive latin-1 / ascii "fix".
    sandbox.bash_proc = _FakeProc(stdout="café ✓\n".encode(), exit_code=0)
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_executor._load_modal", return_value=fake)
    executor = ModalExecutor()

    result = await executor.run("printf 'café ✓\\n'", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    assert result.stdout == "café ✓\n"


async def test_run_timeout_decodes_partial_bytes_with_replace(mocker, sandbox):
    # The timeout branch reads PARTIAL streams too — they must decode with replace, not crash on a byte
    # split mid-sequence at the kill point.
    sandbox.bash_proc = _FakeProc(stdout=b"partial\xff", exit_code=-1)
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_executor._load_modal", return_value=fake)
    executor = ModalExecutor()

    result = await executor.run("sleep 100", cwd=None, timeout_s=1.0)  # type: ignore[arg-type]

    assert result.timed_out is True
    assert result.stdout == "partial�"  # partial output + replaced undecodable byte, no crash
    assert result.exit_code == -signal.SIGKILL


# --- aclose: terminate, idempotent, safe when never started ----------------------------------


async def test_aclose_terminates_the_sandbox(fake_modal, sandbox):
    executor = ModalExecutor()
    await executor.run("echo hi", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    await executor.aclose()

    assert sandbox.terminate_count == 1
    assert executor._sandbox is None


async def test_aclose_is_a_safe_noop_when_never_started(mocker):
    load = mocker.patch("decode.sandbox.modal_executor._load_modal")
    executor = ModalExecutor()

    await executor.aclose()
    await executor.aclose()  # a double aclose must not raise

    load.assert_not_called()  # no sandbox was created, so none is torn down (no modal import)


async def test_aclose_is_idempotent_after_a_run(fake_modal, sandbox):
    executor = ModalExecutor()
    await executor.run("echo hi", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    await executor.aclose()
    await executor.aclose()  # the second call finds nothing to do

    assert sandbox.terminate_count == 1  # terminate ran exactly once


async def test_aclose_swallows_a_terminate_failure(mocker, sandbox):
    async def _boom() -> None:
        raise RuntimeError("modal terminate blew up")

    sandbox.terminate = _Aio(_boom)
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_executor._load_modal", return_value=fake)
    executor = ModalExecutor()
    await executor.run("echo hi", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]

    await executor.aclose()  # best-effort: a terminate failure must never block the exit path

    assert executor._sandbox is None


# --- observability ---------------------------------------------------------------------------


async def test_logs_create_command_and_terminate(fake_modal, sandbox, caplog):
    import logging

    executor = ModalExecutor()
    with caplog.at_level(logging.DEBUG, logger="decode.sandbox.modal_executor"):
        await executor.run("echo observ", cwd=None, timeout_s=30.0)  # type: ignore[arg-type]
        await executor.aclose()

    text = caplog.text
    assert f"[sandbox] modal create {sandbox.object_id}" in text  # id on create (INFO)
    assert "image=python:3.12-slim" in text  # image on create
    assert "[sandbox] $ echo observ" in text  # the command (DEBUG)
    assert "exit=0" in text
    assert "bytes=" in text  # byte count, never the output itself
    assert f"[sandbox] modal terminate {sandbox.object_id}" in text  # id on terminate (INFO)


# --- laziness: modal never imported by decode.sandbox / decode.cli ---------------------------


def test_load_modal_returns_the_real_sdk():
    # The seam the tests patch really does import modal — proves the fake mirrors a real thing.
    assert _load_modal().__name__ == "modal"


def test_importing_decode_does_not_import_modal():
    # A fresh interpreter: neither decode.cli nor decode.sandbox may transitively import modal, so the
    # none/docker/REPL paths never pay the modal import cost (ADR-0011 §3).
    code = (
        "import sys; import decode.cli; import decode.sandbox; "
        "leaked = sorted(m for m in sys.modules if m == 'modal' or m.startswith('modal.')); "
        "assert not leaked, leaked"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
