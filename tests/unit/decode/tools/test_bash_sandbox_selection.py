"""Unit tests for the ``bash`` executor-selection seam + mode-specific description (ADR-0011 §4).

Covers lazy ``_get_executor()`` selection + memoization, ``reset``/``close``/``warm``/``export``
lifecycle hooks, the ``active_backend`` file-tool seam, ``bash_description()`` + registry
``prepare`` composition (none byte-identical; docker == modal), end-to-end routing through the
selected executor, and import-laziness (no sandbox/kitaru/modal modules leak). Hermetic: the
sandbox executors are faked at the ``select_executor`` seam — no Docker daemon, no Modal creds.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from pydantic import SecretStr
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import ToolDefinition

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agents.loader import load_agent
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.tools import bash as bash_mod
from decode.tools.askuser import deny_user_question_resolver
from decode.tools.exec import ExecResult, LocalExecutor
from decode.tools.registry import _prepare_for


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(cwd: Path) -> RunContext[AgentDeps]:
    """A pre-approved RunContext so ``bash`` runs the command (mirrors ``test_bash.py``)."""
    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=True)  # type: ignore[arg-type]


class _AgentCtx:
    """A minimal ctx exposing ``deps.active_agent`` for the registry ``prepare`` callback."""

    def __init__(self, agent_name: str) -> None:
        self.deps = SimpleNamespace(active_agent=load_agent(agent_name))


# _get_executor: lazy selection + memo


def test_get_executor_none_keeps_the_eager_local_executor(monkeypatch):
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "none")
    bash_mod.reset_executor()
    eager = bash_mod._EXECUTOR  # the fresh LocalExecutor the reset installed

    selected = bash_mod._get_executor()

    # none mode returns the SAME eager instance (no re-select) — this is what preserves a test's
    # ``_EXECUTOR.run`` patch across the getter and keeps the none path sandbox-import-free.
    assert selected is eager
    assert isinstance(selected, LocalExecutor)


def test_active_executor_is_the_public_read_accessor(monkeypatch):
    # ``active_executor`` is the documented public seam accessor (out-of-tree callers must not reach
    # for ``_get_executor``): it returns exactly what the private getter memoizes.
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "none")
    bash_mod.reset_executor()

    public = bash_mod.active_executor()

    assert public is bash_mod._get_executor()
    assert isinstance(public, LocalExecutor)


def test_active_executor_docker_returns_the_warmed_memo(monkeypatch):
    # In a sandbox mode it shares the same memo — the executor a prior warm/select produced, so an
    # out-of-tree caller (the eval harness) reads the already-warmed executor.
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: SimpleNamespace(mode=mode))
    bash_mod.reset_executor()

    selected = bash_mod._get_executor()

    assert bash_mod.active_executor() is selected


def test_get_executor_docker_selects_via_the_seam_and_memoizes(monkeypatch):
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    calls = {"n": 0}

    def fake_select(mode: str):
        calls["n"] += 1
        return SimpleNamespace(mode=mode)

    monkeypatch.setattr("decode.sandbox.select_executor", fake_select)
    bash_mod.reset_executor()

    first = bash_mod._get_executor()
    second = bash_mod._get_executor()

    assert first.mode == "docker"
    assert first is second  # memoized: the sandbox executor is built once, not per bash call
    assert calls["n"] == 1  # select_executor ran exactly once


def test_get_executor_docker_returns_a_real_sandbox_executor(monkeypatch):
    # Through the REAL select_executor (not faked): docker mode yields a SandboxExecutor (over a
    # DockerBackend). Construction is inert, so no daemon is needed here (ADR-0012 §2).
    from decode.sandbox.executor import SandboxExecutor

    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    bash_mod.reset_executor()

    assert isinstance(bash_mod._get_executor(), SandboxExecutor)


def test_get_executor_modal_returns_a_real_sandbox_executor(monkeypatch):
    # Through the REAL select_executor: modal mode yields a SandboxExecutor over a ModalBackend (inert —
    # no creds needed; the remote sandbox + modal import are lazy on first run, ADR-0012 §2).
    from decode.sandbox.executor import SandboxExecutor
    from decode.sandbox.modal_backend import ModalBackend

    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "modal")
    bash_mod.reset_executor()

    executor = bash_mod._get_executor()
    assert isinstance(executor, SandboxExecutor)
    assert isinstance(executor._backend, ModalBackend)


def test_reset_executor_rearms_selection(monkeypatch):
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: SimpleNamespace(mode=mode))
    bash_mod.reset_executor()
    assert bash_mod._get_executor().mode == "docker"

    bash_mod.reset_executor()

    # After reset the memo is the none default again and selection re-runs on the next call.
    assert isinstance(bash_mod._EXECUTOR, LocalExecutor)
    assert bash_mod._executor_selected is False
    assert bash_mod._get_executor().mode == "docker"


# close_executor: teardown + reset


async def test_close_executor_awaits_aclose_once_and_resets(monkeypatch):
    aclose = AsyncMock()
    monkeypatch.setattr(bash_mod, "_EXECUTOR", SimpleNamespace(aclose=aclose))
    monkeypatch.setattr(bash_mod, "_executor_selected", True)

    await bash_mod.close_executor()

    aclose.assert_awaited_once()
    assert isinstance(bash_mod._EXECUTOR, LocalExecutor)  # memo reset to the none default
    assert bash_mod._executor_selected is False


async def test_close_executor_is_idempotent(monkeypatch):
    aclose = AsyncMock()
    monkeypatch.setattr(bash_mod, "_EXECUTOR", SimpleNamespace(aclose=aclose))
    monkeypatch.setattr(bash_mod, "_executor_selected", True)

    await bash_mod.close_executor()
    await bash_mod.close_executor()  # second call finds the reset LocalExecutor — no aclose

    aclose.assert_awaited_once()


async def test_close_executor_calls_sync_close_when_no_aclose(monkeypatch):
    close = Mock()
    monkeypatch.setattr(bash_mod, "_EXECUTOR", SimpleNamespace(close=close))
    monkeypatch.setattr(bash_mod, "_executor_selected", True)

    await bash_mod.close_executor()

    close.assert_called_once()


async def test_close_executor_is_a_safe_noop_in_none_mode():
    # The LocalExecutor default has neither aclose nor close: close_executor must not raise, and the
    # "nothing was built" case (a fresh reset memo) is covered by the same path.
    bash_mod.reset_executor()

    await bash_mod.close_executor()

    assert isinstance(bash_mod._EXECUTOR, LocalExecutor)
    assert bash_mod._executor_selected is False


# warm_executor: the eager REPL warm-up


async def test_warm_executor_none_is_a_noop_that_never_selects(monkeypatch):
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "none")
    bash_mod.reset_executor()
    eager = bash_mod._EXECUTOR

    await bash_mod.warm_executor(Path("/repo"))

    # none returns BEFORE touching the memo: no selection ran, no ``[sandbox]`` log line, and the
    # eager LocalExecutor is untouched — the plain REPL stays byte-identical (and a test's
    # ``_EXECUTOR.run`` patch would survive the warm-up exactly as it survives the getter).
    assert bash_mod._executor_selected is False
    assert bash_mod._EXECUTOR is eager


async def test_warm_executor_docker_selects_and_awaits_start(monkeypatch, tmp_path):
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    start = AsyncMock()
    executor = SimpleNamespace(start=start)
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: executor)
    bash_mod.reset_executor()

    await bash_mod.warm_executor(tmp_path)

    start.assert_awaited_once_with(tmp_path)
    # Warm shares the ``_EXECUTOR`` memo: the warmed instance IS the one ``bash`` will use.
    assert bash_mod._get_executor() is executor


async def test_warm_executor_startless_executor_is_a_noop(monkeypatch, tmp_path):
    # An executor without ``start`` (the Protocol minimum) warms as a no-op — duck-typed like
    # ``close_executor``'s ``aclose``/``close`` probe, so ``CommandExecutor`` stays run-only.
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    executor = SimpleNamespace()
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: executor)
    bash_mod.reset_executor()

    await bash_mod.warm_executor(tmp_path)  # must not raise

    assert bash_mod._get_executor() is executor


async def test_warm_executor_failure_propagates_and_keeps_the_memo(monkeypatch, tmp_path):
    # A failed start propagates (the app call site renders one friendly line) WITHOUT resetting the
    # memo: the next ``bash`` retries through the same executor from scratch (it cached nothing).
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    executor = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("image pull failed")))
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: executor)
    bash_mod.reset_executor()

    with pytest.raises(RuntimeError, match="image pull failed"):
        await bash_mod.warm_executor(tmp_path)

    assert bash_mod._get_executor() is executor  # memo kept — the first bash retries through it


# export_executor: the mid-session /ship sweep hook


async def test_export_executor_awaits_export_without_resetting_the_memo(monkeypatch):
    # /ship sweeps the live Workspace to the host mid-session: export is awaited, but — unlike
    # close_executor — the memo is NOT reset and the sandbox is NOT destroyed (the session continues).
    export = AsyncMock()
    executor = SimpleNamespace(export=export)
    monkeypatch.setattr(bash_mod, "_EXECUTOR", executor)
    monkeypatch.setattr(bash_mod, "_executor_selected", True)

    await bash_mod.export_executor()

    export.assert_awaited_once()
    assert bash_mod._EXECUTOR is executor  # still live — no reset, no destroy
    assert bash_mod._executor_selected is True


async def test_export_executor_is_a_safe_noop_in_none_mode():
    # The LocalExecutor default has no ``export`` (duck-typed like warm/close): export_executor must
    # not raise, and the "nothing was selected" case (a fresh reset memo) is the same path.
    bash_mod.reset_executor()

    await bash_mod.export_executor()

    assert isinstance(bash_mod._EXECUTOR, LocalExecutor)  # untouched


# active_backend: the file-tool seam


def test_active_backend_none_mode_returns_none_without_touching_the_memo(monkeypatch):
    # none mode: the file tools take the direct-pathlib path. ``active_backend`` returns None BEFORE any
    # selection — no ``[sandbox]`` log, no sandbox import, no memo change (the none path stays clean).
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "none")
    bash_mod.reset_executor()

    assert bash_mod.active_backend(Path("/repo")) is None
    assert bash_mod._executor_selected is False  # never selected


def test_active_backend_returns_none_for_a_backendless_executor(monkeypatch):
    # A selected executor without ``file_backend`` (the ``none`` LocalExecutor / a fake) yields None —
    # duck-typed like warm/close, so the seam stays optional and never bridges for a run-only executor.
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: SimpleNamespace())
    bash_mod.reset_executor()

    assert bash_mod.active_backend(Path("/x")) is None


async def test_active_backend_docker_returns_the_executors_created_backend(monkeypatch, tmp_path):
    # docker/modal: ``active_backend`` shares ``bash``'s ``_EXECUTOR`` memo and returns the executor's
    # created backend via ``file_backend(cwd)`` — the SAME backend/container ``bash`` runs through, so a
    # file-tool write is visible to ``bash`` and vice-versa. Bridged (from_thread) → driven in a thread.
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    sentinel_backend = object()

    class _FakeExecutor:
        async def file_backend(self, cwd: Path) -> object:
            self.cwd = cwd
            return sentinel_backend

    executor = _FakeExecutor()
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: executor)
    bash_mod.reset_executor()

    backend = await anyio.to_thread.run_sync(lambda: bash_mod.active_backend(tmp_path))

    assert backend is sentinel_backend
    assert executor.cwd == tmp_path  # ensured-created against the passed Workspace cwd
    assert bash_mod._get_executor() is executor  # shares the bash memo (one backend per session)


# bash_description


def test_bash_description_none_is_identity(monkeypatch):
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "none")
    assert bash_mod.bash_description("BASE") == "BASE"


def test_bash_description_docker_appends_the_unified_sandbox_paragraph(monkeypatch):
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")

    out = bash_mod.bash_description("BASE")

    assert out == f"BASE\n\n{bash_mod._SANDBOX_DESCRIPTION_SUFFIX}"
    assert "/workspace" in out
    assert "isolated Workspace" in out  # /workspace IS the isolated Workspace (ADR-0012 §2)
    assert "git clone of your repo" in out  # ...a clone of --repo, or an empty scratch
    assert (
        "do NOT carry over" in out
    )  # fresh-exec: cd/export do not persist across calls (ADR-0012)
    assert "separate streams" in out  # stdout/stderr kept split


def test_bash_description_modal_appends_the_same_unified_paragraph(monkeypatch):
    """ADR-0012 §2: modal gets the SAME unified paragraph as docker (one fresh-exec shape)."""
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "modal")
    modal_out = bash_mod.bash_description("BASE")

    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    docker_out = bash_mod.bash_description("BASE")

    # The whole point of the task: docker == modal == base + the ONE unified paragraph.
    assert modal_out == docker_out == f"BASE\n\n{bash_mod._SANDBOX_DESCRIPTION_SUFFIX}"


# registry prepare


async def test_bash_prepare_none_returns_the_definition_untouched(monkeypatch):
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "none")
    prepare = _prepare_for("bash")
    td = ToolDefinition(name="bash", parameters_json_schema={"type": "object"}, description="BASE")

    result = await prepare(_AgentCtx("build"), td)  # type: ignore[arg-type]

    assert result is td  # byte-identical: the exact same definition object, unmodified


async def test_bash_prepare_docker_appends_the_paragraph(monkeypatch):
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    prepare = _prepare_for("bash")
    td = ToolDefinition(name="bash", parameters_json_schema={"type": "object"}, description="BASE")

    result = await prepare(_AgentCtx("build"), td)  # type: ignore[arg-type]

    assert result is not td  # a replaced copy, not the passed object
    assert result.description == f"BASE\n\n{bash_mod._SANDBOX_DESCRIPTION_SUFFIX}"


async def test_bash_prepare_still_hides_bash_when_the_agent_omits_it(monkeypatch):
    # The description composition never overrides the active-agent restriction: plan omits bash.
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    prepare = _prepare_for("bash")
    td = ToolDefinition(name="bash", parameters_json_schema={"type": "object"}, description="BASE")

    assert await prepare(_AgentCtx("plan"), td) is None  # type: ignore[arg-type]


async def test_prepare_for_non_bash_ignores_sandbox_mode(monkeypatch):
    # Only bash gets the mode-specific description; a non-bash tool is the plain restriction.
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    prepare = _prepare_for("read")
    td = ToolDefinition(
        name="read", parameters_json_schema={"type": "object"}, description="READ BASE"
    )

    result = await prepare(_AgentCtx("build"), td)  # type: ignore[arg-type]

    assert result is td  # unchanged — no sandbox paragraph on a non-bash tool


# through a real agent: the description the MODEL sees, per mode


async def _bash_description_via_agent(mode: str, monkeypatch, cwd: Path) -> str:
    """Build a real agent in ``mode`` and return the ``bash`` description the model is handed."""
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", mode)
    monkeypatch.setattr(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), raising=False
    )
    agent = build_agent()
    captured: dict[str, str | None] = {}

    def model_fn(messages: list[ModelResponse], info: AgentInfo) -> ModelResponse:
        captured["bash"] = next(
            (t.description for t in info.function_tools if t.name == "bash"), None
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


async def test_agent_bash_description_docker_is_none_plus_the_paragraph(monkeypatch, tmp_path):
    """The model-facing bash description: docker == none + the unified paragraph (proves none is base).

    Capturing the description the model actually receives proves the end-to-end wiring (registry
    ``prepare`` → the model schema). ``docker == none + suffix`` transitively proves the ``none``-mode
    description is byte-identical to the untouched base (no sandbox paragraph leaks into ``none``).
    """
    none_desc = await _bash_description_via_agent("none", monkeypatch, tmp_path)
    docker_desc = await _bash_description_via_agent("docker", monkeypatch, tmp_path)

    assert "/workspace" not in none_desc  # none carries no sandbox paragraph
    assert "isolated Workspace" not in none_desc
    assert docker_desc == f"{none_desc}\n\n{bash_mod._SANDBOX_DESCRIPTION_SUFFIX}"


async def test_agent_bash_description_modal_equals_docker(monkeypatch, tmp_path):
    """ADR-0012 §2: the description the model sees is IDENTICAL for docker and modal (one paragraph)."""
    none_desc = await _bash_description_via_agent("none", monkeypatch, tmp_path)
    docker_desc = await _bash_description_via_agent("docker", monkeypatch, tmp_path)
    modal_desc = await _bash_description_via_agent("modal", monkeypatch, tmp_path)

    assert modal_desc == docker_desc == f"{none_desc}\n\n{bash_mod._SANDBOX_DESCRIPTION_SUFFIX}"


# end to end: a bash call routes through the SELECTED executor


async def test_bash_routes_through_the_selected_docker_executor(monkeypatch, tmp_path):
    """docker mode: ``bash`` runs through the executor ``select_executor`` returns, and renders it.

    A fake stands in for the docker ``SandboxExecutor`` at the ``select_executor`` seam (no real daemon). The
    fake records the ``run`` call and returns a canned :class:`ExecResult`; the tool must route the
    command through it and render that result — proving the executor swap end to end.
    """
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    calls: list[tuple[str, Path, float]] = []

    class _FakeExecutor:
        async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
            calls.append((command, cwd, timeout_s))
            return ExecResult(stdout="sandboxed-out", stderr="", exit_code=0, timed_out=False)

    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: _FakeExecutor())
    bash_mod.reset_executor()

    out = await bash_mod.bash(_ctx(tmp_path), command="echo hi")

    assert len(calls) == 1
    assert calls[0][0] == "echo hi"
    assert calls[0][1] == tmp_path  # bash still passes ctx.deps.cwd through the seam
    assert "sandboxed-out" in out and "Exit code: 0" in out  # the ExecResult was rendered


async def test_bash_renders_a_daemon_loss_without_raising(monkeypatch, tmp_path):
    """docker mode, daemon down: ``bash`` returns a rendered failure, never lets it escape.

    The tool-boundary never-crash contract: when the docker backend cannot be created (daemon down),
    :meth:`SandboxExecutor.run` catches the infra failure and returns a rendered :class:`ExecResult`
    (exit 125 + a session-lost note + the cause on stderr). This proves the whole boundary stays intact —
    ``bash`` renders that result to text (so the model reacts) rather than a ``RuntimeError`` crashing the
    turn. A real ``SandboxExecutor(DockerBackend())`` is used with only the backend's ``create`` patched
    to raise (as a dead daemon makes ``docker run`` fail), so no daemon is needed.
    """
    from decode.sandbox.docker_backend import DockerBackend
    from decode.sandbox.executor import SandboxExecutor

    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    backend = DockerBackend()
    monkeypatch.setattr(
        backend,
        "create",
        AsyncMock(
            side_effect=RuntimeError(
                "docker run failed (exit 1): Cannot connect to the Docker daemon at "
                "unix:///var/run/docker.sock. Is the docker daemon running?"
            )
        ),
    )
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: SandboxExecutor(backend))
    bash_mod.reset_executor()

    out = await bash_mod.bash(_ctx(tmp_path), command="echo hi")  # must NOT raise

    assert "Exit code: 125" in out  # docker's container-failed convention, rendered for the model
    assert "unreachable" in out  # the session-lost note reaches the model
    assert "Cannot connect to the Docker daemon" in out  # the underlying failure text is surfaced


# laziness / isolation: subprocess assertions on a clean interpreter


def _run_isolated(code: str, *, mode: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter with a pinned ``SANDBOX_MODE`` (clean sys.modules)."""
    env = {
        **os.environ,
        "SANDBOX_MODE": mode,
        "GEMINI_API_KEY": "test-key",
        "LLM_PROVIDER": "gemini",
        "DECODE_LOG_FILE": "",
    }
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)


