"""The plain headless runner — ``run_headless_task`` end to end (ADR-0019 §1).

Drives the REAL :func:`decode.runtime.headless.run_headless_task` with only the model boundary
swapped (a scripted ``FunctionModel`` agent through the ``_build_headless_agent`` seam), so every
property the deleted Durable Flow used to own is proved on the plain path: bypass permissions,
``ask_user`` as a headless no-op, the Workspace warm-up, the sandbox executor reap on a dedicated
loop, the host-side Hand-back, and Opik tracing init. No kitaru, no local stack, no network.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart
from support.kitaru_recording import install_fake_recording_stack
from support.runtime_agents import make_scripted_agent

import decode.runtime.headless as hl
import decode.tools.bash as bash_mod
from decode.entities.permissions import PermissionOutcome
from decode.permissions.types import PermissionMode
from decode.runtime.recording import RecordingUnavailableError

# A syntactically valid Kitaru agent id — the recording opt-in the seam parses (ADR-0019 §3).
AGENT_ID = "6f1d6b6a-6f6f-4c0a-9c9a-0f0f0f0f0f0f"


@pytest.fixture(autouse=True)
def _in_tmp_cwd(tmp_path, monkeypatch):
    """Run every test from a throwaway cwd — it is both the tool scope and the Harness Home."""
    monkeypatch.chdir(tmp_path)


def _patch_agent(monkeypatch, responses):
    """Point the runner's agent seam at a scripted agent; return its model-leg counter."""
    agent, counter = make_scripted_agent(responses)
    monkeypatch.setattr(hl, "_build_headless_agent", lambda model=None: agent)
    return counter


def _text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def _call(tool: str, **args) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=args)])


# --- the answer + the model override -----------------------------------------------------------


def test_run_headless_task_returns_the_agents_final_text(monkeypatch):
    _patch_agent(monkeypatch, [_text("the headless answer")])

    assert hl.run_headless_task("summarize the module") == "the headless answer"


def test_the_model_override_reaches_the_agent_build(monkeypatch):
    captured: dict[str, object] = {}

    def _build_agent(*, model=None):
        captured["model"] = model
        agent, _ = make_scripted_agent([_text("done")])
        return agent

    monkeypatch.setattr(hl, "build_agent", _build_agent)

    assert hl.run_headless_task("do it", model="gemini-2.5-pro") == "done"
    assert captured["model"] == "gemini-2.5-pro"


def test_no_model_override_passes_none_to_the_agent_build(monkeypatch):
    captured: dict[str, object] = {"model": "SENTINEL"}

    def _build_agent(*, model=None):
        captured["model"] = model
        agent, _ = make_scripted_agent([_text("done")])
        return agent

    monkeypatch.setattr(hl, "build_agent", _build_agent)

    hl.run_headless_task("do it")
    assert captured["model"] is None


def test_a_deferred_tool_request_is_a_defensive_error_not_a_hang(monkeypatch):
    """Under BYPASS nothing may defer, so a deferred output is a wiring bug — it must say so."""

    class _DeferringAgent:
        async def run(self, task, deps):
            return SimpleNamespace(output=DeferredToolRequests())

    monkeypatch.setattr(hl, "_build_headless_agent", lambda model=None: _DeferringAgent())

    with pytest.raises(RuntimeError, match="BYPASS"):
        hl.run_headless_task("do it")


# --- bypass semantics: gated tools inline, ask_user a no-op ------------------------------------


def test_the_headless_deps_are_bypass_and_anchor_artifacts_to_the_launch_cwd(tmp_path):
    workspace = tmp_path / "workspace"

    deps = hl._build_headless_deps(workspace)

    assert deps.gate.mode is PermissionMode.BYPASS  # every gated tool runs inline
    assert deps.cwd == workspace  # tool scope = the Workspace
    assert deps.harness_home == tmp_path  # harness artifacts stay at the launch cwd
    assert deps.resolve_user_question is hl.deny_user_question_resolver


async def test_the_deny_permission_resolver_is_a_never_reached_safety_net():
    decision = await hl._deny_permission_resolver(SimpleNamespace(tool_name="write"))

    assert decision.outcome is PermissionOutcome.DENY


