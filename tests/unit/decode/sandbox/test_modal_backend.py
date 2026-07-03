"""Hermetic unit tests for the Modal sandbox backend (``decode.sandbox.modal_backend``, ADR-0012 §2,4,5).

These exercise :class:`ModalBackend` — the second adapter behind the one
:class:`~decode.sandbox.executor.SandboxExecutor` — with **no modal account and no network**: the
``modal`` SDK is imported through a single lazy seam (:func:`_load_modal`) that the tests patch with a
**fake ``modal`` module** + a fake ``Sandbox`` / ``SandboxFilesystem`` double. They prove the backend's
contract offline:

* **exec** — the fresh ``bash -lc`` / ``workdir=/workspace`` / int per-exec ``timeout`` / ``text=False``
  shape, the stdout/stderr/exit mapping, the timeout-kills-the-exec-not-the-sandbox rule, and the
  ``errors="replace"`` decode contract (binary output never crashes the turn);
* **create + bootstrap** — ``App.lookup`` → ``Sandbox.create("sleep","infinity", …)`` → ``mkdir -p
  /workspace`` → the ONE tar bootstrap upload (``filesystem.write_bytes`` + a remote ``tar -x`` exec),
  with **no** ``add_local_dir`` anywhere;
* **file ops = the SandboxFilesystem API** against ``/workspace/<rel>`` (a fake fs backed by a real
  ``tmp_path`` so the round-trips are truthful), including the missing-file mapping (``read_bytes`` /
  ``list_dir`` → ``FileNotFoundError``; ``stat`` → ``None``) that mirrors
  :class:`~decode.sandbox.docker_backend.DockerBackend`, and the file-op revival that gives every op the
  same self-heal ``exec`` has when the remote sandbox is gone;
* **export** — the ONE end-of-session sweep (a remote ``tar -c`` exec + ``read_bytes`` + host-side
  ``extract_tar``), standalone (no ``destroy``);
* **revival** — a ``poll()``-dead sandbox (exec) or a ``NotFoundError`` from a **file op** on a
  shut-down sandbox is recreated + re-bootstrapped from the host state; exec surfaces the one-shot restore
  ``note`` inline, a file op has no note channel so it defers the note to the next exec;
* **destroy** — ``terminate`` (idempotent, best-effort), and that importing ``decode.sandbox`` /
  ``decode.cli`` never imports ``modal``.

The real end-to-end contract (a live remote sandbox, the real SandboxFilesystem, bootstrap + export
round-trips, revival) lives in the ``@skipif``-guarded ``tests/integration/test_modal_executor.py``.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import tarfile
import types
from io import BytesIO
from pathlib import Path, PurePosixPath

import pytest

from decode.sandbox.executor import FileStat
from decode.sandbox.modal_backend import (
    _BOOTSTRAP_TAR,
    _EXPORT_TAR,
    _SANDBOX_LOST_EXIT,
    _SANDBOX_LOST_NOTE,
    _WORKSPACE,
    ModalBackend,
    _load_modal,
)

# --- fake modal filesystem exceptions (the real ones do NOT subclass FileNotFoundError) ----------
# ``modal.exception.SandboxFilesystemNotFoundError`` subclasses ``modal.exception.Error`` (verified on
# modal 1.5.1), NOT ``FileNotFoundError`` — so the backend must normalize it. The fakes stand in for
# those real classes; the backend catches ``_load_modal().exception.<name>``, so a fake module whose
# ``exception`` namespace points here lets the mapping be proven with no real modal import.


class _FsNotFound(Exception):
    """Stand-in for ``modal.exception.SandboxFilesystemNotFoundError``."""


class _FsNotADir(Exception):
    """Stand-in for ``modal.exception.SandboxFilesystemNotADirectoryError``."""


class _Gone(Exception):
    """Stand-in for ``modal.exception.NotFoundError`` — a command exec'd against a shut-down sandbox."""


_FAKE_EXCEPTION_NS = types.SimpleNamespace(
    SandboxFilesystemNotFoundError=_FsNotFound,
    SandboxFilesystemNotADirectoryError=_FsNotADir,
    NotFoundError=_Gone,
)


# --- fake modal surface (only what the backend touches) ------------------------------------------


class _Aio:
    """Mimics modal's synchronicity double: the backend only ever calls the ``.aio`` async variant."""

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

    The backend runs ``exec(..., text=False)``, so modal's real reader yields *raw bytes* (no strict
    UTF-8 decode). The fake mirrors that faithfully — returning bytes, not str — so it can never mask the
    decode contract (undecodable bytes must be replaced, not crash) the way a str fake would.
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


class _FakeFileInfo:
    """A ``modal.sandbox_fs.FileInfo`` stand-in — only the ``name`` / ``type`` / ``size`` the backend reads.

    ``type`` mirrors modal's ``FileType`` enum through its ``.value`` (``"file"`` / ``"directory"`` /
    ``"symlink"``) — the backend reads ``info.type.value``, never the enum identity, so a light
    namespace suffices.
    """

    def __init__(self, name: str, *, is_dir: bool, size: int) -> None:
        self.name = name
        self.path = f"/workspace/{name}"
        self.type = types.SimpleNamespace(value="directory" if is_dir else "file")
        self.size = size


