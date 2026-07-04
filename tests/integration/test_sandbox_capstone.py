"""The isolated-Workspace capstone: the whole ADR-0012 feature end to end (tasks 078-085).

This is the **living proof** for the isolated Workspace — and it doubles as documentation, in the
style of :mod:`tests.integration.test_milestone1_capstone` (swap only the boundary),
:mod:`tests.integration.test_runtime_capstone` (patch the seam, ``skipif`` the real stack), and
:mod:`tests.integration.test_lsp_capstone` (a real-wire smoke guarded on a binary probe). It replaces
the ADR-0011-era ``SANDBOX_MODE`` capstone (host file tools on the real repo + ``bash`` in a scratch)
with the ADR-0012 model. It has two parts — an **always-run offline slice** and four
**``skipif``-guarded real-infra smokes** — so ``make ci`` stays green on a machine with no Docker / no
Modal (the smokes SKIP, never fail).

**The feature in one paragraph.** In a sandbox mode (``docker`` / ``modal``) the agent's *whole* tool
scope — the file/search tools **and** ``bash`` — is an isolated ``/workspace`` (ADR-0012 §1-4): a
``git clone`` of a user-supplied ``--repo`` (or an empty scratch), backed by the host
``.decode/sandbox`` directory. The two ADR-0011 executors collapse into **one**
:class:`~decode.sandbox.executor.SandboxExecutor` (create → ``exec bash -lc <cmd>`` per call → destroy;
**fresh-exec** — the filesystem persists, ``cd`` / ``export`` do not) over a thin
:class:`~decode.sandbox.executor.SandboxBackend` Protocol that carries **exec + file ops + lifecycle**.
``DockerBackend`` (079) runs ``docker exec`` and does file ops as pathlib on a bind mount; ``ModalBackend``
(080) runs ``sb.exec`` and does file ops via the ``SandboxFilesystem`` API with one bootstrap upload + one
export sweep. The file tools route their byte transport through that same seam (081, the "swap the set"
pattern), so a tool-written file is visible to ``bash`` and vice-versa; ``glob`` / ``grep`` execute in the
sandbox (``find`` / ``grep`` via ``exec``). decode's *own* artifacts stay at **Harness Home** (the launch
cwd) — ``.decode/sessions``, ``MEMORY.md`` / ``AGENTS.md``, the permission file, the skills catalog —
while only ``deps.cwd`` moves into the Workspace (§6). Results survive via a host-side **git hand-back**
(083, §8): the harness secures the final Workspace onto a ``decode/<session-id>`` Session Branch and
pushes it — **every git command host-side**, so no credential ever enters the sandbox. ``none`` mode stays
byte-identical to M1. The Credential Proxy (ADR-0011 §6) and replay-safety ``{"cache": False}`` bash
checkpoint (§5) are retained.

**Part 1 — the always-run offline slice (no docker, no modal, no network, no ``GEMINI_API_KEY``).**
Like the M1 capstone it swaps only the boundaries; everything structural is real:

* **REAL** — the ADR-0002 run seam via the ``none``-mode :class:`~decode.tools.exec.LocalExecutor` (a real
  ``echo`` round-trips through the real :func:`~decode.agent.factory.build_agent` registry + the real
  :class:`~decode.permissions.gate.PermissionGate` to an :class:`~decode.tools.exec.ExecResult`); the
  **real** :class:`~decode.sandbox.executor.SandboxExecutor` driving a **fake backend** (so the fresh-exec
  ``create → exec bash -lc`` contract and the file-tool ⇄ ``bash`` shared-backend seam are exercised with
  no daemon); the real ``SANDBOX_MODE`` → executor-class selection; the real per-mode ``bash`` description;
  the real file-tool containment path-math + ``glob`` / ``grep`` parity (real ``find`` / ``grep`` on a host
  dir); the real host-side git hand-back against **local** repos; the real
  :func:`~decode.sandbox.proxy.build_credential_map`; and the real replay-safety wiring in
  :func:`decode.runtime.flow._build_runtime_agent`.
* **FAKED** — the model is a scripted :class:`~pydantic_ai.models.function.FunctionModel`
  (``GEMINI_API_KEY`` is faked only so ``build_agent`` constructs); the ``SandboxBackend`` is a recording /
  local-exec double injected at the :func:`decode.sandbox.select_executor` (``bash``) and
  :func:`decode.tools.files._active_backend` (file tools) seams (so no container / remote sandbox is
  touched); ``kitaru.get_secret`` is patched for the credential map; the LSP server call is stubbed for the
  Modal-off posture; and the bypass ``KitaruAgent`` build is spied so the replay-safety kwarg is asserted
  without booting a flow.

**Part 2 — the ``skipif``-guarded real-infra smokes.** Each SKIPS (never fails) when its infra is absent,
using the **same** predicates as the executors' own integration tests (a ``docker info`` probe; the
``modal`` credential-presence check): a real ``SandboxExecutor(DockerBackend())`` clone-Workspace
round-trip + host-side hand-back push; a real ``SandboxExecutor(ModalBackend())`` isolated-Workspace
round-trip (bootstrap + direct file ops + export) and a real Modal max-lifetime revival; and the real
docker Credential-Proxy boundary. Each reaps its container / sandbox / network in a ``finally`` so the
suite is hermetic under ``filterwarnings=["error"]`` and leaves no infra litter. The exhaustive
per-backend matrices live in ``test_docker_executor.py`` / ``test_modal_executor.py`` /
``test_workspace_clone.py`` / ``test_credential_proxy.py`` / ``test_handback.py``; this capstone is the
integrated proof they hang together.

**A bug this capstone surfaced (now fixed).** The capstone was the first export-over-a-real-clone, and
it caught a limitation the exhaustive matrices missed: the modal export sweep
(:func:`decode.sandbox.workspace.extract_tar`) could not overwrite a ``git clone``'s **read-only ``.git``
loose objects** (mode 0444) — ``extractall`` aborted with ``PermissionError``, so a modal session's
export-over-a-clone brought nothing down and the modal git hand-back to a ``--repo`` origin captured no
work (docker is unaffected — its bind mount is live, no extract). ``extract_tar`` now makes the
destination tree owner-writable before extracting, so the sweep lands over a clone.
:func:`test_modal_export_over_a_clone_round_trips_git` pins the fix hermetically (agent work + a valid
``.git`` survive the sweep).
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import anyio
import pytest
from pydantic import SecretStr
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.config.settings import settings
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import TurnContext
from decode.permissions.gate import PermissionGate
from decode.sandbox.docker_backend import DockerBackend
from decode.sandbox.executor import FileStat, SandboxExecutor, WorkspaceEscape
from decode.sandbox.handback import ShipResult, ship_workspace
from decode.sandbox.modal_backend import ModalBackend
from decode.sandbox.proxy import (
    DEFAULT_PROXY_RULES,
    DockerCredentialProxy,
    SandboxProxyRule,
    build_credential_map,
)
from decode.sandbox.workspace import prepare_workspace
from decode.tools import bash as bash_mod
from decode.tools import files as files_mod
from decode.tools.askuser import deny_user_question_resolver
from decode.tools.exec import ExecResult, LocalExecutor

_BASH = bash_mod.BASH_TOOL_NAME


# ================================================================================================
# Hermeticity fixtures — a faked key so ``build_agent`` constructs offline. (The rootdir conftest
# already pins ``SANDBOX_MODE=none`` and resets the ``bash`` executor memo around every test.)
# ================================================================================================


@pytest.fixture(autouse=True)
def _fake_gemini_key(mocker) -> None:
    """Let ``build_agent`` construct the Gemini provider offline (the model is always overridden)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


@pytest.fixture
def no_lsp_enrichment(mocker) -> None:
    """Stub the post-edit LSP enricher to identity so a ``.py`` write/edit spawns no ``ty`` (ADR-0007).

    The byte-transport routing tests pin the file-tool *seam*, not the orthogonal ``ty`` enrichment
    (its sandbox posture has its own test below and in ``test_files.py`` / ``test_lsp.py``). Without
    this, editing a ``.py`` file through the seam would reach the real enricher, spawn a server nothing
    here shuts down, and leak its subprocess pipe transports under ``filterwarnings=["error"]``.
    """
    mocker.patch("decode.tools.files._enrich", new=lambda base, cwd, path: base)


# ================================================================================================
# Shared SandboxBackend doubles (mirroring tests/unit/decode/tools/test_files_sandbox.py) + scripted
# drivers — a REAL decode agent (full ``build_agent`` registry) on a scripted FunctionModel, driven
# through the REAL interactive gated loop so the run seam + permission gate are exercised.
# ================================================================================================


