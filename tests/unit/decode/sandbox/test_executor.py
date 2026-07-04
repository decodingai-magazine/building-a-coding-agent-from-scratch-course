"""Unit tests for the unified sandbox executor (``decode.sandbox.executor``, ADR-0012 §2).

These pin the backend-agnostic :class:`SandboxExecutor` contract with a **fake backend** (no docker,
no modal): fresh-exec ``run`` = ensure-created → ``backend.exec("bash","-lc", command)``; one create
per session (memoized); ``start(workspace)`` sets the canonical Workspace + seeds skills; the lazy
``run(cwd=…)`` fallback derives ``workspace_dir(cwd)``; ``aclose`` = ``export()`` then ``destroy()``;
and a backend create failure renders a crash-free exit-125 :class:`~decode.tools.exec.ExecResult`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decode.config.settings import settings
from decode.sandbox.executor import SandboxExecutor
from decode.tools.exec import ExecResult


class _FakeBackend:
    """A recording :class:`~decode.sandbox.executor.SandboxBackend` — no infra, just call capture."""

    def __init__(self, *, create_error: Exception | None = None) -> None:
        self.created: list[Path] = []
        self.exec_calls: list[tuple[tuple[str, ...], float]] = []
        self.export_count = 0
        self.destroy_count = 0
        self.events: list[str] = []  # ordered lifecycle log (proves export-before-destroy)
        self._create_error = create_error
        self.result = ExecResult(stdout="ok", stderr="", exit_code=0, timed_out=False)

    async def create(self, workspace: Path) -> None:
        self.events.append("create")
        if self._create_error is not None:
            raise self._create_error
        self.created.append(workspace)

    async def exec(self, *args: str, timeout_s: float) -> ExecResult:
        self.exec_calls.append((args, timeout_s))
        return self.result

    async def export(self) -> None:
        self.events.append("export")
        self.export_count += 1

    async def destroy(self) -> None:
        self.events.append("destroy")
        self.destroy_count += 1


# --- run: fresh-exec = ensure-created → backend.exec ------------------------------------------


async def test_run_ensures_created_then_execs_bash_lc(tmp_path):
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)

    result = await executor.run("echo hi", cwd=tmp_path, timeout_s=12.5)

    # One create, then a single fresh ``bash -lc`` exec — the fresh-exec shape.
    assert len(backend.created) == 1
    assert backend.exec_calls == [(("bash", "-lc", "echo hi"), 12.5)]
    assert result is backend.result


async def test_run_creates_the_backend_only_once(tmp_path):
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)

    await executor.run("a", cwd=tmp_path, timeout_s=5.0)
    await executor.run("b", cwd=tmp_path, timeout_s=5.0)

    assert len(backend.created) == 1  # memoized: one container/sandbox per session
    assert len(backend.exec_calls) == 2  # ...but a fresh exec per command


async def test_run_ignores_cwd_for_the_workdir_and_derives_the_workspace(tmp_path):
    # A sandbox executor runs in /workspace; ``cwd`` is only used to derive the Workspace when nothing
    # was started — ``workspace_dir(cwd)`` = ``<cwd>/.decode/sandbox``, NOT ``cwd`` itself.
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)

    await executor.run("echo hi", cwd=tmp_path, timeout_s=5.0)

    expected = (tmp_path / settings.sandbox_workspace_dir).resolve()
    assert backend.created == [expected]


# --- start: the canonical Workspace + skills seeding ------------------------------------------


async def test_start_sets_the_workspace_verbatim_and_seeds_skills(mocker, tmp_path):
    # ``start`` receives the already-resolved Workspace (the call site passes ``workspace_dir(cwd)``):
    # it is stored verbatim — never re-derived into a nested ``.decode/sandbox`` under it — and skills
    # are seeded into it before create (so a bootstrap-uploading backend carries them, ADR-0012 §5).
    seed = mocker.patch("decode.sandbox.executor.seed_skills")
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)
    workspace = tmp_path / ".decode" / "sandbox"

    await executor.start(workspace)

    assert executor._workspace == workspace
    assert backend.created == [workspace]  # verbatim — no double-nesting
    seed.assert_called_once_with(workspace)


async def test_start_then_run_reuses_the_started_workspace(mocker, tmp_path):
    mocker.patch("decode.sandbox.executor.seed_skills")
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)
    workspace = tmp_path / "ws"

    await executor.start(workspace)
    await executor.run("echo hi", cwd=Path("/some/other/cwd"), timeout_s=5.0)

    assert len(backend.created) == 1  # start already created it; run reuses (cwd ignored)
    assert backend.created == [workspace]


async def test_start_is_idempotent(mocker, tmp_path):
    mocker.patch("decode.sandbox.executor.seed_skills")
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)
    workspace = tmp_path / "ws"

    await executor.start(workspace)
    await executor.start(workspace)  # a second start creates nothing new

    assert len(backend.created) == 1


# --- file_backend: the file/search tools' byte-transport seam (ADR-0012 §4) -------------------


async def test_file_backend_ensures_created_and_returns_the_backend(tmp_path):
    # The file-tool seam: ``file_backend`` ensures the sandbox exists (so a file op works before the
    # first ``bash``) and hands back the ONE backend the tools route their byte transport through.
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)

    result = await executor.file_backend(tmp_path)

    assert result is backend
    assert len(backend.created) == 1  # ensured created


async def test_file_backend_reuses_the_backend_bash_already_created(tmp_path):
    # File tools + ``bash`` share the ONE backend/container per session: after a ``run`` created it,
    # ``file_backend`` returns the same backend without re-creating (the shared memo).
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)
    await executor.run("echo hi", cwd=tmp_path, timeout_s=5.0)

    result = await executor.file_backend(tmp_path)

    assert result is backend
    assert len(backend.created) == 1  # not re-created — shares the create memo with run()


# --- aclose / export: session teardown --------------------------------------------------------


async def test_aclose_exports_then_destroys(tmp_path):
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)
    await executor.run("echo hi", cwd=tmp_path, timeout_s=5.0)

    await executor.aclose()

    assert backend.events[-2:] == ["export", "destroy"]  # sweep before teardown
    assert backend.export_count == 1
    assert backend.destroy_count == 1


async def test_aclose_is_idempotent_and_safe_when_never_started():
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)

    await executor.aclose()
    await executor.aclose()  # double aclose must not raise

    # export/destroy are delegated unconditionally (the backends no-op when nothing was created).
    assert backend.destroy_count == 2


async def test_aclose_destroys_even_when_export_raises(tmp_path):
    class _ExportBoom(_FakeBackend):
        async def export(self) -> None:
            self.events.append("export")
            raise RuntimeError("sweep failed")

    backend = _ExportBoom()
    executor = SandboxExecutor(backend)
    await executor.run("echo hi", cwd=tmp_path, timeout_s=5.0)

    with pytest.raises(RuntimeError, match="sweep failed"):
        await executor.aclose()

    assert backend.destroy_count == 1  # destroy still ran despite the export failure


async def test_export_passthrough_reaches_the_backend():
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)

    await executor.export()

    assert backend.export_count == 1


# --- create failure → a rendered, crash-free infra failure ------------------------------------


async def test_run_renders_a_create_failure_without_raising(tmp_path):
    # The never-crash contract: a backend create failure (daemon down) is caught and rendered — exit
    # 125 + a session-lost note + the underlying cause on stderr — never an exception into the tool.
    error = RuntimeError(
        "docker run failed (exit 1): Cannot connect to the Docker daemon at "
        "unix:///var/run/docker.sock. Is the docker daemon running?"
    )
    backend = _FakeBackend(create_error=error)
    executor = SandboxExecutor(backend)

    result = await executor.run("echo hi", cwd=tmp_path, timeout_s=5.0)

    assert result.exit_code == 125  # docker's container-failed convention, reused generically
    assert result.timed_out is False
    assert "Cannot connect to the Docker daemon" in result.stderr  # the cause is surfaced
    assert result.note  # a session-lost note is set
    assert backend.exec_calls == []  # no exec when create failed
    assert executor._created is False  # left un-created so a later run retries


async def test_run_retries_create_after_a_failure(tmp_path):
    backend = _FakeBackend(create_error=RuntimeError("daemon down"))
    executor = SandboxExecutor(backend)

    await executor.run("a", cwd=tmp_path, timeout_s=5.0)
    await executor.run("b", cwd=tmp_path, timeout_s=5.0)

    # Each run re-attempts create (both failed) — never a stuck "created" flag after a failure.
    assert backend.events.count("create") == 2


async def test_run_lets_an_unexpected_create_error_surface(tmp_path):
    # The infra catch is scoped to RuntimeError/OSError — a genuine bug (ValueError) must still crash,
    # not be masked as a fake ExecResult that hides the defect.
    backend = _FakeBackend(create_error=ValueError("a real bug"))
    executor = SandboxExecutor(backend)

    with pytest.raises(ValueError, match="a real bug"):
        await executor.run("echo hi", cwd=tmp_path, timeout_s=5.0)


def test_construction_is_inert():
    backend = _FakeBackend()
    executor = SandboxExecutor(backend)

    assert executor._workspace is None
    assert executor._created is False
    assert backend.created == []  # nothing runs until the first run()/start()