def test_none_mode_agent_imports_no_sandbox_executor_module():
    """Building + selecting on the ``none`` path pulls in NEITHER docker nor modal executor module."""
    code = (
        "import sys; "
        "import decode.tools.bash as b; "
        "from decode.agent.factory import build_agent; "
        "build_agent(); "
        "b._get_executor(); "  # selects the none-mode executor (must not import the sandbox pkg)
        "leaked = [m for m in "
        "('decode.sandbox.docker_backend', 'decode.sandbox.executor', "
        "'decode.sandbox.modal_backend') if m in sys.modules]; "
        "assert not leaked, leaked; "
        "print('OK')"
    )
    result = _run_isolated(code, mode="none")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_docker_mode_repl_agent_imports_no_kitaru_or_modal():
    """A docker-mode REPL agent (build + select) stays kitaru-free and never imports the modal SDK."""
    code = (
        "import sys; "
        "import decode.tools.bash as b; "
        "from decode.agent.factory import build_agent; "
        "build_agent(); "
        "b._get_executor(); "  # selects docker → SandboxExecutor(DockerBackend()) (inert, no daemon)
        "leaked = sorted(m for m in sys.modules "
        "if m == 'kitaru' or m.startswith('kitaru.') or m == 'modal' or m.startswith('modal.')); "
        "assert not leaked, leaked; "
        "print('OK')"
    )
    result = _run_isolated(code, mode="docker")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