def test_a_gated_bash_call_runs_inline_with_no_approval_pause(monkeypatch, tmp_path):
    """AC4: ``bash`` executes for real under ``decode run`` — no prompt, no pause, no denial.

    The side effect is the proof: a gated ``bash`` would raise ``ApprovalRequired`` outside BYPASS,
    which under this runner surfaces as a deferred-output ``RuntimeError`` and no file at all.
    """
    _patch_agent(
        monkeypatch,
        [_call("bash", command="echo inline-bash > bash_ran.txt"), _text("bash ran")],
    )

    assert hl.run_headless_task("run a command") == "bash ran"
    assert (tmp_path / "bash_ran.txt").read_text(encoding="utf-8").strip() == "inline-bash"


def test_a_gated_write_touches_the_file_inline(monkeypatch, tmp_path):
    _patch_agent(
        monkeypatch,
        [_call("write", path="notes.txt", content="written inline"), _text("wrote it")],
    )

    assert hl.run_headless_task("write the notes") == "wrote it"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "written inline"


def test_ask_user_is_a_headless_no_op_the_model_is_told_and_continues(monkeypatch):
    """``ask_user`` never hangs: the resolver refuses and the model gets a readable retry prompt."""
    agent, _counter = make_scripted_agent(
        [_call("ask_user", question="which environment?"), _text("assumed staging")]
    )
    monkeypatch.setattr(hl, "_build_headless_agent", lambda model=None: agent)

    assert hl.run_headless_task("ask me something") == "assumed staging"


def test_ask_user_feeds_the_no_interactive_user_message_back_to_the_model(monkeypatch):
    captured: list[str] = []
    agent, _counter = make_scripted_agent(
        [_call("ask_user", question="which environment?"), _text("assumed staging")]
    )

    class _Recorder:
        async def run(self, task, deps):
            result = await agent.run(task, deps=deps)
            captured.extend(
                part.content
                for message in result.all_messages()
                for part in message.parts
                if isinstance(part, RetryPromptPart) and isinstance(part.content, str)
            )
            return result

    monkeypatch.setattr(hl, "_build_headless_agent", lambda model=None: _Recorder())

    hl.run_headless_task("ask me something")

    assert any("No interactive user is attached" in message for message in captured)


# --- the Recording Seam (ADR-0019 §3) -----------------------------------------------------------


def test_an_unrecorded_run_drives_the_bare_agent(monkeypatch):
    """The default: nothing configured, so the seam hands back the very agent that was built."""
    agent, _counter = make_scripted_agent([_text("done")])
    monkeypatch.setattr(hl, "_build_headless_agent", lambda model=None: agent)
    seen: list[object] = []
    real_wrap = hl.wrap_for_recording

    async def _wrap(built, *, session_name=None):
        result = await real_wrap(built, session_name=session_name)
        seen.append(result)
        return result

    monkeypatch.setattr(hl, "wrap_for_recording", _wrap)

    assert hl.run_headless_task("do it") == "done"
    assert seen == [(agent, None)]  # the same agent back, and nothing to tell the operator


def test_the_run_goes_through_the_recording_seam_named_by_its_session_id(monkeypatch, mocker):
    """``session_name`` is the run's session id — the same id the Session Branch carries."""
    captured: dict[str, object] = {}
    spans: list[str | None] = []
    real_root_span = hl.observability.root_span

    def _root_span(name, *, thread_id=None, input=None):
        spans.append(thread_id)
        return real_root_span(name, thread_id=thread_id, input=input)

    monkeypatch.setattr(hl.observability, "root_span", _root_span)

    async def _wrap(built, *, session_name=None):
        captured["agent"] = built
        captured["session_name"] = session_name
        return built, None

    monkeypatch.setattr(hl, "wrap_for_recording", _wrap)
    _patch_agent(monkeypatch, [_text("done")])

    hl.run_headless_task("record me")

    assert captured["session_name"] == spans[0]


def test_a_recorded_run_executes_through_the_kitaru_wrapper(monkeypatch, capsys):
    """AC2: configured + a reachable (faked) workspace → the run really goes through KitaruAgent."""
    stack = install_fake_recording_stack(monkeypatch)
    monkeypatch.setattr(hl.settings, "kitaru_agent_id", AGENT_ID)
    monkeypatch.setenv("KITARU_API_URL", "https://kitaru.example.invalid")
    _patch_agent(monkeypatch, [_text("recorded answer")])

    assert hl.run_headless_task("record me") == "recorded answer"

    assert capsys.readouterr().err == ""  # a working recording is silent — only a loss is news
    assert len(stack.wrapped) == 1
    assert stack.wrapped[0].agent_id == UUID(AGENT_ID)
    assert stack.wrapped[0].runs == ["record me"]  # the wrapper, not the bare agent, ran the task


