"""Unit tests for the file/search tools' sandbox routing through the backend seam (ADR-0012 §4).

Hermetic (no docker, no modal): the seam is injected by patching ``files._active_backend``.
A recording fake backend proves read/write/edit route bytes on logical (workspace-relative)
paths; a local-exec fake (real ``find``/``grep`` + pathlib on a host tmp dir) proves glob/grep
output-parity with ``none`` mode; containment escapes are rejected by path-math before any
backend op. Sync tools bridge via ``anyio.from_thread.run``, so routing tests invoke them
through ``anyio.to_thread.run_sync`` — the thread context the real loop provides.
"""

from __future__ import annotations

import functools
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import pytest
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.sandbox.executor import FileStat, WorkspaceEscape
from decode.tools import files
from decode.tools.askuser import deny_user_question_resolver
from decode.tools.exec import ExecResult


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(cwd: Path, *, approved: bool = True) -> RunContext[AgentDeps]:
    """A pre-approved RunContext whose ``deps.cwd`` is the (logical) Workspace root."""
    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=approved)  # type: ignore[arg-type]


async def _call(fn: Callable[..., str], *args: Any, **kwargs: Any) -> str:
    """Invoke a sync file tool in a worker thread so its ``anyio.from_thread.run`` bridge works."""
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))


@pytest.fixture(autouse=True)
def _no_lsp_enrichment(mocker):
    """Isolate the byte-transport seam from the orthogonal LSP enrichment (ADR-0007 / ADR-0012 §7).

    These tests pin ``read`` / ``write`` / ``edit`` byte transport + containment path-math — NOT the
    post-edit ``ty`` enrichment, which has its own sandbox-posture tests in ``test_files.py`` /
    ``test_lsp.py``. They inject the backend at the ``_active_backend`` seam while the autouse mode stays
    ``none`` (a deliberately minimal injection — a backend is only *really* active when the mode is not
    ``none``), so ``write`` / ``edit`` of a ``.py`` file would reach the real ``ty`` enricher, spawn a
    server nothing here shuts down, and leak its unclosed subprocess pipe transports. Stub ``_enrich`` to
    identity so the seam is exercised in isolation and hermetically (no ``ty``, no leak).
    """
    mocker.patch("decode.tools.files._enrich", new=lambda base, cwd, path: base)


class _RecordingBackend:
    """A recording :class:`~decode.sandbox.executor.SandboxBackend`: captures file-op calls + logical paths."""

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.writes: list[tuple[str, bytes]] = []
        self.stat_calls: list[str] = []
        self.exec_calls: list[tuple[tuple[str, ...], float]] = []
        self._stats: dict[str, FileStat] = {}
        self._bytes: dict[str, bytes] = {}
        self.exec_result = ExecResult(stdout="", stderr="", exit_code=0, timed_out=False)

    def seed_file(self, rel: str, data: bytes) -> None:
        self._bytes[rel] = data
        self._stats[rel] = FileStat(path=rel, is_dir=False, size=len(data))

    def seed_dir(self, rel: str) -> None:
        self._stats[rel] = FileStat(path=rel, is_dir=True, size=0)

    async def stat(self, rel: str) -> FileStat | None:
        self.stat_calls.append(rel)
        return self._stats.get(rel)

    async def read_bytes(self, rel: str) -> bytes:
        self.reads.append(rel)
        if rel not in self._bytes:
            raise FileNotFoundError(rel)
        return self._bytes[rel]

    async def write_bytes(self, rel: str, data: bytes) -> None:
        self.writes.append((rel, data))
        self._bytes[rel] = data
        self._stats[rel] = FileStat(path=rel, is_dir=False, size=len(data))

    async def exec(self, *args: str, timeout_s: float) -> ExecResult:
        self.exec_calls.append((args, timeout_s))
        return self.exec_result