class _RecordingBackend:
    """A recording :class:`~decode.sandbox.executor.SandboxBackend`: records exec + file-op calls.

    Wrapped in the **real** :class:`~decode.sandbox.executor.SandboxExecutor` (or injected at the file
    seam) so the fresh-exec contract and the byte-transport routing are proven with **no** container /
    remote sandbox. ``create_calls`` proves one sandbox per session; ``exec_args`` proves one
    ``bash -lc`` exec per call; ``reads`` / ``writes`` / ``stat_calls`` prove file ops route on **logical**
    (workspace-relative) paths.
    """

    def __init__(self) -> None:
        self.create_calls = 0
        self.export_calls = 0
        self.destroy_calls = 0
        self.exec_args: list[tuple[str, ...]] = []
        self.reads: list[str] = []
        self.writes: list[tuple[str, bytes]] = []
        self.stat_calls: list[str] = []
        self._stats: dict[str, FileStat] = {}
        self._bytes: dict[str, bytes] = {}
        self.exec_result = ExecResult(stdout="", stderr="", exit_code=0, timed_out=False)

    def seed_file(self, rel: str, data: bytes) -> None:
        self._bytes[rel] = data
        self._stats[rel] = FileStat(path=rel, is_dir=False, size=len(data))

    def seed_dir(self, rel: str) -> None:
        self._stats[rel] = FileStat(path=rel, is_dir=True, size=0)

    async def create(self, workspace: Path) -> None:
        self.create_calls += 1

    async def exec(self, *args: str, timeout_s: float) -> ExecResult:
        self.exec_args.append(args)
        return self.exec_result

    async def read_bytes(self, rel: str) -> bytes:
        self.reads.append(rel)
        if rel not in self._bytes:
            raise FileNotFoundError(rel)
        return self._bytes[rel]

    async def write_bytes(self, rel: str, data: bytes) -> None:
        self.writes.append((rel, data))
        self._bytes[rel] = data
        self._stats[rel] = FileStat(path=rel, is_dir=False, size=len(data))

    async def make_directory(self, rel: str) -> None:
        self._stats[rel] = FileStat(path=rel, is_dir=True, size=0)

    async def stat(self, rel: str) -> FileStat | None:
        self.stat_calls.append(rel)
        return self._stats.get(rel)

    async def list_dir(self, rel: str) -> list[FileStat]:
        return [st for key, st in self._stats.items() if key != rel]

    async def remove(self, rel: str) -> None:
        self._bytes.pop(rel, None)
        self._stats.pop(rel, None)

    async def export(self) -> None:
        self.export_calls += 1

    async def destroy(self) -> None:
        self.destroy_calls += 1


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


class _EscapingBackend:
    """A backend whose file ops raise :class:`~decode.sandbox.executor.WorkspaceEscape`.

    Mirrors :meth:`~decode.sandbox.docker_backend.DockerBackend._path` when a symlink planted in the
    Workspace resolves off the bind mount. Proves the file layer renders that physical-containment
    refusal (an :class:`OSError`) as a :class:`~pydantic_ai.ModelRetry` **without** ``files.py`` importing
    the class (the §9 laziness invariant — the broad ``except OSError`` catches it by base class).
    """

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


def _last_request_has_tool_return(messages: list[Any]) -> bool:
    """True when the most recent request carries a tool result (i.e. this is a resume leg)."""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return any(isinstance(part, ToolReturnPart) for part in message.parts)
    return False


def _bash_stream_model(command: str) -> FunctionModel:
    """A streaming model that calls ``bash(command)`` on the fresh leg, then ends the turn with text."""

    async def stream_function(messages: list[Any], info: AgentInfo):
        if _last_request_has_tool_return(messages):
            yield "the command ran"
            return
        yield {0: DeltaToolCall(name=_BASH, json_args=json.dumps({"command": command}))}

    return FunctionModel(stream_function=stream_function)


async def _drive_one_gated_bash_turn(
    command: str, *, cwd: Path
) -> tuple[list[str], list[events.Event]]:
    """Drive ONE gated ``bash(command)`` through the real agent + gate + registry; return returns+events.

    Builds the real :func:`~decode.agent.factory.build_agent` agent, overrides its model with
    :func:`_bash_stream_model`, and runs it through the real :class:`~decode.agent.loop.AgentTurnHandler`
    with a real :class:`~decode.permissions.gate.PermissionGate` + an approving resolver. Which executor
    runs the command is whatever ``SANDBOX_MODE`` selects (``none`` → the real
    :class:`~decode.tools.exec.LocalExecutor`; a sandbox mode → whatever :func:`decode.sandbox.select_executor`
    is patched to return). Returns every ``bash`` tool-return string and the emitted events.
    """
    emitted: list[events.Event] = []

    async def _approve(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=cwd,
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=_approve,
        resolve_user_question=deny_user_question_resolver,
    )
    agent = build_agent()
    handler = AgentTurnHandler(agent, deps=deps)

    async def _run() -> None:
        agen = handler(TurnContext(0, "run a command", emitted.append))
        with contextlib.suppress(StopAsyncIteration):
            await agen.asend(None)
            while True:
                await agen.asend([])
        await agen.aclose()

    with agent.override(model=_bash_stream_model(command)):
        await _run()

    returns = [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == _BASH
    ]
    return returns, emitted


# ================================================================================================
# PART 1 — the always-run offline slice.
# ================================================================================================

# ------------------------------------------------------------------------------------------------
# 1. The one-seam fresh-exec contract — a command round-trips SandboxExecutor (over a fake backend)
#    through the real ``bash`` registry + gate to a rendered ExecResult; ``none`` is byte-identical.
# ------------------------------------------------------------------------------------------------


async def test_none_mode_command_round_trips_the_run_seam_and_renders(tmp_path: Path) -> None:
    """``none`` mode: a real ``echo`` runs through the real gate + the host LocalExecutor and renders.

    The default-path anchor (byte-identical to M1). A gated ``bash`` is surfaced to the permission gate
    and approved, the real :class:`~decode.tools.exec.LocalExecutor` runs the command under ``cwd``, and
    its :class:`~decode.tools.exec.ExecResult` renders the exit code + stdout back to the model.
    """
    returns, emitted = await _drive_one_gated_bash_turn("echo capstone-none-ok", cwd=tmp_path)

    assert any(isinstance(e, events.PermissionRequested) and e.name == _BASH for e in emitted), (
        "the gated bash call must be surfaced to the permission gate"
    )
    assert returns, "the bash result must reach the model as a tool return"
    assert any("Exit code: 0" in r and "capstone-none-ok" in r for r in returns)