class _FakeFilesystem:
    """A ``SandboxFilesystem`` stand-in backed by a **real** host ``root`` dir (truthful round-trips).

    Maps an absolute remote path onto ``root`` (``/workspace`` == ``root``; other absolutes land under
    ``root`` too, e.g. the ``/tmp`` bootstrap tar) and runs plain pathlib, raising the fake
    ``SandboxFilesystemNotFoundError`` / ``NotADirectoryError`` on a miss so the backend's normalization
    is genuinely exercised. Records ``write_bytes`` calls so the bootstrap upload can be asserted.

    Flip :attr:`gone` to ``True`` to simulate a **shut-down sandbox**: every op then raises the fake
    ``NotFoundError`` (``_Gone``, a raw ``GRPCError`` stand-in) — exactly what a real max-lifetime-expired
    / terminated sandbox's filesystem raises — so the backend's file-op revival is genuinely exercised.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        # True → every op raises _Gone (a shut-down sandbox), driving the file-op revival path.
        self.gone = False
        self.write_calls: list[tuple[bytes, str]] = []
        self.read_bytes = _Aio(self._read_bytes)
        self.write_bytes = _Aio(self._write_bytes)
        self.make_directory = _Aio(self._make_directory)
        self.list_files = _Aio(self._list_files)
        self.stat = _Aio(self._stat)
        self.remove = _Aio(self._remove)

    def _guard_live(self) -> None:
        """Raise the fake ``NotFoundError`` when the sandbox was flipped ``gone`` (a shut-down remote)."""
        if self.gone:
            raise _Gone("Modal Sandbox not found — has already shut down")

    def _host(self, remote: str) -> Path:
        p = PurePosixPath(remote)
        ws = PurePosixPath(_WORKSPACE)
        if p == ws:
            return self._root
        if ws == p or ws in p.parents:
            return self._root / p.relative_to(ws)
        return self._root / p.relative_to("/")

    async def _read_bytes(self, remote: str) -> bytes:
        self._guard_live()
        try:
            return self._host(remote).read_bytes()
        except FileNotFoundError as exc:
            raise _FsNotFound(remote) from exc

    async def _write_bytes(self, data, remote: str) -> None:
        self._guard_live()
        self.write_calls.append((bytes(data), remote))
        path = self._host(remote)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def _make_directory(self, remote: str, *, create_parents: bool = True) -> None:
        self._guard_live()
        self._host(remote).mkdir(parents=create_parents, exist_ok=True)

    async def _list_files(self, remote: str) -> list[_FakeFileInfo]:
        self._guard_live()
        base = self._host(remote)
        try:
            children = list(base.iterdir())
        except FileNotFoundError as exc:
            # modal raises SandboxFilesystemNotFoundError (not the pathlib FileNotFoundError) — mirror
            # it so the backend's list_dir normalization to FileNotFoundError is genuinely exercised.
            raise _FsNotFound(remote) from exc
        return [
            _FakeFileInfo(child.name, is_dir=child.is_dir(), size=child.stat().st_size)
            for child in children
        ]

    async def _stat(self, remote: str) -> _FakeFileInfo:
        self._guard_live()
        path = self._host(remote)
        try:
            st = path.stat()
        except FileNotFoundError as exc:
            raise _FsNotFound(remote) from exc
        except NotADirectoryError as exc:
            raise _FsNotADir(remote) from exc
        return _FakeFileInfo(path.name, is_dir=path.is_dir(), size=st.st_size)

    async def _remove(self, remote: str, *, recursive: bool = False) -> None:
        self._guard_live()
        import shutil

        path = self._host(remote)
        if not path.exists() and not path.is_symlink():
            raise _FsNotFound(remote)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


# Command payloads the backend runs as sandbox infrastructure (not model commands) — the ``mkdir``
# bootstrap and the tar/rm transport execs. Filtered out of ``command_calls`` so a test can assert only
# the real ``bash -lc`` the executor issued.
def _is_infra_exec(args: tuple) -> bool:
    if args[:1] == ("mkdir",):
        return True
    if args[:2] == ("bash", "-lc"):
        payload = args[2]
        return payload.startswith("tar ") or payload.startswith("rm -f")
    return False


class _FakeSandbox:
    """A ``modal.Sandbox`` stand-in: records ``exec`` calls, exposes a ``filesystem``, tracks terminate.

    ``poll_result`` mirrors modal's liveness probe — ``None`` while the sandbox runs (the default), an
    exit code once it ended remotely (flip it mid-test to simulate a max-lifetime expiry). Infra execs
    (``mkdir`` / tar / rm) get a success proc; every other exec returns ``bash_proc`` (the model command).
    """

    def __init__(
        self, root: Path, object_id: str = "sb-fake", bash_proc: _FakeProc | None = None
    ) -> None:
        self.object_id = object_id
        self.bash_proc = bash_proc or _FakeProc()
        self.filesystem = _FakeFilesystem(root)
        self.exec_calls: list[tuple[tuple, dict]] = []
        self.terminate_count = 0
        self.poll_result: int | None = None
        self.exec = _Aio(self._exec)
        self.terminate = _Aio(self._terminate)
        self.poll = _Aio(self._poll)

    async def _exec(self, *args, **kwargs) -> _FakeProc:
        self.exec_calls.append((args, kwargs))
        if _is_infra_exec(args):
            return _FakeProc()
        return self.bash_proc

    async def _terminate(self) -> None:
        self.terminate_count += 1

    async def _poll(self) -> int | None:
        return self.poll_result

    @property
    def command_calls(self) -> list[tuple[tuple, dict]]:
        """Only the model command execs — the ``mkdir`` / tar / rm infra execs excluded."""
        return [c for c in self.exec_calls if not _is_infra_exec(c[0])]


def _make_fake_modal(
    sandbox: _FakeSandbox, *, image: object = "image-obj"
) -> tuple[types.SimpleNamespace, dict]:
    """Build a fake ``modal`` module exposing exactly the surface the backend uses.

    The default ``image`` is a plain string — it would raise on ``add_local_dir`` (a plain ``str`` has no
    such method), so green everywhere IS the pin that the backend NEVER calls ``add_local_dir`` (ADR-0012
    §5 retires the modal ``add_local_dir`` seeding). ``exception`` points at the fake filesystem-error
    namespace so the missing-file normalization is proven with no real modal import.
    """
    lookup = _Recording(return_value="app-obj")
    from_registry = _RecordingSync(return_value=image)
    create = _Recording(return_value=sandbox)
    fake = types.SimpleNamespace(
        App=types.SimpleNamespace(lookup=lookup),
        Image=types.SimpleNamespace(from_registry=from_registry),
        Sandbox=types.SimpleNamespace(create=create),
        exception=_FAKE_EXCEPTION_NS,
    )
    return fake, {"lookup": lookup, "from_registry": from_registry, "create": create}


@pytest.fixture
def sandbox(tmp_path: Path) -> _FakeSandbox:
    """A fake remote sandbox whose filesystem is backed by a real ``tmp_path/remote`` dir."""
    return _FakeSandbox(tmp_path / "remote")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A host Workspace root with one seeded file, so the bootstrap tar has real content to upload."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "seed.txt").write_text("seeded\n", encoding="utf-8")
    return ws


@pytest.fixture
def fake_modal(mocker, sandbox: _FakeSandbox) -> dict:
    """Patch the lazy import seam so the backend gets the fake ``modal`` module (no account/network)."""
    fake, recorders = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    return recorders


# --- construction is inert -----------------------------------------------------------------------


def test_construction_creates_no_sandbox_and_imports_no_modal(mocker):
    load = mocker.patch("decode.sandbox.modal_backend._load_modal")

    backend = ModalBackend()

    assert backend._sandbox is None
    assert backend._workspace is None
    load.assert_not_called()  # nothing imports/creates modal until create()


# --- create: spawn + bootstrap upload (App.lookup → Sandbox.create → mkdir → tar upload) ----------


async def test_create_spawns_the_sandbox_once_with_the_configured_image_and_lifetime(
    fake_modal, sandbox, workspace
):
    backend = ModalBackend()

    await backend.create(workspace)
    await backend.create(workspace)  # idempotent: a second create with a live sandbox no-ops

    assert len(fake_modal["create"].calls) == 1  # created exactly once
    assert len(fake_modal["lookup"].calls) == 1
    assert fake_modal["lookup"].calls[0] == (("decode-sandbox",), {"create_if_missing": True})
    assert fake_modal["from_registry"].calls[0] == (
        ("ghcr.io/astral-sh/uv:python3.12-bookworm-slim",),
        {},
    )
    create_args, create_kwargs = fake_modal["create"].calls[0]
    # The explicit long-lived entrypoint (the docker keeper's shape): without it modal runs the image's
    # own CMD — the astral uv default (``Cmd=[uv]``) prints help and exits, killing the sandbox.
    assert create_args == ("sleep", "infinity")
    assert create_kwargs["app"] == "app-obj"
    assert create_kwargs["image"] == "image-obj"
    assert create_kwargs["timeout"] == 600  # int(settings.sandbox_timeout_s) default
    assert backend._sandbox is sandbox


async def test_create_bootstraps_the_workspace_dir_before_any_command(
    fake_modal, sandbox, workspace
):
    backend = ModalBackend()

    await backend.create(workspace)

    # The stock image has no /workspace; it is mkdir -p'd once before anything runs against it.
    assert sandbox.exec_calls[0][0] == ("mkdir", "-p", "/workspace")


async def test_create_uploads_the_host_workspace_as_one_tar_and_extracts_it(
    fake_modal, sandbox, workspace
):
    # ADR-0012 §5: ONE bootstrap upload — tar the host Workspace, write_bytes it to a /tmp tar, then a
    # single remote ``tar -x`` into /workspace. NEVER add_local_dir (retired), never a per-file walk.
    backend = ModalBackend()

    await backend.create(workspace)

    # write_bytes carried the tar to the absolute /tmp bootstrap path ...
    assert len(sandbox.filesystem.write_calls) == 1
    tar_bytes, remote = sandbox.filesystem.write_calls[0]
    assert remote == _BOOTSTRAP_TAR
    # ... and the tar really contains the seeded host file (a faithful whole-tree bootstrap).
    with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:*") as tar:
        assert "./seed.txt" in tar.getnames()
    # ... and a single remote extract exec ran, unpacking the tar into /workspace.
    tar_execs = [c for c in sandbox.exec_calls if c[0][:2] == ("bash", "-lc")]
    assert any(
        _BOOTSTRAP_TAR in c[0][2] and f"-C {_WORKSPACE}" in c[0][2] and c[0][2].startswith("tar -x")
        for c in tar_execs
    )


async def test_create_raises_a_runtime_error_when_the_bootstrap_extract_fails(mocker, workspace):
    # A non-zero ``tar -x`` bootstrap is an infra failure the executor renders — the backend surfaces it
    # as RuntimeError (the (RuntimeError, OSError) the executor catches) and reaps the half-built sandbox.
    sandbox = _FakeSandbox(workspace.parent / "remote2")

    async def _exec(*args, **kwargs):
        sandbox.exec_calls.append((args, kwargs))
        if args[:2] == ("bash", "-lc") and args[2].startswith("tar -x"):
            return _FakeProc(stderr=b"tar: broken", exit_code=2)  # the untar fails
        return _FakeProc()

    sandbox.exec = _Aio(_exec)
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()

    with pytest.raises(RuntimeError, match="bootstrap"):
        await backend.create(workspace)

    assert sandbox.terminate_count == 1  # the half-built sandbox was reaped, not leaked
    assert backend._sandbox is None


# --- exec: fresh bash -lc in /workspace, int timeout, text=False ---------------------------------


async def test_exec_runs_bash_lc_with_workspace_workdir_and_int_timeout(
    fake_modal, sandbox, workspace
):
    backend = ModalBackend()
    await backend.create(workspace)

    await backend.exec("bash", "-lc", "echo hi", timeout_s=5.0)

    (args, kwargs) = sandbox.command_calls[0]
    assert args == ("bash", "-lc", "echo hi")
    assert kwargs["workdir"] == "/workspace"
    assert kwargs["timeout"] == 5  # modal wants an int-second per-exec timeout
    assert kwargs["text"] is False  # bytes streams → decode with errors="replace" (never crash)


async def test_exec_maps_stdout_stderr_and_exit_code(mocker, workspace):
    sandbox = _FakeSandbox(
        workspace.parent / "remote", bash_proc=_FakeProc(b"out\n", b"err\n", exit_code=3)
    )
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)

    result = await backend.exec("bash", "-lc", "whatever", timeout_s=30.0)

    assert result.stdout == "out\n"
    assert result.stderr == "err\n"  # streams stay separate (unlike docker's retired merge)
    assert result.exit_code == 3
    assert result.timed_out is False
    assert result.note == ""  # a normal command carries no out-of-band note


async def test_exec_floors_a_sub_second_timeout_to_one_second(fake_modal, sandbox, workspace):
    backend = ModalBackend()
    await backend.create(workspace)

    await backend.exec("bash", "-lc", "echo hi", timeout_s=0.5)

    # int(0.5) == 0, which modal reads as "no timeout"; the floor keeps a real 1s deadline.
    assert sandbox.command_calls[0][1]["timeout"] == 1


# --- timeout: kill the exec, keep the sandbox ---------------------------------------------------


async def test_exec_timeout_reports_timed_out_without_terminating_the_sandbox(mocker, workspace):
    # modal signals a per-exec timeout by returning -1 from wait() (verified against modal 1.5.1).
    sandbox = _FakeSandbox(
        workspace.parent / "remote", bash_proc=_FakeProc(b"partial", exit_code=-1)
    )
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)

    result = await backend.exec("bash", "-lc", "sleep 100", timeout_s=1.0)

    assert result.timed_out is True
    assert result.exit_code == -signal.SIGKILL  # normalized to the sibling executors' sentinel
    assert result.stdout == "partial"  # partial output is preserved
    assert result.note == ""  # the sandbox + its fs survive — no session-level reset happened
    assert sandbox.terminate_count == 0  # the sandbox is NOT torn down on an exec timeout


# --- decode contract: non-UTF-8 output is replaced, never crashes -------------------------------


async def test_exec_replaces_undecodable_bytes_on_both_streams(mocker, workspace):
    # Regression (blocking): binary / non-UTF-8 output must NOT crash exec(). text=False +
    # errors="replace" fixes modal's strict-UTF-8 text reader (which raised on the first invalid byte).
    sandbox = _FakeSandbox(
        workspace.parent / "remote", bash_proc=_FakeProc(b"\xff\xfehi", b"\xff", exit_code=0)
    )
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)

    result = await backend.exec("bash", "-lc", "head -c 4 /dev/urandom", timeout_s=30.0)

    assert result.stdout == "��hi"  # invalid bytes → U+FFFD, the valid ascii tail kept
    assert result.stderr == "�"  # stderr decodes with replace too (and stays split)
    assert result.exit_code == 0
    assert result.timed_out is False


async def test_exec_round_trips_utf8_multibyte_output(mocker, workspace):
    # Valid multibyte UTF-8 must decode EXACTLY — the replace decoder never mangles well-formed bytes.
    sandbox = _FakeSandbox(
        workspace.parent / "remote", bash_proc=_FakeProc("café ✓\n".encode(), exit_code=0)
    )
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)

    result = await backend.exec("bash", "-lc", "printf 'café ✓\\n'", timeout_s=30.0)

    assert result.stdout == "café ✓\n"


# --- file ops = SandboxFilesystem, direct against the remote (ADR-0012 §4) ----------------------


async def test_file_ops_round_trip_through_the_filesystem_api(fake_modal, sandbox, workspace):
    # The file ops go straight through sandbox.filesystem against /workspace/<rel> — always truthful
    # (no host mirror). The fake fs is backed by a real dir, so the round-trips are real.
    backend = ModalBackend()
    await backend.create(workspace)
    root = sandbox.filesystem._root

    await backend.write_bytes("sub/f.txt", b"hi there")
    assert (root / "sub" / "f.txt").read_bytes() == b"hi there"  # parents created
    assert await backend.read_bytes("sub/f.txt") == b"hi there"

    await backend.make_directory("made/deep")
    assert (root / "made" / "deep").is_dir()

    stat = await backend.stat("sub/f.txt")
    assert stat == FileStat(path="sub/f.txt", is_dir=False, size=len(b"hi there"))
    dir_stat = await backend.stat("sub")
    assert dir_stat is not None and dir_stat.is_dir is True


async def test_write_bytes_passes_data_then_remote_path_in_modal_order(
    fake_modal, sandbox, workspace
):
    # modal's signature is write_bytes(data, remote_path) — the reversed arg order is a real trap.
    backend = ModalBackend()
    await backend.create(workspace)
    sandbox.filesystem.write_calls.clear()  # drop the bootstrap upload

    await backend.write_bytes("note.txt", b"payload")

    assert sandbox.filesystem.write_calls == [(b"payload", f"{_WORKSPACE}/note.txt")]


async def test_stat_returns_none_for_a_missing_path(fake_modal, sandbox, workspace):
    # ADR-0012 §4 + DockerBackend parity: an absent path → None (modal's NotFound is caught), never raises.
    backend = ModalBackend()
    await backend.create(workspace)

    assert await backend.stat("missing") is None


async def test_read_bytes_normalizes_a_missing_file_to_filenotfounderror(
    fake_modal, sandbox, workspace
):
    # modal raises SandboxFilesystemNotFoundError (NOT a FileNotFoundError subclass); the backend must
    # normalize it so the shared file-tool layer (081) catches the same exception it catches for docker.
    backend = ModalBackend()
    await backend.create(workspace)

    with pytest.raises(FileNotFoundError):
        await backend.read_bytes("does-not-exist")


async def test_remove_deletes_files_and_trees_and_tolerates_a_missing_path(
    fake_modal, sandbox, workspace
):
    backend = ModalBackend()
    await backend.create(workspace)
    root = sandbox.filesystem._root
    await backend.write_bytes("f.txt", b"x")
    await backend.make_directory("tree/nested")
    await backend.write_bytes("tree/nested/g.txt", b"y")

    await backend.remove("f.txt")
    await backend.remove("tree")
    await backend.remove("already-gone")  # missing → no raise (docker's missing_ok parity)

    assert not (root / "f.txt").exists()
    assert not (root / "tree").exists()


async def test_list_dir_returns_sorted_logical_paths(fake_modal, sandbox, workspace):
    backend = ModalBackend()
    await backend.create(workspace)
    await backend.write_bytes("sub/a.txt", b"aa")
    await backend.make_directory("sub/b")

    entries = await backend.list_dir("sub")

    # Logical (workspace-relative, POSIX) paths, sorted by name; dirs flagged.
    assert entries == [
        FileStat(path="sub/a.txt", is_dir=False, size=2),
        FileStat(
            path="sub/b", is_dir=True, size=(sandbox.filesystem._root / "sub" / "b").stat().st_size
        ),
    ]


async def test_list_dir_at_the_root_uses_bare_names(fake_modal, sandbox, workspace):
    backend = ModalBackend()
    await backend.create(workspace)
    await backend.write_bytes("top.txt", b"x")

    entries = await backend.list_dir("")

    names = {e.path for e in entries}
    assert "top.txt" in names  # bare name at the root (no prefix)
    assert all("/" not in e.path for e in entries)  # every root entry is a bare name


async def test_list_dir_on_a_missing_path_matches_dockerbackend(
    fake_modal, sandbox, workspace, tmp_path
):
    # Cross-backend parity (081's shared file layer needs ONE contract): list_dir on a MISSING path must
    # raise FileNotFoundError on BOTH backends — docker's pathlib ``iterdir`` raises it, and modal must
    # normalize its SandboxFilesystemNotFoundError to match (read_bytes/stat/remove already did; list_dir
    # was the missed op). Before the fix modal leaked SandboxFilesystemNotFoundError here.
    from decode.sandbox.docker_backend import DockerBackend

    modal_backend = ModalBackend()
    await modal_backend.create(workspace)
    docker_backend = DockerBackend()
    docker_backend._workspace = tmp_path  # pathlib file ops need no daemon, just a workspace root

    with pytest.raises(FileNotFoundError):
        await modal_backend.list_dir("no-such-dir")
    with pytest.raises(FileNotFoundError):
        await docker_backend.list_dir("no-such-dir")


async def test_file_ops_require_a_created_sandbox():
    backend = ModalBackend()  # _sandbox is None

    with pytest.raises(RuntimeError, match="created sandbox"):
        await backend.read_bytes("f.txt")


# --- export: the ONE end-of-session sweep, standalone (no destroy) -------------------------------


async def test_export_sweeps_the_workspace_down_to_the_host(fake_modal, sandbox, workspace):
    # ADR-0012 §5,8: export = a remote ``tar -c`` of /workspace + read_bytes the tar down + extract it
    # host-side, leaving the sandbox ALIVE (standalone /ship). A file only present remotely lands on the
    # host after export.
    backend = ModalBackend()
    await backend.create(workspace)
    # Simulate the remote /workspace holding a file the host does not have yet, then have the export tar
    # read it back: the fake ``tar -c`` writes the export tar from the remote fs root.
    (sandbox.filesystem._root / "made-remotely.txt").write_text("remote\n", encoding="utf-8")
    _install_fake_tar_roundtrip(sandbox)

    await backend.export()

    assert (workspace / "made-remotely.txt").read_text(encoding="utf-8") == "remote\n"
    assert sandbox.terminate_count == 0  # export does NOT tear the sandbox down
    assert backend._sandbox is sandbox  # still live


async def test_export_is_a_safe_noop_when_nothing_was_created(fake_modal):
    backend = ModalBackend()

    await backend.export()  # no sandbox → nothing to sweep, never raises


async def test_export_swallows_a_sweep_failure(mocker, sandbox, workspace):
    # Best-effort: a sweep failure (e.g. the tar exec errored) must never block teardown — export logs
    # and returns instead of raising, so aclose's export→destroy still reaps the sandbox.
    backend = ModalBackend()
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    await backend.create(workspace)

    async def _boom_read(remote):
        raise RuntimeError("read_bytes blew up")

    sandbox.filesystem.read_bytes = _Aio(_boom_read)

    await backend.export()  # must not raise


# --- revival: a remotely-ended sandbox is recreated + re-bootstrapped ----------------------------


async def test_exec_revives_a_remotely_ended_sandbox_and_notes_the_restore(mocker, workspace):
    # The modal ``timeout`` is a max LIFETIME, so a sandbox can end remotely mid-session. The next exec
    # must probe poll(), recreate + re-bootstrap from the host state, run the command on the fresh
    # sandbox, and surface the restore via the note — ONCE (not sticky).
    first = _FakeSandbox(workspace.parent / "remote-first", object_id="sb-first")
    second = _FakeSandbox(workspace.parent / "remote-second", object_id="sb-second")
    sandboxes = iter([first, second])
    created: list[dict] = []

    async def _create(*args, **kwargs):
        created.append(kwargs)
        return next(sandboxes)

    fake, _ = _make_fake_modal(first)
    fake.Sandbox = types.SimpleNamespace(create=_Aio(_create))
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)

    ok = await backend.exec("bash", "-lc", "echo one", timeout_s=30.0)
    first.poll_result = 137  # the sandbox hit its max lifetime between calls
    revived = await backend.exec("bash", "-lc", "echo two", timeout_s=30.0)
    after = await backend.exec("bash", "-lc", "echo three", timeout_s=30.0)

    assert ok.note == ""
    assert len(created) == 2  # one fresh create after the remote death
    assert "restored from the last local state" in revived.note  # the restore reaches the model ...
    assert "may be lost" in revived.note  # ... with the honest in-flight-loss caveat ...
    assert after.note == ""  # ... exactly once — the flag is one-shot
    assert backend._sandbox is second
    assert (
        len(second.filesystem.write_calls) == 1
    )  # the fresh sandbox was re-bootstrapped from host
    assert len(second.command_calls) == 2  # 'echo two' and 'echo three' ran on the fresh sandbox
    assert len(first.command_calls) == 1  # nothing new ran through the dead handle


async def test_exec_renders_a_session_lost_failure_when_revival_fails(mocker, workspace):
    # The never-crash contract: if reviving a dead sandbox fails (the recreate raises), exec renders a
    # session-lost failure (exit 125 + a note) instead of crashing the turn.
    first = _FakeSandbox(workspace.parent / "remote-first")

    async def _create(*args, **kwargs):
        if getattr(_create, "called", False):
            raise RuntimeError("modal create failed on revival")
        _create.called = True
        return first

    fake, _ = _make_fake_modal(first)
    fake.Sandbox = types.SimpleNamespace(create=_Aio(_create))
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)
    first.poll_result = 1  # the sandbox died; the next exec must try to revive and fail

    result = await backend.exec("bash", "-lc", "echo hi", timeout_s=30.0)

    assert result.exit_code == _SANDBOX_LOST_EXIT
    assert result.timed_out is False
    assert result.note  # a session-lost note is set — never a bare crash


async def test_exec_revives_when_the_command_hits_a_shut_down_sandbox(mocker, workspace):
    # Regression (real modal 1.5.1): a sandbox can expire in the narrow window BETWEEN the poll()
    # liveness probe (still None) and the exec, so sb.exec raises NotFoundError. exec() must catch it,
    # revive + re-bootstrap, and retry ONCE carrying the restore note — instead of crashing the turn.
    first = _FakeSandbox(workspace.parent / "remote-first", object_id="sb-first")
    second = _FakeSandbox(workspace.parent / "remote-second", object_id="sb-second")
    sandboxes = iter([first, second])

    async def _create(*args, **kwargs):
        return next(sandboxes)

    async def _first_exec(*args, **kwargs):
        # Infra (mkdir / tar) succeeds so create + bootstrap complete; the model command hits the gone
        # sandbox — poll() never reported the death (the mid-command race).
        first.exec_calls.append((args, kwargs))
        if _is_infra_exec(args):
            return _FakeProc()
        raise _Gone("Modal Sandbox not found — has already shut down")

    first.exec = _Aio(_first_exec)
    fake, _ = _make_fake_modal(first)
    fake.Sandbox = types.SimpleNamespace(create=_Aio(_create))
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)  # creates + bootstraps `first`

    result = await backend.exec("bash", "-lc", "echo hi", timeout_s=30.0)

    assert backend._sandbox is second  # the retry revived onto a fresh sandbox
    assert result.exit_code == 0  # ... where the command actually ran
    assert "restored from the last local state" in result.note  # the retry carries the restore note
    assert (
        len(second.filesystem.write_calls) == 1
    )  # the fresh sandbox was re-bootstrapped from host


async def test_exec_lets_a_non_sandbox_error_from_the_command_surface(mocker, workspace):
    # The exec-error revival is scoped to a shut-down sandbox (NotFoundError). A genuine bug (ValueError)
    # from the exec must still crash — never masked as a rendered failure or a spurious revival.
    sandbox = _FakeSandbox(workspace.parent / "remote")

    async def _boom_exec(*args, **kwargs):
        sandbox.exec_calls.append((args, kwargs))
        if _is_infra_exec(args):
            return _FakeProc()
        raise ValueError("a real bug")

    sandbox.exec = _Aio(_boom_exec)
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)

    with pytest.raises(ValueError, match="a real bug"):
        await backend.exec("bash", "-lc", "echo hi", timeout_s=30.0)


# --- file-op revival: a dead sandbox is recreated + re-bootstrapped, like exec (ADR-0012 §3,4) ----


@pytest.mark.parametrize(
    "op_name", ["read_bytes", "write_bytes", "make_directory", "stat", "list_dir", "remove"]
)
async def test_a_file_op_revives_a_dead_sandbox_and_retries_once(mocker, workspace, op_name):
    # Regression (real modal 1.5.1): a max-lifetime-expired / terminated sandbox makes a file op raise
    # NotFoundError (a raw GRPCError, NOT FileNotFoundError) — exactly as sb.exec does. EVERY file op must
    # self-heal like exec: drop the dead handle, recreate + re-bootstrap from the host, retry the op ONCE
    # — never leak the raw GRPC type. (Before the fix the raw NotFoundError escaped and _sandbox stayed
    # a dead handle, so a file tool right after an expiry crashed where bash would revive.)
    first = _FakeSandbox(workspace.parent / "remote-first", object_id="sb-first")
    second = _FakeSandbox(workspace.parent / "remote-second", object_id="sb-second")
    # The re-bootstrap restores the host state; the fake bootstrap does not extract the tar, so pre-seed
    # second's fs root with the target read/stat/list/remove need after revival.
    second.filesystem._root.mkdir(parents=True, exist_ok=True)
    (second.filesystem._root / "target.txt").write_text("restored", encoding="utf-8")
    sandboxes = iter([first, second])

    async def _create(*args, **kwargs):
        return next(sandboxes)

    fake, _ = _make_fake_modal(first)
    fake.Sandbox = types.SimpleNamespace(create=_Aio(_create))
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)  # first is created + bootstrapped (gone still False)
    first.filesystem.gone = (
        True  # first now behaves like a shut-down sandbox: fs ops raise NotFoundError
    )

    ops = {
        "read_bytes": lambda: backend.read_bytes("target.txt"),
        "write_bytes": lambda: backend.write_bytes("written.txt", b"payload"),
        "make_directory": lambda: backend.make_directory("newdir"),
        "stat": lambda: backend.stat("target.txt"),
        "list_dir": lambda: backend.list_dir(""),
        "remove": lambda: backend.remove("target.txt"),
    }
    result = await ops[op_name]()

    # Revived onto the fresh sandbox (dead handle dropped, re-bootstrapped from the host state) ...
    assert backend._sandbox is second
    assert any(remote == _BOOTSTRAP_TAR for _, remote in second.filesystem.write_calls)
    assert (
        backend._recreated is True
    )  # ... the one-shot restore note is pending for the NEXT exec ...
    # ... and the op actually completed on the fresh sandbox.
    root = second.filesystem._root
    if op_name == "read_bytes":
        assert result == b"restored"
    elif op_name == "stat":
        assert result == FileStat(path="target.txt", is_dir=False, size=len(b"restored"))
    elif op_name == "list_dir":
        assert "target.txt" in {entry.path for entry in result}
    elif op_name == "write_bytes":
        assert (root / "written.txt").read_bytes() == b"payload"
    elif op_name == "make_directory":
        assert (root / "newdir").is_dir()
    elif op_name == "remove":
        assert not (root / "target.txt").exists()


async def test_a_file_op_revival_note_rides_the_next_exec(mocker, workspace):
    # A file op has no ``note`` channel of its own, so a file-op-triggered revival sets only the one-shot
    # flag; the restore note must ride the NEXT exec's result (the same flag exec's own revival uses) and
    # then clear — so the model still learns the Workspace was restored, exactly once.
    first = _FakeSandbox(workspace.parent / "remote-first", object_id="sb-first")
    second = _FakeSandbox(workspace.parent / "remote-second", object_id="sb-second")
    second.filesystem._root.mkdir(parents=True, exist_ok=True)
    sandboxes = iter([first, second])

    async def _create(*args, **kwargs):
        return next(sandboxes)

    fake, _ = _make_fake_modal(first)
    fake.Sandbox = types.SimpleNamespace(create=_Aio(_create))
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)
    first.filesystem.gone = True

    await backend.write_bytes("f.txt", b"x")  # revives onto `second`, silently (no note channel)
    first_exec = await backend.exec("bash", "-lc", "echo one", timeout_s=30.0)
    second_exec = await backend.exec("bash", "-lc", "echo two", timeout_s=30.0)

    assert backend._sandbox is second
    assert (
        "restored from the last local state" in first_exec.note
    )  # the note rides the next exec ...
    assert "may be lost" in first_exec.note
    assert second_exec.note == ""  # ... exactly once (one-shot)


async def test_a_file_op_renders_a_clean_error_when_revival_fails_again(mocker, workspace):
    # The never-crash contract for the file path: when the revival target is ALSO gone, the file op must
    # surface a CLEAN RuntimeError — never a raw modal NotFoundError (_Gone, a GRPCError) — with no spin
    # (exactly two sandboxes created, then stop). Mirrors exec's session-lost rendering.
    first = _FakeSandbox(workspace.parent / "remote-first", object_id="sb-first")
    second = _FakeSandbox(workspace.parent / "remote-second", object_id="sb-second")
    second.filesystem._root.mkdir(parents=True, exist_ok=True)
    created: list[dict] = []
    sandboxes = iter([first, second])

    async def _create(*args, **kwargs):
        created.append(kwargs)
        return next(sandboxes)

    fake, _ = _make_fake_modal(first)
    fake.Sandbox = types.SimpleNamespace(create=_Aio(_create))
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)  # first created + bootstrapped
    first.filesystem.gone = True
    second.filesystem.gone = (
        True  # the revival target is dead too → the retry hits NotFoundError again
    )

    with pytest.raises(RuntimeError) as excinfo:
        await backend.read_bytes("target.txt")

    assert not isinstance(
        excinfo.value, _Gone
    )  # a CLEAN RuntimeError, never the raw GRPC NotFoundError
    assert isinstance(
        excinfo.value.__cause__, _Gone
    )  # ... wrapping the real modal death as its cause
    assert _SANDBOX_LOST_NOTE in str(excinfo.value)
    assert len(created) == 2  # exactly two creates (initial + one revival) — no spin


async def test_a_timeout_never_triggers_file_op_revival(mocker, workspace):
    # A per-exec timeout (modal's -1) is NOT a dead sandbox: it must never trip the revival machinery —
    # the same sandbox stays live, created exactly once, with no restore note pending.
    sandbox = _FakeSandbox(
        workspace.parent / "remote", bash_proc=_FakeProc(b"partial", exit_code=-1)
    )
    created: list[dict] = []

    async def _create(*args, **kwargs):
        created.append(kwargs)
        return sandbox

    fake, _ = _make_fake_modal(sandbox)
    fake.Sandbox = types.SimpleNamespace(create=_Aio(_create))
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)

    result = await backend.exec("bash", "-lc", "sleep 100", timeout_s=1.0)

    assert result.timed_out is True
    assert result.exit_code == -signal.SIGKILL
    assert len(created) == 1  # exactly one sandbox — a timeout does NOT recreate
    assert backend._sandbox is sandbox  # the same live sandbox
    assert backend._recreated is False  # no restore note pending


# --- destroy: terminate, idempotent, safe when never started -------------------------------------


async def test_destroy_terminates_the_sandbox(fake_modal, sandbox, workspace):
    backend = ModalBackend()
    await backend.create(workspace)

    await backend.destroy()

    assert sandbox.terminate_count == 1
    assert backend._sandbox is None


async def test_destroy_is_a_safe_noop_when_never_started(mocker):
    load = mocker.patch("decode.sandbox.modal_backend._load_modal")
    backend = ModalBackend()

    await backend.destroy()
    await backend.destroy()  # a double destroy must not raise

    load.assert_not_called()  # no sandbox was created, so none is torn down (no modal import)


async def test_destroy_is_idempotent_after_a_create(fake_modal, sandbox, workspace):
    backend = ModalBackend()
    await backend.create(workspace)

    await backend.destroy()
    await backend.destroy()  # the second call finds nothing to do

    assert sandbox.terminate_count == 1  # terminate ran exactly once


async def test_destroy_swallows_a_terminate_failure(mocker, sandbox, workspace):
    async def _boom() -> None:
        raise RuntimeError("modal terminate blew up")

    sandbox.terminate = _Aio(_boom)
    fake, _ = _make_fake_modal(sandbox)
    mocker.patch("decode.sandbox.modal_backend._load_modal", return_value=fake)
    backend = ModalBackend()
    await backend.create(workspace)

    await backend.destroy()  # best-effort: a terminate failure must never block the exit path

    assert backend._sandbox is None


# --- observability -------------------------------------------------------------------------------


async def test_logs_create_command_and_terminate(fake_modal, sandbox, workspace, caplog):
    import logging

    backend = ModalBackend()
    with caplog.at_level(logging.DEBUG, logger="decode.sandbox.modal_backend"):
        await backend.create(workspace)
        await backend.exec("bash", "-lc", "echo observ", timeout_s=30.0)
        await backend.destroy()

    text = caplog.text
    assert f"[sandbox] modal create {sandbox.object_id}" in text  # id on create (INFO)
    assert "image=ghcr.io/astral-sh/uv:python3.12-bookworm-slim" in text  # image on create
    assert "echo observ" in text  # the command (DEBUG)
    assert "exit=0" in text
    assert "bytes=" in text  # byte count, never the output itself
    assert f"[sandbox] modal terminate {sandbox.object_id}" in text  # id on terminate (INFO)


# --- laziness: modal never imported by decode.sandbox / decode.cli -------------------------------


def test_load_modal_returns_the_real_sdk():
    # The seam the tests patch really does import modal — proves the fake mirrors a real thing.
    assert _load_modal().__name__ == "modal"


def test_importing_decode_does_not_import_modal():
    # A fresh interpreter: neither decode.cli nor decode.sandbox may transitively import modal, so the
    # none/docker/REPL paths never pay the modal import cost (ADR-0012 §2; ADR-0011 §3 retained).
    code = (
        "import sys; import decode.cli; import decode.sandbox; "
        "leaked = sorted(m for m in sys.modules if m == 'modal' or m.startswith('modal.')); "
        "assert not leaked, leaked"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --- helpers -------------------------------------------------------------------------------------


def _install_fake_tar_roundtrip(sandbox: _FakeSandbox) -> None:
    """Make the fake sandbox's ``tar -c`` export exec actually pack its remote fs root into the export tar.

    The default fake exec returns an empty success proc for infra commands; export needs the export tar
    to really exist in the (fake) remote fs so ``read_bytes`` can stream it down. This wires the ``tar
    -c`` exec to write a genuine tar of the remote root to ``_EXPORT_TAR`` (mirroring what real ``tar``
    would do), so the host-side ``extract_tar`` reconstructs the swept tree.
    """
    fs = sandbox.filesystem

    async def _exec(*args, **kwargs):
        sandbox.exec_calls.append((args, kwargs))
        if args[:2] == ("bash", "-lc") and args[2].startswith("tar -c"):
            buffer = BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as tar:
                tar.add(fs._root, arcname=".")
            fs._host(_EXPORT_TAR).write_bytes(buffer.getvalue())
            return _FakeProc()
        if _is_infra_exec(args):
            return _FakeProc()
        return sandbox.bash_proc

    sandbox.exec = _Aio(_exec)