def test_an_unreachable_workspace_still_completes_the_run(monkeypatch, caplog):
    """AC3: ONE warning line, the answer still comes back, and nothing raises."""
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("connection refused"))
    monkeypatch.setattr(hl.settings, "kitaru_agent_id", AGENT_ID)
    monkeypatch.setenv("KITARU_API_URL", "https://kitaru.example.invalid")
    _patch_agent(monkeypatch, [_text("unrecorded answer")])

    with caplog.at_level(logging.WARNING, logger="decode.runtime.recording"):
        assert hl.run_headless_task("record me") == "unrecorded answer"

    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


def test_the_degrade_warning_reaches_the_operators_stderr(monkeypatch, caplog, capsys):
    """AC3: "prints ONE warning line" means the terminal, not a log file nobody is tailing.

    The operator asked for a recorded run and is not getting one — a line filed under
    ``.decode/logs/decode.log`` during a headless run is invisible. stdout stays untouched, so a
    piped ``decode run`` still yields exactly the answer.
    """
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("connection refused"))
    monkeypatch.setattr(hl.settings, "kitaru_agent_id", AGENT_ID)
    monkeypatch.setenv("KITARU_API_URL", "https://kitaru.example.invalid")
    _patch_agent(monkeypatch, [_text("unrecorded answer")])

    with caplog.at_level(logging.WARNING, logger="decode.runtime.recording"):
        hl.run_headless_task("record me")

    printed = capsys.readouterr()
    logged = next(r for r in caplog.records if r.levelno >= logging.WARNING).getMessage()
    assert printed.err == f"{logged}\n"  # the same one line, on the terminal too
    assert "kitaru.example.invalid" in printed.err  # ...naming the workspace that was unreachable
    assert "Traceback" not in printed.err
    assert printed.out == ""  # stdout is the answer channel and nothing else


def test_a_worker_task_run_fails_when_the_workspace_is_unreachable(monkeypatch):
    """AC4: under a Worker Task the failure propagates — the cli turns it into a non-zero exit."""
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("connection refused"))
    monkeypatch.setattr(hl.settings, "kitaru_agent_id", AGENT_ID)
    monkeypatch.setenv("KITARU_API_URL", "https://kitaru.example.invalid")
    monkeypatch.setenv("KITARU_TASK_ID", "0f9d1a3e-0000-4000-8000-000000000001")
    _patch_agent(monkeypatch, [_text("never reached")])

    with pytest.raises(RecordingUnavailableError, match="recording is unavailable"):
        hl.run_headless_task("record me")


# --- tracing -----------------------------------------------------------------------------------


def test_tracing_is_initialized_before_the_agent_is_built(monkeypatch):
    """Same order as the TUI: instrument first, then build — otherwise the run exports no spans."""
    order: list[str] = []
    monkeypatch.setattr(hl.observability, "init_tracing", lambda: order.append("init_tracing"))

    def _build(model=None):
        order.append("build_agent")
        agent, _ = make_scripted_agent([_text("done")])
        return agent

    monkeypatch.setattr(hl, "_build_headless_agent", _build)

    hl.run_headless_task("do it")

    assert order == ["init_tracing", "build_agent"]