async def test_sandbox_command_round_trips_the_real_executor_over_a_fake_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``docker`` mode: a gated ``bash`` round-trips the **real** SandboxExecutor to a fake backend.

    The one-seam contract (ADR-0012 §2): ``SandboxExecutor`` is real; only the ``SandboxBackend`` is a
    fake injected at :func:`decode.sandbox.select_executor` (no daemon). The gated ``bash`` — driven
    through the real agent + gate — must create the sandbox **once** and run the command as a fresh
    ``exec("bash", "-lc", <cmd>)``, whose :class:`~decode.tools.exec.ExecResult` renders back to the model.
    """
    backend = _RecordingBackend()
    backend.exec_result = ExecResult("sandboxed-ok", "", 0, timed_out=False)
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: SandboxExecutor(backend))
    bash_mod.reset_executor()

    returns, _ = await _drive_one_gated_bash_turn("echo sandbox-seam-ok", cwd=tmp_path)

    assert backend.create_calls == 1  # the sandbox was created exactly once for the session
    assert backend.exec_args == [("bash", "-lc", "echo sandbox-seam-ok")]  # one fresh bash -lc exec
    assert any("sandboxed-ok" in r and "Exit code: 0" in r for r in returns)


async def test_sandbox_executor_is_fresh_exec_one_create_many_execs(tmp_path: Path) -> None:
    """Fresh-exec (ADR-0012 §2): the sandbox is created ONCE, each ``run`` is a new ``bash -lc`` exec.

    Directly on the real :class:`~decode.sandbox.executor.SandboxExecutor` over a fake backend (no
    agent, no infra): two ``run`` calls create the backend once (the filesystem persists) but exec twice
    — no persistent shell, so ``cd`` / ``export`` cannot carry over (each is a brand-new process).
    """
    backend = _RecordingBackend()
    executor = SandboxExecutor(backend)

    await executor.run("echo a", cwd=tmp_path, timeout_s=5.0)
    await executor.run("echo b", cwd=tmp_path, timeout_s=5.0)

    assert backend.create_calls == 1  # one sandbox for the session (memoized)
    assert backend.exec_args == [("bash", "-lc", "echo a"), ("bash", "-lc", "echo b")]


def test_none_mode_rendering_is_byte_identical_with_an_empty_note() -> None:
    """An empty ``note`` (every ``none``-mode LocalExecutor result) renders exactly as before the field."""
    result = ExecResult(stdout="hi\n", stderr="", exit_code=0, timed_out=False)

    assert bash_mod._render(result, timeout_s=120.0) == "Exit code: 0.\n\nstdout:\nhi"


# ------------------------------------------------------------------------------------------------
# 2. File tools through the seam — read/write/edit route byte transport through the backend on logical
#    paths; glob/grep execute in the sandbox; ``none`` is direct pathlib; containment is layered.
# ------------------------------------------------------------------------------------------------


async def test_file_tools_and_bash_share_one_session_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_lsp_enrichment: None
) -> None:
    """ADR-0012 §4: ``bash`` and the file tools route through ONE session backend (a tool-written file
    is visible to ``bash`` and vice-versa).

    The integration highlight: a gated ``bash`` turn AND a ``write`` tool call run in one session through
    the **real** seams (``select_executor`` → :class:`~decode.sandbox.executor.SandboxExecutor` →
    :meth:`~decode.sandbox.executor.SandboxExecutor.file_backend`, the file tool bridging sync→async via
    :func:`anyio.from_thread.run`). BOTH reach the **same** fake backend, created exactly once — proving
    the one container / remote sandbox per session ADR-0012 promises.
    """
    backend = _RecordingBackend()
    backend.exec_result = ExecResult("hi\n", "", 0, timed_out=False)
    executor = SandboxExecutor(backend)  # the real executor over the fake backend
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: executor)
    bash_mod.reset_executor()

    # (a) a gated bash turn → SandboxExecutor.run → backend.exec ...
    returns, _ = await _drive_one_gated_bash_turn("echo hi", cwd=tmp_path)
    # (b) ... and a file-tool write → files._active_backend → bash.active_backend → executor.file_backend
    #     → the SAME backend (the real seam, not a patched _active_backend).
    out = await _call(files_mod.write, _ctx(tmp_path), "note.txt", "from the file tool")

    assert backend.create_calls == 1  # ONE session backend, shared by bash + the file tools
    assert ("bash", "-lc", "echo hi") in backend.exec_args  # bash routed through it
    assert ("note.txt", b"from the file tool") in backend.writes  # the file tool routed through it
    assert out == "Wrote 'note.txt' (18 characters)."
    assert any("Exit code: 0" in r for r in returns)


async def test_read_write_edit_route_through_the_backend_on_logical_paths(
    mocker, tmp_path: Path, no_lsp_enrichment: None
) -> None:
    """``read`` / ``write`` / ``edit`` route their byte transport through the backend on logical paths (§4).

    With the fake backend injected at :func:`decode.tools.files._active_backend`: ``read`` reads the
    logical path and renders the SAME numbering as ``none`` mode; ``write`` writes logical-path bytes
    through the seam (nothing lands on the host cwd); ``edit`` reuses the shared unique-match replacement.
    """
    backend = _RecordingBackend()
    backend.seed_file("sub/f.py", b"x = 1\ny = 2\n")
    mocker.patch("decode.tools.files._active_backend", return_value=backend)

    out = await _call(files_mod.read, _ctx(tmp_path), "sub/f.py")
    assert backend.reads == ["sub/f.py"]  # the logical (workspace-relative) path
    assert out == "1\tx = 1\n2\ty = 2"  # same shared numbering as none mode

    await _call(files_mod.write, _ctx(tmp_path), "new/g.txt", "hi there")
    assert ("new/g.txt", b"hi there") in backend.writes  # logical path + bytes through the seam
    assert not (tmp_path / "new" / "g.txt").exists()  # nothing on the host cwd

    await _call(files_mod.edit, _ctx(tmp_path), "sub/f.py", "y = 2", "y = 3")
    assert (
        "sub/f.py",
        b"x = 1\ny = 3\n",
    ) in backend.writes  # unique-match replace through the seam


@pytest.mark.parametrize("pattern", ["*.py", "**/*.py", "src/**/*.py"])
async def test_glob_has_output_parity_with_none_mode(mocker, tmp_path: Path, pattern: str) -> None:
    """``glob`` executes in the sandbox (real ``find`` via the local-exec backend) with none-mode parity (§4)."""
    _seed_tree(tmp_path)
    none_out = files_mod.glob(_ctx(tmp_path), pattern)  # sandbox_mode=none → direct pathlib

    mocker.patch("decode.tools.files._active_backend", return_value=_LocalBackend(tmp_path))
    sandbox_out = await _call(files_mod.glob, _ctx(tmp_path), pattern)

    assert sandbox_out == none_out


@pytest.mark.parametrize("kwargs", [{}, {"path": "b.py"}, {"glob": "**/*.py"}])
async def test_grep_has_output_parity_with_none_mode(mocker, tmp_path: Path, kwargs: dict) -> None:
    """``grep`` executes in the sandbox (real ``grep`` via the local-exec backend) with none-mode parity (§4)."""
    _seed_tree(tmp_path)
    none_out = files_mod.grep(_ctx(tmp_path), "TODO", **kwargs)

    mocker.patch("decode.tools.files._active_backend", return_value=_LocalBackend(tmp_path))
    sandbox_out = await _call(files_mod.grep, _ctx(tmp_path), "TODO", **kwargs)

    assert sandbox_out == none_out


@pytest.mark.parametrize(
    "tool_and_args",
    [
        ("read", ("../secret.txt",)),
        ("write", ("../evil.txt", "x")),
        ("edit", ("../evil.txt", "a", "b")),
    ],
)
def test_containment_rejects_dotdot_before_touching_the_backend(
    mocker, tmp_path: Path, tool_and_args: tuple[str, tuple[str, ...]]
) -> None:
    """Containment: the backend-agnostic path-math rejects a ``..`` escape BEFORE any backend op (§4).

    The shared :func:`decode.tools.files._resolve_logical` refuses the escape synchronously (a logical
    fold, never host ``Path.resolve`` — a modal path is not a host path), so the recording backend
    records nothing (no leak). The docker backend *additionally* raises
    :class:`~decode.sandbox.executor.WorkspaceEscape` for a symlink below the seam — the next test.
    """
    backend = _RecordingBackend()
    backend.seed_file("../secret.txt", b"SECRET")  # even if it "existed", it must never be reached
    mocker.patch("decode.tools.files._active_backend", return_value=backend)
    name, args = tool_and_args

    with pytest.raises(ModelRetry, match="outside the working directory"):
        getattr(files_mod, name)(_ctx(tmp_path), *args)

    assert backend.reads == [] and backend.writes == [] and backend.stat_calls == []


async def test_symlink_escape_below_the_seam_renders_a_refusal(mocker, tmp_path: Path) -> None:
    """A symlink resolving off the mount (docker's physical layer) renders a refusal, not a crash (§4).

    A real-fs backend raises :class:`~decode.sandbox.executor.WorkspaceEscape` (an :class:`OSError`) when
    a Workspace symlink resolves onto the host. The ``_bridge`` boundary renders it as a
    :class:`~pydantic_ai.ModelRetry` — and ``files.py`` never imports the class (the §9 laziness invariant).
    """
    mocker.patch("decode.tools.files._active_backend", return_value=_EscapingBackend())

    with pytest.raises(ModelRetry, match=r"Sandbox file operation failed.*escapes the workspace"):
        await _call(files_mod.read, _ctx(tmp_path), "evil")


def test_none_mode_file_tools_use_direct_pathlib_byte_identical(tmp_path: Path) -> None:
    """``none`` mode: the seam is never engaged — file tools take the direct-pathlib path (byte-identical)."""
    assert bash_mod.active_backend(tmp_path) is None  # no seam in none mode

    out = files_mod.write(_ctx(tmp_path), "host.txt", "on disk")

    assert (tmp_path / "host.txt").read_text(
        encoding="utf-8"
    ) == "on disk"  # landed on the host cwd
    assert out == "Wrote 'host.txt' (7 characters)."


# ------------------------------------------------------------------------------------------------
# 3. Selection swap · harness-home split · unified description · workspace prep · LSP · web_fetch.
# ------------------------------------------------------------------------------------------------


def test_sandbox_mode_selects_the_matching_executor_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SANDBOX_MODE`` → the right executor class through the REAL ``select_executor``, inertly (ADR-0012 §2).

    ``none`` keeps the host :class:`~decode.tools.exec.LocalExecutor`; ``docker`` /``modal`` each yield a
    :class:`~decode.sandbox.executor.SandboxExecutor` over the matching backend. Construction is inert for
    all three (no container, no remote sandbox, no ``modal`` SDK import), so no daemon is needed.
    """
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "none")
    bash_mod.reset_executor()
    assert isinstance(bash_mod._get_executor(), LocalExecutor)

    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    bash_mod.reset_executor()
    docker_executor = bash_mod._get_executor()
    assert isinstance(docker_executor, SandboxExecutor)
    assert isinstance(docker_executor._backend, DockerBackend)

    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "modal")
    bash_mod.reset_executor()
    modal_executor = bash_mod._get_executor()
    assert isinstance(modal_executor, SandboxExecutor)
    assert isinstance(modal_executor._backend, ModalBackend)