class _LocalBackend:
    """A :class:`~decode.sandbox.executor.SandboxBackend` backed by a real host dir — no container.

    Mirrors :class:`~decode.sandbox.docker_backend.DockerBackend`'s bind-mount semantics (pathlib file
    ops + a real subprocess ``exec``) against a plain tmp dir, so ``glob`` / ``grep`` parity tests
    exercise the **real** ``find`` / ``grep`` the container would run.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, rel: str) -> Path:
        return self._root / rel

    async def stat(self, rel: str) -> FileStat | None:
        path = self._path(rel)
        try:
            st = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        return FileStat(path=rel, is_dir=path.is_dir(), size=st.st_size)

    async def read_bytes(self, rel: str) -> bytes:
        return self._path(rel).read_bytes()

    async def write_bytes(self, rel: str, data: bytes) -> None:
        path = self._path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def exec(self, *args: str, timeout_s: float) -> ExecResult:
        proc = subprocess.run(list(args), cwd=self._root, capture_output=True, text=True)
        return ExecResult(
            stdout=proc.stdout, stderr=proc.stderr, exit_code=proc.returncode, timed_out=False
        )


# _resolve_logical: the backend-agnostic containment path-math


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("f.txt", "f.txt"),
        ("sub/f.txt", "sub/f.txt"),
        ("./sub/f.txt", "sub/f.txt"),
        ("sub/../f.txt", "f.txt"),  # in-tree ``..`` folds, does NOT escape
        ("a/b/../../c.txt", "c.txt"),
        ("", ""),  # the workspace root itself
        ("./", ""),
    ],
)
def test_resolve_logical_normalizes_in_tree_paths(raw: str, expected: str):
    assert files._resolve_logical(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "../secret.txt",
        "../../etc/passwd",
        "sub/../../escape.txt",
        "/etc/passwd",  # absolute → refused (a modal path is not a host path; never Path.resolve)
        "/workspace/x",
    ],
)
def test_resolve_logical_rejects_escapes(raw: str):
    with pytest.raises(ModelRetry, match="outside the working directory"):
        files._resolve_logical(raw)


# _glob_match: reproduces Path.glob


def test_glob_match_matches_pathlib_across_patterns(tmp_path: Path):
    # Build a tree (incl. dotfiles / dot-dirs — pathlib 3.12 does NOT special-case them) and assert the
    # shared matcher agrees with Path.glob for every listed file, so the sandbox glob has true parity.
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / ".hidden").mkdir()
    (tmp_path / "tests").mkdir()
    for rel in [
        "a.py",
        "README.md",
        ".cfg.py",
        "src/main.py",
        "src/pkg/util.py",
        "src/pkg/__init__.py",
        "src/.hidden/h.py",
        "tests/test_a.py",
    ]:
        (tmp_path / rel).write_text("", encoding="utf-8")

    all_files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    for pattern in ["*.py", "**/*.py", "**/*", "src/*.py", "src/**/*.py", "**/test_*.py", "*.md"]:
        expected = sorted(
            str(p.relative_to(tmp_path)) for p in tmp_path.glob(pattern) if p.is_file()
        )
        got = sorted(f for f in all_files if files._glob_match(f, pattern))
        assert got == expected, pattern


# read / write / edit: routing through the backend on LOGICAL paths


async def test_read_routes_through_backend_read_bytes_on_logical_path(mocker, tmp_path: Path):
    backend = _RecordingBackend()
    backend.seed_file("sub/f.txt", b"alpha\nbravo\ncharlie\n")
    mocker.patch("decode.tools.files._active_backend", return_value=backend)

    out = await _call(files.read, _ctx(tmp_path), "sub/f.txt")

    assert backend.reads == ["sub/f.txt"]  # the logical (workspace-relative) path, not a host path
    # The rendering is the SAME shared numbering as none mode.
    assert out == "1\talpha\n2\tbravo\n3\tcharlie"


async def test_read_missing_and_directory_map_to_the_same_model_retries(mocker, tmp_path: Path):
    backend = _RecordingBackend()
    backend.seed_dir("adir")
    mocker.patch("decode.tools.files._active_backend", return_value=backend)

    with pytest.raises(ModelRetry, match="No such file"):
        await _call(files.read, _ctx(tmp_path), "nope.txt")
    with pytest.raises(ModelRetry, match="is a directory"):
        await _call(files.read, _ctx(tmp_path), "adir")


async def test_write_routes_through_backend_write_bytes_on_logical_path(mocker, tmp_path: Path):
    backend = _RecordingBackend()
    mocker.patch("decode.tools.files._active_backend", return_value=backend)

    out = await _call(files.write, _ctx(tmp_path), "sub/new.txt", "hi there")

    assert backend.writes == [("sub/new.txt", b"hi there")]  # logical path + bytes through the seam
    assert out == "Wrote 'sub/new.txt' (8 characters)."  # same base string as none mode
    # Nothing landed on the host cwd — the write went to the backend, not pathlib.
    assert not (tmp_path / "sub" / "new.txt").exists()


async def test_write_rejects_writing_over_a_directory(mocker, tmp_path: Path):
    backend = _RecordingBackend()
    backend.seed_dir("adir")
    mocker.patch("decode.tools.files._active_backend", return_value=backend)

    with pytest.raises(ModelRetry, match="is a directory"):
        await _call(files.write, _ctx(tmp_path), "adir", "x")
    assert backend.writes == []  # nothing written


async def test_edit_routes_through_the_seam_and_reuses_the_shared_replacement(
    mocker, tmp_path: Path
):
    backend = _RecordingBackend()
    backend.seed_file("f.py", b"x = 1\ny = 2\n")
    mocker.patch("decode.tools.files._active_backend", return_value=backend)

    out = await _call(files.edit, _ctx(tmp_path), "f.py", "y = 2", "y = 3")

    assert backend.writes[-1] == (
        "f.py",
        b"x = 1\ny = 3\n",
    )  # unique-match replace through the seam
    assert out == "Edited 'f.py' (replaced 1 occurrence)."


async def test_edit_preserves_crlf_and_bom_through_the_seam(mocker, tmp_path: Path):
    backend = _RecordingBackend()
    backend.seed_file("f.txt", "﻿alpha\r\nbeta\r\n".encode())
    mocker.patch("decode.tools.files._active_backend", return_value=backend)

    await _call(files.edit, _ctx(tmp_path), "f.txt", "beta", "gamma")

    # The shared _apply_edit restores the original BOM + CRLF style even through the byte seam.
    assert backend.writes[-1][1] == "﻿alpha\r\ngamma\r\n".encode()


async def test_edit_missing_file_maps_to_model_retry(mocker, tmp_path: Path):
    backend = _RecordingBackend()
    mocker.patch("decode.tools.files._active_backend", return_value=backend)

    with pytest.raises(ModelRetry, match="No such file to edit"):
        await _call(files.edit, _ctx(tmp_path), "gone.py", "a", "b")
    assert backend.writes == []


# containment: escapes rejected before any backend op


@pytest.mark.parametrize(
    "tool_and_args",
    [
        ("read", ("../secret.txt",)),
        ("write", ("../evil.txt", "x")),
        ("edit", ("../evil.txt", "a", "b")),
    ],
)
def test_file_tools_reject_dotdot_escapes_before_touching_the_backend(
    mocker, tmp_path: Path, tool_and_args: tuple[str, tuple[str, ...]]
):
    # The deferred `..` containment lands here: the shared path-math refuses the escape synchronously,
    # BEFORE the async backend op is bridged — so a recording backend records nothing (no leak).
    backend = _RecordingBackend()
    backend.seed_file("../secret.txt", b"SECRET")  # even if it "existed", it must never be reached
    mocker.patch("decode.tools.files._active_backend", return_value=backend)
    name, args = tool_and_args
    tool = getattr(files, name)

    with pytest.raises(ModelRetry, match="outside the working directory"):
        tool(_ctx(tmp_path), *args)

    assert backend.reads == []
    assert backend.writes == []
    assert backend.stat_calls == []


# glob / grep: sandbox output-parity with none mode


def _seed_tree(root: Path) -> None:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "a.py").write_text("import os\nprint('a')\n", encoding="utf-8")
    (root / "b.py").write_text("x = 1  # TODO tidy\n", encoding="utf-8")
    (root / "c.txt").write_text("plain text TODO\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("def main():\n    return 1  # TODO\n", encoding="utf-8")
    (root / "src" / "pkg" / "util.py").write_text("def util():\n    pass\n", encoding="utf-8")
    (root / "docs" / "note.md").write_text("a TODO here\n", encoding="utf-8")


@pytest.mark.parametrize("pattern", ["*.py", "**/*.py", "**/*", "src/**/*.py"])
async def test_glob_has_output_parity_with_none_mode(mocker, tmp_path: Path, pattern: str):
    # Same tree, same pattern: the sandbox glob (real ``find`` via the local-exec backend + the shared
    # matcher) renders identically to the none-mode glob (host ``Path.glob``).
    _seed_tree(tmp_path)
    none_out = files.glob(_ctx(tmp_path), pattern)  # sandbox_mode=none (autouse) → direct pathlib

    mocker.patch("decode.tools.files._active_backend", return_value=_LocalBackend(tmp_path))
    sandbox_out = await _call(files.glob, _ctx(tmp_path), pattern)

    assert sandbox_out == none_out


async def test_glob_no_matches_is_a_model_retry_in_sandbox(mocker, tmp_path: Path):
    _seed_tree(tmp_path)
    mocker.patch("decode.tools.files._active_backend", return_value=_LocalBackend(tmp_path))

    with pytest.raises(ModelRetry, match="No files match"):
        await _call(files.glob, _ctx(tmp_path), "*.rs")


async def test_glob_rejects_escaping_pattern_in_sandbox(mocker, tmp_path: Path):
    # The escaping-pattern guard runs before the seam in both modes.
    mocker.patch("decode.tools.files._active_backend", return_value=_LocalBackend(tmp_path))
    with pytest.raises(ModelRetry, match="points outside"):
        await _call(files.glob, _ctx(tmp_path), "../*.py")


@pytest.mark.parametrize(
    "kwargs",
    [
        {},  # whole-tree default (grep -r)
        {"path": "b.py"},  # single-file scope
        {"glob": "**/*.py"},  # glob file scope (recursive)
    ],
)
async def test_grep_has_output_parity_with_none_mode(mocker, tmp_path: Path, kwargs: dict):
    # Same tree + pattern + scope: the sandbox grep (real ``grep`` via the local-exec backend) renders
    # identically to the none-mode grep (Python ``re`` per line) — down to the sorted path:lineno:line.
    _seed_tree(tmp_path)
    none_out = files.grep(_ctx(tmp_path), "TODO", **kwargs)

    mocker.patch("decode.tools.files._active_backend", return_value=_LocalBackend(tmp_path))
    sandbox_out = await _call(files.grep, _ctx(tmp_path), "TODO", **kwargs)

    assert sandbox_out == none_out


async def test_grep_missing_path_is_a_model_retry_in_sandbox(mocker, tmp_path: Path):
    _seed_tree(tmp_path)
    mocker.patch("decode.tools.files._active_backend", return_value=_LocalBackend(tmp_path))

    with pytest.raises(ModelRetry, match="No such file to search"):
        await _call(files.grep, _ctx(tmp_path), "TODO", "does-not-exist.py")


async def test_grep_no_matches_is_a_model_retry_in_sandbox(mocker, tmp_path: Path):
    _seed_tree(tmp_path)
    mocker.patch("decode.tools.files._active_backend", return_value=_LocalBackend(tmp_path))

    with pytest.raises(ModelRetry, match="No matches"):
        await _call(files.grep, _ctx(tmp_path), "ZZZ-absent-token")


# none mode: the seam is NOT engaged


def test_none_mode_never_engages_the_backend_seam(tmp_path: Path):
    # The autouse fixture pins SANDBOX_MODE=none; ``active_backend`` must yield None so the file tools
    # take the direct-pathlib path (proven by the write landing on the host cwd, not any backend).
    from decode.tools import bash

    assert bash.active_backend(tmp_path) is None
    out = files.write(_ctx(tmp_path), "host.txt", "on disk")
    assert (tmp_path / "host.txt").read_text(encoding="utf-8") == "on disk"
    assert out == "Wrote 'host.txt' (7 characters)."


async def test_gating_still_fires_before_the_seam_in_sandbox(mocker, tmp_path: Path):
    # The approval gate is unchanged in a sandbox mode: an unapproved call defers BEFORE the backend.
    backend = _RecordingBackend()
    mocker.patch("decode.tools.files._active_backend", return_value=backend)

    with pytest.raises(ApprovalRequired):
        files.write(_ctx(tmp_path, approved=False), "f.txt", "x")
    assert backend.writes == []


# never-crash: infra failures below the seam render as ModelRetry


class _EscapingBackend:
    """A backend whose file ops raise :class:`WorkspaceEscape` — as ``DockerBackend._path`` does when a
    symlink resolves off the mount. Proves the file tools render that physical-containment refusal (an
    ``OSError``) as a ModelRetry **without** files.py importing the class (ADR-0012 §9 laziness)."""

    def __init__(self) -> None:
        self._escape = WorkspaceEscape(
            "path 'evil' escapes the workspace sandbox (resolves outside the bind mount)"
        )

    async def stat(self, rel: str) -> FileStat | None:
        raise self._escape

    async def read_bytes(self, rel: str) -> bytes:
        raise self._escape

    async def write_bytes(self, rel: str, data: bytes) -> None:
        raise self._escape


@pytest.mark.parametrize(
    "tool_and_args",
    [
        ("read", ("f.txt",)),
        ("write", ("f.txt", "x")),
        ("edit", ("f.txt", "a", "b")),
    ],
)
def test_file_tools_render_a_backend_create_failure_as_model_retry(
    mocker, tmp_path: Path, tool_and_args: tuple[str, tuple[str, ...]]
):
    # Never-crash contract: when the backend cannot be created for this op (bad SANDBOX_IMAGE, daemon
    # died mid-session) ``bash.active_backend`` raises a RuntimeError. The file tools must render it as a
    # ModelRetry (the way ``bash`` renders an exit-125 ExecResult), NOT crash the turn with a traceback.
    # Patches the underlying ``bash.active_backend`` so the real ``files._active_backend`` guard is run.
    mocker.patch(
        "decode.tools.bash.active_backend",
        side_effect=RuntimeError("docker run failed (exit 125): No such image: bogus:latest"),
    )
    name, args = tool_and_args
    tool = getattr(files, name)

    # Raised synchronously by ``_active_backend`` before the async bridge — a direct sync call suffices.
    with pytest.raises(ModelRetry, match="sandbox is unavailable"):
        tool(_ctx(tmp_path), *args)


@pytest.mark.parametrize(
    "tool_and_args",
    [
        ("read", ("evil",)),
        ("write", ("evil", "x")),
        ("edit", ("evil", "a", "b")),
    ],
)
async def test_file_tools_render_a_workspace_escape_as_model_retry(
    mocker, tmp_path: Path, tool_and_args: tuple[str, tuple[str, ...]]
):
    # A symlink escape is caught PHYSICALLY below the seam (``DockerBackend._path`` raises
    # WorkspaceEscape, an OSError). The ``_bridge`` boundary renders it as a ModelRetry carrying the
    # escape message — the read/write/edit is refused, the host file is never touched — and files.py
    # never imports WorkspaceEscape (the broad ``except OSError`` catches it by base class).
    mocker.patch("decode.tools.files._active_backend", return_value=_EscapingBackend())
    name, args = tool_and_args
    tool = getattr(files, name)

    with pytest.raises(ModelRetry, match=r"Sandbox file operation failed.*escapes the workspace"):
        await _call(tool, _ctx(tmp_path), *args)