def test_the_run_opens_one_root_span_whose_thread_id_is_the_hand_back_session_id(
    monkeypatch, mocker
):
    """ONE run id names both the Opik trace thread and the ``decode/<session-id>`` Session Branch."""
    spans: list[dict] = []
    real_root_span = hl.observability.root_span

    def _root_span(name, *, thread_id=None, input=None):
        spans.append({"name": name, "thread_id": thread_id, "input": input})
        return real_root_span(name, thread_id=thread_id, input=input)

    monkeypatch.setattr(hl.observability, "root_span", _root_span)
    monkeypatch.setattr(hl.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(hl, "_prepare_headless_tool_scope", AsyncMock(return_value=Path.cwd()))
    ship = mocker.patch("decode.sandbox.handback.ship_workspace")
    _patch_agent(monkeypatch, [_text("done")])

    hl.run_headless_task("trace me", repo="/some/repo")

    assert len(spans) == 1
    assert spans[0]["name"] == hl.RUN_SPAN_NAME
    assert spans[0]["input"] == "trace me"
    assert ship.call_args.kwargs["session_id"] == spans[0]["thread_id"]


# --- sandbox executor reap ---------------------------------------------------------------------


def _inject_fake_executor(monkeypatch) -> AsyncMock:
    """Put a fake executor with an ``aclose`` spy in the bash seam; return the spy."""
    aclose = AsyncMock()
    monkeypatch.setattr(bash_mod, "_EXECUTOR", SimpleNamespace(aclose=aclose))
    monkeypatch.setattr(bash_mod, "_executor_selected", True)
    return aclose


def test_the_executor_is_reaped_when_the_run_completes(monkeypatch):
    aclose = _inject_fake_executor(monkeypatch)
    _patch_agent(monkeypatch, [_text("done")])

    assert hl.run_headless_task("do it") == "done"
    aclose.assert_awaited_once()
    assert bash_mod._executor_selected is False  # ...and the seam memo was reset


def test_the_executor_is_reaped_even_when_the_run_raises(monkeypatch):
    aclose = _inject_fake_executor(monkeypatch)

    class _BoomAgent:
        async def run(self, task, deps):
            raise RuntimeError("boom from the agent run")

    monkeypatch.setattr(hl, "_build_headless_agent", lambda model=None: _BoomAgent())

    with pytest.raises(RuntimeError, match="boom"):
        hl.run_headless_task("do it")

    aclose.assert_awaited_once()


def test_the_reap_runs_on_a_fresh_loop_and_sweeps_then_destroys(monkeypatch, caplog):
    """Re-homed from the flow: the reap runs on its OWN loop, distinct from the run's (ADR-0012 §2).

    Fresh-exec holds no loop-bound subprocess, so a cross-loop teardown is trivially clean: the
    backend's ``export`` then ``destroy`` run and nothing warns. A recording backend stands in for
    docker.
    """
    from decode.sandbox.executor import SandboxExecutor

    class _RecordingBackend:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def create(self, workspace):  # pragma: no cover - not exercised here
            self.events.append("create")

        async def exec(self, *args, timeout_s):  # pragma: no cover - not exercised here
            raise AssertionError("exec must not run during teardown")

        async def export(self) -> None:
            self.events.append(f"export:{id(asyncio.get_running_loop())}")

        async def destroy(self) -> None:
            self.events.append("destroy")

    backend = _RecordingBackend()
    executor = SandboxExecutor(backend)
    executor._created = True  # a live session so aclose exports + destroys
    monkeypatch.setattr(bash_mod, "_EXECUTOR", executor)
    monkeypatch.setattr(bash_mod, "_executor_selected", True)

    with caplog.at_level(logging.WARNING, logger="decode.runtime.headless"):
        hl._reap_executor()

    assert "headless sandbox teardown failed" not in caplog.text
    assert [event.split(":")[0] for event in backend.events] == ["export", "destroy"]
    assert bash_mod._executor_selected is False


def test_a_teardown_failure_is_logged_never_raised(monkeypatch, caplog):
    monkeypatch.setattr(hl, "close_executor", AsyncMock(side_effect=RuntimeError("no daemon")))

    with caplog.at_level(logging.WARNING, logger="decode.runtime.headless"):
        hl._reap_executor()  # must not raise

    assert "headless sandbox teardown failed" in caplog.text


# --- Workspace preparation ---------------------------------------------------------------------


async def test_none_mode_tool_scope_is_the_launch_cwd_and_warms_nothing(monkeypatch, tmp_path):
    warm = AsyncMock()
    monkeypatch.setattr(hl, "warm_executor", warm)
    monkeypatch.setattr(hl.settings, "sandbox_mode", "none")

    scope = await hl._prepare_headless_tool_scope(None, False)

    assert scope == tmp_path
    warm.assert_not_awaited()


async def test_a_sandbox_mode_clones_then_warms_the_workspace(monkeypatch, mocker, tmp_path):
    workspace = tmp_path / ".decode" / "sandbox"
    prepare = mocker.patch(
        "decode.sandbox.workspace.prepare_workspace_or_empty", return_value=(workspace, None)
    )
    warm = AsyncMock()
    monkeypatch.setattr(hl, "warm_executor", warm)
    monkeypatch.setattr(hl.settings, "sandbox_mode", "docker")

    scope = await hl._prepare_headless_tool_scope("/some/repo", True)

    assert scope == workspace
    assert prepare.call_args.kwargs == {"repo": "/some/repo", "local": True}
    warm.assert_awaited_once_with(workspace)


async def test_a_clone_failure_is_fatal_for_a_headless_run(monkeypatch, mocker, tmp_path):
    """Nobody is watching, so degrading to an empty Workspace would burn the whole run silently."""
    mocker.patch(
        "decode.sandbox.workspace.prepare_workspace_or_empty",
        return_value=(tmp_path, "fatal: repository not found"),
    )
    monkeypatch.setattr(hl, "warm_executor", AsyncMock())
    monkeypatch.setattr(hl.settings, "sandbox_mode", "docker")

    with pytest.raises(RuntimeError, match="nothing to work on"):
        await hl._prepare_headless_tool_scope("/missing/repo", False)


async def test_a_warm_up_failure_degrades_to_a_lazy_start(monkeypatch, mocker, tmp_path, caplog):
    workspace = tmp_path / ".decode" / "sandbox"
    mocker.patch(
        "decode.sandbox.workspace.prepare_workspace_or_empty", return_value=(workspace, None)
    )
    monkeypatch.setattr(hl, "warm_executor", AsyncMock(side_effect=RuntimeError("no daemon")))
    monkeypatch.setattr(hl.settings, "sandbox_mode", "docker")

    with caplog.at_level(logging.WARNING, logger="decode.runtime.headless"):
        scope = await hl._prepare_headless_tool_scope(None, False)

    assert scope == workspace  # the run continues; the first bash retries the start
    assert "warm-up failed" in caplog.text


# --- host-side Hand-back -------------------------------------------------------------------------


def test_a_completed_run_hands_the_workspace_back_after_the_reap(monkeypatch, mocker, tmp_path):
    """AC5 wiring: reap FIRST (it sweeps a modal sandbox), then ship — from this host-side process."""
    order: list[str] = []
    monkeypatch.setattr(hl.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(hl, "_prepare_headless_tool_scope", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(
        hl, "close_executor", AsyncMock(side_effect=lambda: order.append("reap") and None)
    )
    ship = mocker.patch(
        "decode.sandbox.handback.ship_workspace",
        side_effect=lambda *a, **k: (
            order.append("ship")
            or SimpleNamespace(branch="decode/abc", pushed=True, message="handed back")
        ),
    )
    _patch_agent(monkeypatch, [_text("done")])

    assert hl.run_headless_task("build it", repo="/some/repo") == "done"
    assert order == ["reap", "ship"]
    assert ship.call_args.args[0] == tmp_path  # harness home = the launch cwd
    assert ship.call_args.kwargs["repo"] == "/some/repo"


def test_no_repo_means_no_hand_back(monkeypatch, mocker):
    monkeypatch.setattr(hl.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(hl, "_prepare_headless_tool_scope", AsyncMock(return_value=Path.cwd()))
    ship = mocker.patch("decode.sandbox.handback.ship_workspace")
    _patch_agent(monkeypatch, [_text("done")])

    hl.run_headless_task("do it")

    ship.assert_not_called()


def test_none_mode_never_hands_back(monkeypatch, mocker):
    monkeypatch.setattr(hl.settings, "sandbox_mode", "none")
    ship = mocker.patch("decode.sandbox.handback.ship_workspace")
    _patch_agent(monkeypatch, [_text("done")])

    hl.run_headless_task("do it", repo="/some/repo")

    ship.assert_not_called()


def test_a_hand_back_failure_never_fails_a_completed_run(monkeypatch, mocker, caplog):
    monkeypatch.setattr(hl.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(hl, "_prepare_headless_tool_scope", AsyncMock(return_value=Path.cwd()))
    mocker.patch("decode.sandbox.handback.ship_workspace", side_effect=RuntimeError("git exploded"))
    _patch_agent(monkeypatch, [_text("done")])

    with caplog.at_level(logging.WARNING, logger="decode.runtime.headless"):
        assert hl.run_headless_task("do it", repo="/some/repo") == "done"

    assert "hand-back failed" in caplog.text