async def test_harness_home_split_memory_and_skills_read_home_not_the_workspace_cwd(
    tmp_path: Path,
) -> None:
    """ADR-0012 §6: memory + the skills catalog are harness artifacts → read from ``harness_home``.

    With ``cwd`` (the Workspace) and ``harness_home`` (the launch cwd) distinct, the ``AGENTS.md`` and the
    project skill under Harness Home reach the model's assembled instructions, while an ``AGENTS.md`` that
    only exists in the Workspace does NOT — the file/search tools use ``cwd`` (proven above), the harness
    artifacts use ``harness_home``.
    """
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (home / "AGENTS.md").write_text("HARNESS-HOME RULE: from home", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("WORKSPACE RULE: should not load", encoding="utf-8")
    skill = home / ".decode" / "skills" / "greetcap"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: greetcap\ndescription: a capstone skill\n---\nbody\n", encoding="utf-8"
    )
    deps = AgentDeps(
        cwd=workspace,
        harness_home=home,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    captured: dict[str, str] = {}

    def model_fn(messages: list[Any], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        captured["instructions"] = (
            first.instructions or "" if isinstance(first, ModelRequest) else ""
        )
        return ModelResponse(parts=[TextPart(content="ok")])

    agent = build_agent()
    with agent.override(model=FunctionModel(model_fn)):
        await agent.run("hi", deps=deps)

    instructions = captured["instructions"]
    assert "HARNESS-HOME RULE: from home" in instructions  # memory read from Harness Home
    assert "WORKSPACE RULE" not in instructions  # NOT the workspace cwd
    assert "greetcap" in instructions  # the skills catalog read from Harness Home too


async def _bash_description_the_model_sees(mode: str, monkeypatch, cwd: Path) -> str:
    """Build a real agent in ``mode`` and return the ``bash`` description the model is actually handed."""
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", mode)
    agent = build_agent()
    captured: dict[str, str | None] = {}

    def model_fn(messages: list[Any], info: AgentInfo) -> ModelResponse:
        captured["bash"] = next(
            (t.description for t in info.function_tools if t.name == _BASH), None
        )
        return ModelResponse(parts=[TextPart(content="ok")])

    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    with agent.override(model=FunctionModel(model_fn)):
        await agent.run("hi", deps=deps)
    description = captured["bash"]
    assert description is not None  # the build persona exposes bash
    return description


async def test_bash_description_is_unified_across_backends(monkeypatch, tmp_path: Path) -> None:
    """The model-facing ``bash`` description: ``none`` is the base; ``docker`` == ``modal`` add ONE paragraph.

    ``docker`` / ``modal`` == ``none`` + the unified suffix transitively proves ``none`` is byte-identical
    to the untouched base (no sandbox paragraph leaks into it); the ONE shared paragraph reflects the
    single fresh-exec ``SandboxExecutor`` shape both backends collapsed onto (ADR-0012 §2).
    """
    none_desc = await _bash_description_the_model_sees("none", monkeypatch, tmp_path)
    docker_desc = await _bash_description_the_model_sees("docker", monkeypatch, tmp_path)
    modal_desc = await _bash_description_the_model_sees("modal", monkeypatch, tmp_path)

    assert "/workspace" not in none_desc and "isolated Workspace" not in none_desc
    assert docker_desc == modal_desc == f"{none_desc}\n\n{bash_mod._SANDBOX_DESCRIPTION_SUFFIX}"
    assert "isolated Workspace" in docker_desc  # /workspace IS the Workspace (a clone, or empty)
    assert "do NOT carry over" in docker_desc  # fresh-exec: cd/export do not persist


def test_offline_clone_populates_the_workspace(tmp_path: Path) -> None:
    """Workspace prep (ADR-0012 §3): a local ``--repo`` is host-side cloned into ``.decode/sandbox`` at HEAD.

    Hermetic (a local git repo, no network): the committed tree lands in the Workspace with a real
    ``.git`` and a recoverable origin — the substrate the hand-back branches, secures, and pushes.
    """
    source = _make_git_repo(tmp_path / "source", content="cloned-into-workspace\n")

    workspace = prepare_workspace(tmp_path / "home", repo=str(source))

    assert workspace == (tmp_path / "home" / ".decode" / "sandbox").resolve()
    assert (workspace / "README.md").read_text(encoding="utf-8") == "cloned-into-workspace\n"
    assert (workspace / ".git").is_dir()
    assert _git_out(workspace, "remote", "get-url", "origin") == str(source)


def test_repo_with_none_mode_is_a_friendly_guard_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--repo`` / ``SANDBOX_REPO`` with ``SANDBOX_MODE=none`` is a friendly config error (ADR-0012 §3).

    The clone-at-launch only makes sense in a sandbox mode (the Workspace only exists there), so a
    resolved repo with ``none`` mode returns the friendly guard line; no repo is always fine, and a
    sandbox mode is fine.
    """
    from decode import cli

    monkeypatch.setattr(cli.settings, "sandbox_mode", "none")
    assert cli._sandbox_repo_config_error("some/repo") == cli._SANDBOX_REPO_NONE_MODE_MESSAGE
    assert cli._sandbox_repo_config_error(None) is None  # no repo → always fine

    monkeypatch.setattr(cli.settings, "sandbox_mode", "docker")
    assert cli._sandbox_repo_config_error("some/repo") is None  # a sandbox mode is fine


def test_lsp_posture_none_and_docker_enrich_but_modal_is_best_effort_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LSP posture (ADR-0012 §7): the post-edit enricher runs in ``none`` / ``docker``, off in ``modal``.

    ``ty`` is host-side: in ``none`` (the repo tree) and ``docker`` (the live bind mount) it can open the
    just-written file, so the errors-only block is appended; in ``modal`` it cannot reach the remote
    filesystem, so the enricher is best-effort-disabled and returns the write result unchanged. The LSP
    call is stubbed so no real server spawns.
    """
    diagnostics = [SimpleNamespace(severity=1, line=3, column=5, message="undefined name")]
    monkeypatch.setattr(files_mod.lsp_service, "diagnostics_on_edit", lambda cwd, path: diagnostics)
    monkeypatch.setattr(files_mod.settings, "lsp_enabled", True)
    monkeypatch.setattr(files_mod.settings, "lsp_diagnostics_on_edit", True)
    base = "Wrote 'x.py' (10 characters)."

    for mode in ("none", "docker"):
        monkeypatch.setattr(files_mod.settings, "sandbox_mode", mode)
        enriched = files_mod._enrich(base, tmp_path, "x.py")
        assert enriched != base and "undefined name" in enriched  # ty ran, errors appended

    monkeypatch.setattr(files_mod.settings, "sandbox_mode", "modal")
    assert files_mod._enrich(base, tmp_path, "x.py") == base  # best-effort-off: unchanged


@pytest.mark.parametrize("mode", ["none", "docker", "modal"])
async def test_web_fetch_stays_gated_in_every_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """``web_fetch`` stays gated even in a sandbox mode (ADR-0012 §7) — it reaches the host network.

    A sandbox mode contains the file tools + ``bash`` in the Workspace but does NOT un-gate ``web_fetch``:
    an unapproved call defers to the gate (raises :class:`~pydantic_ai.ApprovalRequired`) **before** any
    connection is opened, in every mode.
    """
    from decode.tools.web import web_fetch

    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", mode)

    with pytest.raises(ApprovalRequired):
        await web_fetch(_ctx(tmp_path, approved=False), "https://example.com")


# ------------------------------------------------------------------------------------------------
# 4. Git hand-back (offline, hermetic) — a local repo + a local ``--repo`` origin, NO network.
# ------------------------------------------------------------------------------------------------


def test_dirty_workspace_lands_on_a_local_branch_even_when_push_fails(tmp_path: Path) -> None:
    """A dirty Workspace lands on ``decode/<id>`` locally with the uncommitted work — push failing (§8).

    The never-lose-results core: the local branch + its capture commit are created BEFORE the push, so an
    unreachable origin still leaves the results on a local branch. Push failure is forced by deleting the
    origin source (recovery reads only local refs, so it still works).
    """
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    (workspace / "agent_work.txt").write_text("uncommitted results\n", encoding="utf-8")
    import shutil

    shutil.rmtree(source)  # make the push impossible; the local git stays intact

    result = ship_workspace(home, repo=str(source), session_id="abcd1234-5678-9012")

    assert isinstance(result, ShipResult)
    assert result.pushed is False
    assert result.branch == "decode/abcd1234"
    assert ".decode/sandbox" in result.message  # names the local branch's location
    assert _branch_exists(workspace, "decode/abcd1234")
    assert "agent_work.txt" in _git_out(workspace, "ls-tree", "--name-only", "decode/abcd1234")


def test_push_to_local_origin_lands_the_branch(tmp_path: Path) -> None:
    """``git push origin decode/<id>`` lands the branch in the local source repo, credential-free (§8)."""
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    _commit_change(workspace, filename="CHANGE.md", content="agent work\n")

    result = ship_workspace(home, repo=str(source), session_id="deadbeef-1111")

    assert result.pushed is True
    assert result.branch == "decode/deadbeef"
    assert _branch_exists(source, "decode/deadbeef")
    assert "CHANGE.md" in _git_out(source, "ls-tree", "--name-only", "decode/deadbeef")


def test_unchanged_workspace_ships_nothing(tmp_path: Path) -> None:
    """A clean Workspace with ``HEAD == origin/HEAD`` (no work) is skipped: branch=None (§8)."""
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    _clone_workspace(source, home)  # a fresh clone: clean AND HEAD == origin/HEAD

    result = ship_workspace(home, repo=str(source), session_id="11112222-3333")

    assert result.branch is None
    assert result.pushed is False
    assert "unchanged" in result.message.lower()


def test_no_repo_ships_nothing_the_friendly_none_mode_line(tmp_path: Path) -> None:
    """No ``--repo`` (``none`` / no-repo auto-ship + ``/ship``) is a friendly no-op, never a crash (§8)."""
    home = tmp_path / "home"

    result = ship_workspace(home, repo=None, session_id="whatever-5555")

    assert result.branch is None
    assert result.pushed is False
    assert "nothing to hand back" in result.message.lower()


def test_handback_git_runs_host_side_never_through_the_sandbox_seam(mocker, tmp_path: Path) -> None:
    """The security crux (ADR-0012 §8): every git op is a HOST ``git`` — no credential enters a sandbox.

    Records every subprocess the hand-back makes and asserts each is a host ``git`` against
    ``.decode/sandbox`` (never ``executor.run`` / ``backend.exec``), that no credential/token is injected
    into any env, and — structurally — that the hand-back module imports **no** sandbox executor/backend
    seam, so a git command *cannot* route through one.
    """
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    (workspace / "work.txt").write_text("results\n", encoding="utf-8")

    calls: list[tuple[list[str], dict]] = []
    real_run = subprocess.run

    def _record(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return real_run(cmd, **kwargs)

    mocker.patch("decode.sandbox.handback.subprocess.run", side_effect=_record)

    result = ship_workspace(home, repo=str(source), session_id="beefcafe-8888")

    assert result.branch == "decode/beefcafe"
    assert calls, "the hand-back must run git commands"
    for cmd, kwargs in calls:
        assert cmd[0] == "git"  # the HOST git binary, never a sandbox seam
        assert "-C" in cmd and str(workspace) in cmd  # host-side, against .decode/sandbox
        env = kwargs.get("env")
        if env is not None:
            assert set(env) - set(os.environ) <= {"GIT_TERMINAL_PROMPT"}  # no injected credential

    import decode.sandbox.handback as handback_module

    for seam in (
        "SandboxExecutor",
        "SandboxBackend",
        "DockerBackend",
        "ModalBackend",
        "select_executor",
    ):
        assert not hasattr(handback_module, seam)


# ------------------------------------------------------------------------------------------------
# 5. Credential map (retained) · replay-safety config · REPL-free (none imports no sandbox module;
#    importing ``decode.cli`` imports no kitaru).
# ------------------------------------------------------------------------------------------------

_CRED_SECRET = "ghp_capstone_secret_token_value"


def _patch_get_secret(mocker, values_by_name: dict[str, dict[str, str]]):
    """Patch ``kitaru.get_secret`` to return a fake ``Secret`` (``.values``) per name (no real store)."""

    def _fake(name: str):
        return SimpleNamespace(values=values_by_name[name])

    return mocker.patch("kitaru.get_secret", side_effect=_fake)


def test_build_credential_map_resolves_templates_hermetically(mocker) -> None:
    """``build_credential_map`` resolves a ``{{ name.key }}`` header template host-side (ADR-0011 §6)."""
    _patch_get_secret(mocker, {"github-token": {"value": _CRED_SECRET}})
    rules = [
        SandboxProxyRule(
            name="github-auth",
            hosts=["api.github.com"],
            headers={"Authorization": "Bearer {{ github-token.value }}"},
        )
    ]

    assert build_credential_map(rules) == {
        "api.github.com": {"Authorization": f"Bearer {_CRED_SECRET}"}
    }


def test_default_proxy_rules_ship_empty_and_yield_a_passthrough_map(mocker) -> None:
    """The shipped rule set is empty (opt-in) → an empty credential map (a passthrough proxy)."""
    spy = _patch_get_secret(mocker, {})

    assert DEFAULT_PROXY_RULES == []
    assert build_credential_map(DEFAULT_PROXY_RULES) == {}
    spy.assert_not_called()  # an empty rule set fetches no secret


def test_build_credential_map_logs_names_never_values(mocker, caplog) -> None:
    """The resolved secret value never reaches a log line — only rule / host / header NAMES (task-061)."""
    _patch_get_secret(mocker, {"github-token": {"value": _CRED_SECRET}})
    rules = [
        SandboxProxyRule(
            name="github-auth",
            hosts=["api.github.com"],
            headers={"Authorization": "Bearer {{ github-token.value }}"},
        )
    ]

    with caplog.at_level(logging.DEBUG, logger="decode.sandbox.proxy"):
        build_credential_map(rules)

    assert _CRED_SECRET not in caplog.text  # the resolved value never appears
    assert "github-auth" in caplog.text  # the rule name does (for correlation)
    assert "Authorization" in caplog.text  # the header name does


def _spy_runtime_agent_kwargs(monkeypatch, *, mode: str) -> dict[str, Any]:
    """Call ``flow._build_runtime_agent`` with a spied ``KitaruAgent`` and return its kwargs (no stack)."""
    import decode.runtime.flow as flow_mod

    captured: dict[str, Any] = {}

    def _fake_kitaru_agent(agent: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(agent=agent, kwargs=kwargs)

    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", mode)
    monkeypatch.setattr(
        flow_mod, "build_agent", lambda flow_mode=True, model=None: SimpleNamespace()
    )
    monkeypatch.setattr(flow_mod, "KitaruAgent", _fake_kitaru_agent)

    flow_mod._build_runtime_agent()
    return captured


def test_sandbox_bypass_agent_gets_the_cache_false_bash_checkpoint(monkeypatch) -> None:
    """``docker`` mode: the bypass durable agent re-executes ``bash`` on replay (``{"cache": False}``, §5)."""
    import decode.runtime.flow as flow_mod

    captured = _spy_runtime_agent_kwargs(monkeypatch, mode="docker")

    assert captured["tool_checkpoint_config_by_name"] == {_BASH: {"cache": False}}
    assert captured["name"] == flow_mod.RUNTIME_AGENT_NAME
    assert captured["checkpoint_strategy"] == "calls"  # the replay-ready default


def test_none_mode_bypass_agent_is_byte_identical_without_the_replay_safety_kwarg(
    monkeypatch,
) -> None:
    """``none`` mode: the bypass durable agent build carries NO replay-safety kwarg (byte-identical)."""
    captured = _spy_runtime_agent_kwargs(monkeypatch, mode="none")

    assert "tool_checkpoint_config_by_name" not in captured


def _run_isolated(code: str, *, mode: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter with a pinned ``SANDBOX_MODE`` (a clean ``sys.modules``)."""
    env = {
        **os.environ,
        "SANDBOX_MODE": mode,
        "GEMINI_API_KEY": "test-key",
        "LLM_PROVIDER": "gemini",
        "DECODE_LOG_FILE": "",
    }
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)


def test_none_mode_agent_imports_no_sandbox_executor_module() -> None:
    """``none`` mode keeps the eager host executor and imports NO sandbox executor module (ADR-0012 §9)."""
    code = (
        "import sys; "
        "import decode.tools.bash as b; "
        "from decode.agent.factory import build_agent; "
        "build_agent(); "
        "b._get_executor(); "
        "leaked = [m for m in "
        "('decode.sandbox.docker_backend', 'decode.sandbox.executor', "
        "'decode.sandbox.modal_backend') if m in sys.modules]; "
        "assert not leaked, leaked; "
        "print('OK')"
    )
    result = _run_isolated(code, mode="none")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_the_cli_imports_no_kitaru() -> None:
    """The REPL entrypoint stays kitaru-free: only ``decode run`` imports kitaru, lazily (ADR-0012 §9)."""
    result = subprocess.run(
        [sys.executable, "-c", "import decode.cli, sys; assert 'kitaru' not in sys.modules"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ================================================================================================
# The offline fixtures + git helpers the git-hand-back tests (§4) and the real smokes (Part 2) share.
# ================================================================================================


def _seed_tree(root: Path) -> None:
    """A small source tree for the glob/grep parity tests (mirrors test_files_sandbox)."""
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "a.py").write_text("import os\nprint('a')\n", encoding="utf-8")
    (root / "b.py").write_text("x = 1  # TODO tidy\n", encoding="utf-8")
    (root / "c.txt").write_text("plain text TODO\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("def main():\n    return 1  # TODO\n", encoding="utf-8")
    (root / "src" / "pkg" / "util.py").write_text("def util():\n    pass\n", encoding="utf-8")
    (root / "docs" / "note.md").write_text("a TODO here\n", encoding="utf-8")


def _git(cwd: Path, *args: str) -> None:
    """Run ``git <args>`` in ``cwd``, raising on a non-zero exit (test setup helper)."""
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _git_out(cwd: Path, *args: str) -> str:
    """Run ``git <args>`` in ``cwd`` and return its stripped stdout (test assertion helper)."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _configure_identity(repo: Path) -> None:
    """Configure a local git identity (never the developer's global config) so commits are hermetic."""
    _git(repo, "config", "user.email", "test@decode.local")
    _git(repo, "config", "user.name", "decode test")
    _git(repo, "config", "commit.gpgsign", "false")


def _make_git_repo(path: Path, *, content: str = "hello\n") -> Path:
    """Create a local git repo at ``path`` with one committed ``README.md``; return ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _configure_identity(path)
    (path / "README.md").write_text(content, encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial commit")
    return path


def _clone_workspace(source: Path, home: Path) -> Path:
    """Clone ``source`` into ``<home>/.decode/sandbox`` (the Workspace) via the real ``prepare_workspace``."""
    return prepare_workspace(home, repo=str(source))


def _commit_change(repo: Path, *, filename: str, content: str) -> str:
    """Commit a new file in ``repo`` (models the agent committing in the Workspace); return the HEAD."""
    _configure_identity(repo)
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", "workspace change")
    return _git_out(repo, "rev-parse", "HEAD")


def _branch_exists(repo: Path, branch: str) -> bool:
    """True if ``branch`` (a local ref) exists in ``repo``."""
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


# ================================================================================================
# PART 2 — the skipif-guarded real-infra smokes. Each SKIPS (never fails) when its infra is absent,
# and reaps its container / sandbox / network in a ``finally`` so the suite leaves no infra litter.
# ================================================================================================


def _docker_available() -> bool:
    """True if a local docker daemon answers a fast ``docker info`` probe (else the docker smokes SKIP)."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5.0, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _modal_credentials_present() -> bool:
    """True if modal account credentials are present (presence only; else the modal smokes SKIP)."""
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return (Path.home() / ".modal.toml").exists()


_DOCKER_AVAILABLE = _docker_available()
_MODAL_AVAILABLE = _modal_credentials_present()

# A real remote-sandbox cold start (image pull + spawn + bootstrap upload) can take a while.
_MODAL_TIMEOUT_S = 120.0
# Modal's minimum sandbox lifetime is 10s; a run at the floor lets the revival smoke wait out a REAL
# max-lifetime expiry (poll() reports it ~1s past the deadline) instead of an external terminate.
_MODAL_MIN_LIFETIME_S = 10
_MODAL_EXPIRY_WAIT_S = 45.0


def _container_exists(name_or_id: str) -> bool:
    """True while the daemon still lists a container matching ``name_or_id`` by id OR by name.

    Two separate probes: docker ANDs distinct filter types, so a single ``--filter name=X --filter id=X``
    could never match a full id (an auto-name never contains the hex id) — probing each type restores OR.
    """
    for key in ("id", "name"):
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"{key}={name_or_id}"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        if result.stdout.strip():
            return True
    return False


def _network_exists(name: str) -> bool:
    """True while the daemon still lists a network named ``name`` (proves proxy-network teardown)."""
    result = subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", f"name={name}"],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    return bool(result.stdout.strip())


def _wait_until_gone(name_or_id: str, timeout_s: float = 5.0) -> bool:
    """Poll (bounded) until the container is no longer listed; return whether it is gone."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _container_exists(name_or_id):
            return True
        time.sleep(0.2)
    return not _container_exists(name_or_id)


@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="the docker daemon is not reachable")
async def test_real_docker_isolated_workspace_roundtrip_and_handback(tmp_path: Path) -> None:
    """The real Docker isolated Workspace, end to end — else SKIP (ADR-0012 §2-4, §8).

    One session container over a bind-mounted ``--repo`` clone: (1) ``bash`` AND the file backend see the
    cloned file — one truthful tree; (2) a ``bash``-written file is host-visible in ``.decode/sandbox``
    (the mount); (3) fresh-exec — ``cd`` / ``export`` do NOT persist; (4) a timeout kills only the command,
    the container + fs survive; (5) the host-side git **hand-back** secures + pushes ``decode/<id>`` to the
    local origin; (6) ``aclose`` removes the container. Reaps in a ``finally`` so the suite stays hermetic.
    """
    source = _make_git_repo(tmp_path / "source", content="cloned-into-workspace\n")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)  # host-side clone at <home>/.decode/sandbox
    executor = SandboxExecutor(DockerBackend())
    container_id: str | None = None
    try:
        await executor.start(workspace)  # bind-mount the cloned Workspace at /workspace
        container_id = executor._backend._container_id
        assert container_id is not None and _container_exists(container_id)

        # 1. One truthful tree: bash AND the file backend both see the cloned file.
        seen = await executor.run("cat README.md", cwd=workspace, timeout_s=30.0)
        assert seen.exit_code == 0 and "cloned-into-workspace" in seen.stdout
        assert (
            await executor._backend.read_bytes("README.md")
        ).decode() == "cloned-into-workspace\n"

        # 2. A bash-written file is host-visible on the mount (.decode/sandbox).
        await executor.run("echo agent-made > new.txt", cwd=workspace, timeout_s=30.0)
        assert (workspace / "new.txt").read_text(encoding="utf-8").strip() == "agent-made"

        # 3. Fresh-exec: the filesystem persists but cd/export do NOT carry over.
        await executor.run("export CAP=1 && cd /tmp", cwd=workspace, timeout_s=30.0)
        fresh = await executor.run("echo [$CAP]; pwd", cwd=workspace, timeout_s=30.0)
        assert "[]" in fresh.stdout and "/workspace" in fresh.stdout

        # 4. A timeout kills only the command; the container + fs survive (no reset note).
        timed = await executor.run("sleep 100", cwd=workspace, timeout_s=1.0)
        assert timed.timed_out is True and timed.note == ""
        after = await executor.run("cat new.txt", cwd=workspace, timeout_s=30.0)
        assert "agent-made" in after.stdout

        # 5. The git hand-back secures + pushes decode/<id> to the local origin, host-side.
        result = ship_workspace(home, repo=str(source), session_id="dckr0000-9999")
        assert result.pushed is True and result.branch == "decode/dckr0000"
        assert "new.txt" in _git_out(source, "ls-tree", "-r", "--name-only", "decode/dckr0000")

        # 6. aclose stops + removes the session container.
        await executor.aclose()
        assert _wait_until_gone(container_id), "aclose must stop and remove the session container"
    finally:
        await executor.aclose()  # idempotent safety net — no leaked container


@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="the docker daemon is not reachable")
async def test_real_docker_workspace_has_git(tmp_path: Path) -> None:
    """git is available AND identity-configured in the default docker Workspace — else SKIP.

    The slim base image ships no git (ADR-0012); ``DockerBackend.create`` installs it AND sets the
    ``SANDBOX_GIT_USER_*`` identity (default ``decode``) best-effort for an unwired worker, so a model
    ``git commit`` works. Proven end to end: ``git --version`` exits 0 and ``git config user.name`` is the
    configured identity. Reaps in ``finally``.
    """
    workspace = tmp_path / ".decode" / "sandbox"
    workspace.mkdir(parents=True)
    executor = SandboxExecutor(DockerBackend())
    try:
        await executor.start(
            workspace
        )  # create() installs + configures git before the first command
        result = await executor.run("git --version", cwd=workspace, timeout_s=60.0)
        assert result.exit_code == 0, result.stderr or result.stdout
        assert "git version" in result.stdout
        ident = await executor.run("git config --global user.name", cwd=workspace, timeout_s=30.0)
        assert ident.stdout.strip() == settings.sandbox_git_user_name  # the preconfigured identity
    finally:
        await executor.aclose()


def _host_workspace_with_marker(tmp_path: Path, *, content: str = "bootstrapped\n") -> Path:
    """A populated NON-git host Workspace (one marker file) — the modal bootstrap-upload source.

    Deliberately a plain tree (not a ``git clone``): this smoke exercises the bootstrap + direct file ops
    + export lifecycle, so a marker file is the minimal source. The export sweep OVER a real clone's
    read-only ``.git`` objects is proven separately by :func:`test_modal_export_over_a_clone_round_trips_git`.
    Mirrors ``test_modal_executor``'s fixture.
    """
    workspace = tmp_path / ".decode" / "sandbox"
    workspace.mkdir(parents=True)
    (workspace / "marker.txt").write_text(content, encoding="utf-8")
    return workspace


@pytest.mark.skipif(not _MODAL_AVAILABLE, reason="modal account credentials are not present")
async def test_real_modal_isolated_workspace_roundtrip(tmp_path: Path) -> None:
    """The real Modal isolated Workspace + lifecycle — else SKIP (ADR-0012 §2, §4-5).

    One session-persistent remote sandbox: (1) the host Workspace is **bootstrap-uploaded** into
    ``/workspace`` at create (a seeded file is readable by ``bash``); (2) **direct SandboxFilesystem file
    ops** — a ``bash``-written file reads back via ``read_bytes`` (no mirror), a ``write_bytes`` is visible
    to ``bash``, a ``remove`` is reflected by a later ``stat``; (3) a per-exec timeout kills only the
    command (the sandbox + fs survive); (4) a standalone ``export`` sweeps a remote-only file back to the
    host, leaving the sandbox alive; (5) ``aclose`` = export + terminate (no leaked remote sandbox). Reaps
    in a ``finally``. (The ``--repo`` clone bootstrap is proven by ``test_workspace_clone.py``; the export
    hand-back **over a clone** — read-only ``.git`` objects and all — by :func:`test_modal_export_over_a_clone_round_trips_git`.)
    """
    workspace = _host_workspace_with_marker(tmp_path)
    executor = SandboxExecutor(ModalBackend())
    try:
        await executor.start(workspace)  # spawn + bootstrap-upload the host Workspace
        backend = executor._backend

        # 1. The bootstrap upload landed: bash sees the host-seeded file remotely.
        seen = await executor.run(
            "cat /workspace/marker.txt", cwd=workspace, timeout_s=_MODAL_TIMEOUT_S
        )
        assert seen.stdout.strip() == "bootstrapped"

        # 2. Direct, truthful file ops — no host mirror.
        await executor.run("echo from-bash > b.txt", cwd=workspace, timeout_s=_MODAL_TIMEOUT_S)
        assert (await backend.read_bytes("b.txt")).decode().strip() == "from-bash"
        await backend.write_bytes("c.txt", b"via-backend\n")
        via = await executor.run("cat c.txt", cwd=workspace, timeout_s=_MODAL_TIMEOUT_S)
        assert via.stdout.strip() == "via-backend"
        await backend.remove("b.txt")
        assert await backend.stat("b.txt") is None  # a remove is reflected (no deletion-blindness)

        # 3. A per-exec timeout kills the command, not the sandbox.
        timed = await executor.run("sleep 100", cwd=workspace, timeout_s=1.0)
        assert timed.timed_out is True and timed.note == ""

        # 4. A standalone export sweeps a remote-only file down to the host, sandbox still alive.
        await executor.run(
            "echo shipped > only-remote.txt", cwd=workspace, timeout_s=_MODAL_TIMEOUT_S
        )
        await executor.export()
        assert (workspace / "only-remote.txt").read_text(encoding="utf-8").strip() == "shipped"
    finally:
        await executor.aclose()  # export + terminate the remote sandbox (no leak)

    assert executor._backend._sandbox is None  # aclose terminated + cleared it


@pytest.mark.skipif(not _MODAL_AVAILABLE, reason="modal account credentials are not present")
async def test_real_modal_workspace_has_git(tmp_path: Path) -> None:
    """git AND its identity are baked into the modal image so a model ``git commit`` works — else SKIP.

    The slim base ships no git; the modal image chains ``.apt_install("git")`` + ``.run_commands("git
    config …")`` (cached layers, no per-session cost). Proven end to end: ``git --version`` exits 0 and
    ``git config user.name`` is the configured identity. The first run may be slow while modal builds the
    layers (cached afterwards). Reaps in ``finally``.
    """
    workspace = _host_workspace_with_marker(tmp_path)
    executor = SandboxExecutor(ModalBackend())
    try:
        await executor.start(workspace)
        result = await executor.run("git --version", cwd=workspace, timeout_s=_MODAL_TIMEOUT_S)
        assert result.exit_code == 0, result.stderr or result.stdout
        assert "git version" in result.stdout
        ident = await executor.run(
            "git config --global user.name", cwd=workspace, timeout_s=_MODAL_TIMEOUT_S
        )
        assert ident.stdout.strip() == settings.sandbox_git_user_name  # the preconfigured identity
    finally:
        await executor.aclose()  # export + terminate the remote sandbox (no leak)


@pytest.mark.skipif(not _MODAL_AVAILABLE, reason="modal account credentials are not present")
async def test_real_modal_injects_the_git_token_into_the_sandbox(
    tmp_path: Path, monkeypatch
) -> None:
    """SANDBOX_GIT_TOKEN rides a modal.Secret into the sandbox env + a credential helper — else SKIP.

    The direct-injection path (ADR-0012 §10), proven end to end WITHOUT a real GitHub call: a DUMMY token
    shows up as ``$GITHUB_TOKEN`` inside the remote sandbox and git's ``credential.helper`` is wired to
    read it (so a real ``git push`` would authenticate). Docker never does this — it uses the Credential
    Proxy so its worker holds no token. Reaps in ``finally``.
    """
    monkeypatch.setattr(settings, "sandbox_git_token", SecretStr("dummy-token-not-real"))
    workspace = _host_workspace_with_marker(tmp_path)
    executor = SandboxExecutor(ModalBackend())
    try:
        await executor.start(workspace)
        env = await executor.run("printenv GITHUB_TOKEN", cwd=workspace, timeout_s=_MODAL_TIMEOUT_S)
        assert (
            env.stdout.strip() == "dummy-token-not-real"
        )  # the modal.Secret reached the sandbox env
        helper = await executor.run(
            "git config --global credential.helper", cwd=workspace, timeout_s=_MODAL_TIMEOUT_S
        )
        assert "GITHUB_TOKEN" in helper.stdout  # the helper reads the runtime token at push time
    finally:
        await executor.aclose()


def test_modal_export_over_a_clone_round_trips_git(tmp_path: Path) -> None:
    """The Modal export sweep OVER a ``--repo`` clone round-trips git — the hand-back's substrate (§5,8).

    ``ModalBackend.export`` sweeps ``/workspace`` down with a remote ``tar -c`` +
    :func:`~decode.sandbox.workspace.extract_tar` into the host ``.decode/sandbox`` — which for a ``--repo``
    session is a real ``git clone`` whose ``.git`` loose objects are read-only (0444). This exercises that
    exact host-side transport (``tar_dir`` → ``extract_tar`` over the clone) and asserts the agent's work
    **and** a valid ``.git`` survive the sweep. It pins the fixed product bug: ``extract_tar`` used to
    abort with :class:`PermissionError` overwriting the read-only objects (a modal ``--repo`` session then
    swept *nothing* and the hand-back captured no work); it now makes the destination tree writable first,
    so the export lands and the git hand-back reads a valid repo. Docker is unaffected (live bind mount).
    """
    from decode.sandbox.workspace import extract_tar, tar_dir

    source = _make_git_repo(tmp_path / "source")
    workspace = _clone_workspace(
        source, tmp_path / "home"
    )  # read-only .git objects, like production
    cloned_head = _git_out(workspace, "rev-parse", "HEAD")
    (workspace / "agent_work.txt").write_text("session results\n", encoding="utf-8")

    # The modal export transport: pack /workspace (tar_dir) and sweep it into the host clone (extract_tar).
    extract_tar(tar_dir(workspace), workspace)

    # The agent's uncommitted work survived the sweep ...
    assert (workspace / "agent_work.txt").read_text(encoding="utf-8") == "session results\n"
    # ... and the swept-back .git is a VALID repo the hand-back reads: HEAD + the loose objects (log) are
    # intact and the untracked agent file shows in a working ``git status`` — i.e. the sweep captured work.
    assert _git_out(workspace, "rev-parse", "HEAD") == cloned_head
    assert _git_out(workspace, "log", "-1", "--format=%s") == "initial commit"
    assert "agent_work.txt" in _git_out(workspace, "status", "--porcelain")


@pytest.mark.skipif(not _MODAL_AVAILABLE, reason="modal account credentials are not present")
async def test_real_modal_revival_re_bootstraps_from_host_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A max-lifetime-expired remote sandbox is recreated + re-bootstrapped from host state — else SKIP (§3).

    Runs a sandbox at Modal's 10s lifetime floor, waits out a REAL max-lifetime expiry, then runs a
    command: the backend recreates a fresh sandbox, re-bootstraps ``/workspace`` from the host state, and
    surfaces the one-shot restore ``note``. The exhaustive revival matrix lives in ``test_modal_executor.py``;
    this is the integrated capstone proof. Reaps the remote sandbox in a ``finally`` (no leak).
    """
    monkeypatch.setattr(bash_mod.settings, "sandbox_timeout_s", _MODAL_MIN_LIFETIME_S)
    workspace = _host_workspace_with_marker(tmp_path, content="revived-content\n")
    executor = SandboxExecutor(ModalBackend())
    try:
        await executor.start(workspace)
        backend = executor._backend
        first_id = backend._sandbox.object_id

        # Wait out the real max-lifetime expiry (poll() reports it ~1s past the deadline).
        deadline = time.monotonic() + _MODAL_EXPIRY_WAIT_S
        while time.monotonic() < deadline:
            if await backend._sandbox.poll.aio() is not None:
                break
            await anyio.sleep(1.0)

        revived = await executor.run(
            "cat /workspace/marker.txt", cwd=workspace, timeout_s=_MODAL_TIMEOUT_S
        )

        assert backend._sandbox.object_id != first_id  # a fresh sandbox replaced the expired one
        assert revived.stdout.strip() == "revived-content"  # re-bootstrapped from the host state
        assert "expired" in revived.note.lower()  # the one-shot restore note
    finally:
        await executor.aclose()


# --- The real docker Credential-Proxy boundary (the retained ADR-0011 §6 topology) --------------

_PROXY_SECRET = "capstone-proxy-secret-4b2a91"
_PROXY_HEADER = "X-Decode-Proxy-Auth"
_PROXY_UPSTREAM_ALIAS = "upstream.local"

# A stub upstream that echoes every request header back in the body, so the worker can prove which
# headers actually ARRIVED. Stdlib only (runs in the uv slim image); binds port 80.
_UPSTREAM_SERVER = (
    "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
    "class H(BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        body=''.join(f'{k}: {v}\\n' for k,v in self.headers.items()).encode()\n"
    "        self.send_response(200); self.send_header('Content-Length', str(len(body)))\n"
    "        self.end_headers(); self.wfile.write(body)\n"
    "    def log_message(self,*a): pass\n"
    "HTTPServer(('0.0.0.0',80),H).serve_forever()\n"
)
# The worker's outbound probe — python/urllib (slim has no curl); reads its ``http_proxy`` env.
_REQUEST_SCRIPT = (
    "import urllib.request\n"
    f"print(urllib.request.urlopen('http://{_PROXY_UPSTREAM_ALIAS}/', timeout=10).read().decode())\n"
)


def _wait_tcp_ready(container: str, port: int, timeout_s: float = 15.0) -> None:
    """Poll (bounded) until a server inside ``container`` accepts a TCP connection on ``port``."""
    probe = f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=1).close()"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "python3", "-c", probe],
            capture_output=True,
            timeout=10.0,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.2)
    raise AssertionError(f"{container} port {port} never became ready")


def _start_upstream(network: str) -> str:
    """Start the header-echoing stub upstream on ``network`` (alias ``upstream.local``); return its name."""
    name = f"decode-cap-upstream-{uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            _PROXY_UPSTREAM_ALIAS,
            "ghcr.io/astral-sh/uv:python3.12-bookworm-slim",
            "python3",
            "-c",
            _UPSTREAM_SERVER,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    _wait_tcp_ready(name, 80)
    return name


def _stop_container(name: str) -> None:
    subprocess.run(["docker", "stop", "--time", "2", name], capture_output=True, timeout=30.0)


@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="the docker daemon is not reachable")
async def test_real_docker_credential_proxy_boundary(monkeypatch, tmp_path: Path) -> None:
    """The credential boundary, proven end to end against a real daemon — else SKIP (ADR-0011 §6, retained).

    A rule injects ``X-Decode-Proxy-Auth: <secret>`` on requests to the stub upstream, resolved host-side
    from a **patched** Kitaru secret. A token-free proxy-wired ``SandboxExecutor(DockerBackend(...))``
    worker makes a urllib request through the ``mitmproxy`` addon container; the upstream echoes the
    headers it received — proving the header **ARRIVED** — while a scan of the worker container's own env
    proves the secret is **absent** there (it lives only in the proxy container). Torn down in a ``finally``.
    """
    monkeypatch.setattr(
        "kitaru.get_secret", lambda name: SimpleNamespace(values={"token": _PROXY_SECRET})
    )
    credential_map = build_credential_map(
        [
            SandboxProxyRule(
                name="upstream-auth",
                hosts=[_PROXY_UPSTREAM_ALIAS],
                headers={_PROXY_HEADER: "{{ test-secret.token }}"},
            )
        ]
    )
    proxy = DockerCredentialProxy(credential_map)
    executor: SandboxExecutor | None = None
    upstream: str | None = None
    worker_id: str | None = None
    try:
        proxy.start()
        upstream = _start_upstream(proxy.network)
        # The worker's /workspace is the project's .decode/sandbox Workspace (ADR-0012 §3) — drop the
        # probe script where the container will actually see it.
        scratch = tmp_path / ".decode" / "sandbox"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "req.py").write_text(_REQUEST_SCRIPT, encoding="utf-8")
        executor = SandboxExecutor(
            DockerBackend(
                network=proxy.network,
                proxy_env=proxy.worker_proxy_env,
                ca_cert_host_path=proxy.ca_cert_host_path,
            )
        )

        result = await executor.run("python3 /workspace/req.py", cwd=tmp_path, timeout_s=30.0)
        worker_id = executor._backend._container_id

        # The upstream echoed the injected header — it ARRIVED, though the worker never held it.
        assert result.exit_code == 0, result.stdout
        assert f"{_PROXY_HEADER}: {_PROXY_SECRET}" in result.stdout

        # SECURITY: the worker container's own env carries the proxy URL but NOT the secret value.
        env = subprocess.run(
            ["docker", "exec", worker_id, "env"], capture_output=True, text=True, timeout=15.0
        ).stdout
        assert _PROXY_SECRET not in env  # the worker holds no token ...
        assert "http_proxy=" in env  # ... it is merely routed through the proxy
    finally:
        if executor is not None:
            await executor.aclose()
        if upstream is not None:
            _stop_container(upstream)
        proxy.stop()

    # Everything is torn down — no docker litter left behind.
    assert not _container_exists(proxy._container_name)
    assert worker_id is None or not _container_exists(worker_id)
    assert upstream is None or not _container_exists(upstream)
    assert not _network_exists(proxy.network)
