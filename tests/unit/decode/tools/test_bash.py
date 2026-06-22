"""Unit tests for the gated ``bash`` tool (``decode.tools.bash``).

ADR-0002 §3,7,10: ``bash`` runs a shell command through the executor seam under
``ctx.deps.cwd``, gates on approval (raises :class:`pydantic_ai.ApprovalRequired` until
approved — the human-in-the-loop *is* the safety gate; no dangerous-command classifier in v1),
enforces ``settings.bash_timeout_s``, and truncates each stream through
:mod:`decode.tools.truncate` (2000 lines / 50 KB, overflow → temp-file path).

The tool functions are driven directly with a hand-built :class:`RunContext` (mirroring
``test_files.py`` / ``test_noop.py``) over ``tmp_path``, plus one run **through a real agent**
with ``TestModel(call_tools=["bash"])`` forcing the gated call and an approving resolver. All
commands are real-but-tiny (``echo`` / ``printf`` / ``false`` / a short ``python`` sleep with a
0.2s timeout / a >2000-line emitter) so the suite stays hermetic and fast — no network.
"""

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.test import TestModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import TurnContext
from decode.permissions.gate import PermissionGate
from decode.tools import bash as bash_module
from decode.tools.askuser import deny_user_question_resolver


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(
    cwd: Path,
    *,
    approved: bool = True,
    resolve: Callable[[PermissionRequest], Awaitable[PermissionDecision]] = _deny_resolver,
) -> RunContext[AgentDeps]:
    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=resolve,
        resolve_user_question=deny_user_question_resolver,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=approved)  # type: ignore[arg-type]


# --- gating: bash asks on every call (no classifier in v1) ----------------------------------


async def test_bash_requires_approval_when_not_approved(tmp_path: Path):
    sentinel = tmp_path / "ran.txt"

    with pytest.raises(ApprovalRequired):
        await bash_module.bash(_ctx(tmp_path, approved=False), command=f"touch {sentinel.name}")
    # Gated BEFORE execution: a denied/unapproved call never runs the command.
    assert not sentinel.exists()


def test_bash_is_tagged_not_read_only():
    assert bash_module.BASH_TOOL_NAME == "bash"
    assert bash_module.BASH_READ_ONLY is False


# --- output capture: stdout, stderr, exit code ----------------------------------------------


async def test_bash_reports_stdout_and_exit_code(tmp_path: Path):
    out = await bash_module.bash(_ctx(tmp_path), command="echo hello")

    assert "hello" in out
    assert "Exit code: 0" in out


async def test_bash_reports_stderr(tmp_path: Path):
    out = await bash_module.bash(_ctx(tmp_path), command="printf boom 1>&2")

    assert "stderr:" in out
    assert "boom" in out


async def test_bash_reports_non_zero_exit_code(tmp_path: Path):
    out = await bash_module.bash(_ctx(tmp_path), command="false")

    # The non-zero status is surfaced to the model (not swallowed).
    assert "Exit code: 0" not in out
    assert "Exit code:" in out


async def test_bash_runs_in_ctx_cwd_not_process_cwd(tmp_path: Path):
    sub = tmp_path / "project"
    sub.mkdir()
    (sub / "marker.txt").write_text("", encoding="utf-8")

    out = await bash_module.bash(_ctx(sub), command="ls")

    assert "marker.txt" in out


async def test_bash_empty_command_returns_model_retry(tmp_path: Path):
    with pytest.raises(ModelRetry):
        await bash_module.bash(_ctx(tmp_path), command="   ")


# --- timeout: kills the process, reports timed_out ------------------------------------------


async def test_bash_times_out_and_tells_the_model(tmp_path: Path):
    out = await bash_module.bash(
        _ctx(tmp_path),
        command=f"{sys.executable} -c 'import time; time.sleep(30)'",
        timeout=0.2,
    )

    assert "timed out" in out.lower()


async def test_bash_timeout_returns_partial_output_to_the_model(tmp_path: Path):
    """The "Partial output below." header must not lie: a flushed-then-hung command's output
    that was captured before the deadline has to reach the model.

    The child writes a sentinel line, flushes, then sleeps past the 0.4s timeout. The reply
    must both flag the timeout AND carry the sentinel under a stdout section — otherwise the
    header promises partial output the model never sees.
    """
    command = (
        f"{sys.executable} -c "
        "'import sys, time; "
        'sys.stdout.write("EARLY-OUT\\n"); sys.stdout.flush(); '
        "time.sleep(30)'"
    )

    out = await bash_module.bash(_ctx(tmp_path), command=command, timeout=0.4)

    assert "timed out" in out.lower()
    assert "stdout:" in out
    assert "EARLY-OUT" in out


async def test_bash_timeout_kills_a_spawned_child(tmp_path: Path):
    """A timed-out command's backgrounded child must not keep running after the kill.

    Same guarantee as the executor test, but exercised through the ``bash`` tool: the child
    appends to a sentinel every 50ms; after the 0.2s timeout the group is killed, so the
    sentinel must stop growing.
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
    command = f"{sys.executable} {child_script} & wait"

    out = await bash_module.bash(_ctx(tmp_path), command=command, timeout=0.2)
    assert "timed out" in out.lower()

    size_at_kill = sentinel.stat().st_size if sentinel.exists() else 0
    await asyncio.sleep(0.5)
    size_after = sentinel.stat().st_size if sentinel.exists() else 0

    assert size_after == size_at_kill, "the spawned child outlived the timed-out bash call"


async def test_bash_clamps_a_too_large_timeout_to_the_configured_max(tmp_path: Path, mocker):
    # The model cannot extend its own leash: a requested timeout above the configured ceiling
    # is clamped down to settings.bash_timeout_s.
    mocker.patch("decode.tools.bash.settings.bash_timeout_s", 0.2, create=False)
    run = mocker.patch(
        "decode.tools.bash._EXECUTOR.run",
        autospec=True,
        return_value=bash_module.ExecResult(stdout="", stderr="", exit_code=0, timed_out=False),
    )

    await bash_module.bash(_ctx(tmp_path), command="echo hi", timeout=9999.0)

    # The executor was asked to run with the clamped ceiling, not the model's 9999s.
    assert run.call_args.kwargs["timeout_s"] == 0.2


async def test_bash_defaults_timeout_to_settings(tmp_path: Path, mocker):
    mocker.patch("decode.tools.bash.settings.bash_timeout_s", 7.5, create=False)
    run = mocker.patch(
        "decode.tools.bash._EXECUTOR.run",
        autospec=True,
        return_value=bash_module.ExecResult(stdout="", stderr="", exit_code=0, timed_out=False),
    )

    await bash_module.bash(_ctx(tmp_path), command="echo hi")

    assert run.call_args.kwargs["timeout_s"] == 7.5


async def test_bash_rejects_non_positive_timeout(tmp_path: Path):
    with pytest.raises(ModelRetry):
        await bash_module.bash(_ctx(tmp_path), command="echo hi", timeout=0)


# --- truncation + temp-file overflow --------------------------------------------------------


async def test_bash_truncates_long_output_and_spills_to_a_temp_file(tmp_path: Path, mocker):
    # Force a tiny line cap so a small command overflows; assert the spill path is mentioned
    # AND the full content lands in the spilled file.
    mocker.patch("decode.tools.bash.settings.max_output_lines", 5, create=False)
    mocker.patch("decode.tools.bash.settings.max_output_bytes", 50_000, create=False)

    out = await bash_module.bash(
        _ctx(tmp_path),
        command=f"{sys.executable} -c 'for i in range(2500): print(i)'",
    )

    assert "truncated" in out.lower()
    # The spill notice names a temp-file path; that file holds the full output (line 2499).
    notice = out.splitlines()[-1]
    assert "full content at" in notice
    spill = Path(notice.split("full content at", 1)[1].strip().rstrip("]"))
    assert spill.exists()
    full = spill.read_text(encoding="utf-8")
    assert "2499" in full  # the full stream is reachable, not just the truncated head


# --- through a real agent: forced bash call, approved -----------------------------------------


def _agent(mocker):
    """A real ``decode`` agent built with a dummy key (the model is overridden per test)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


async def test_bash_runs_through_the_agent_when_approved(tmp_path: Path, mocker):
    """``TestModel(call_tools=["bash"])`` forces the gated bash call; approving resumes the turn.

    This proves the whole path: the model calls ``bash`` → it raises ``ApprovalRequired`` →
    the leg resolves to ``DeferredToolRequests`` → the gate surfaces a ``PermissionRequested``
    event → the approving resolver allows it → bash actually runs on the resume leg and its
    result (the exit-code report) is fed back to the model as a tool return.

    ``TestModel`` synthesises the ``command`` argument itself (we cannot pin it), so we assert
    the *gated-then-executed* contract rather than a specific command's side effect: the bash
    result returned to the model carries the ``Exit code:`` header the tool emits, proving the
    executor seam actually ran on the approved resume leg.
    """
    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=tmp_path,
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=approving_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    agent = _agent(mocker)
    handler = AgentTurnHandler(agent, deps=deps)

    # call_tools=["bash"] forces the model to call bash on the first leg (with a generated
    # command arg), then return text on the resume leg so the turn terminates.
    model = TestModel(call_tools=["bash"])

    async def _run() -> None:
        agen = handler(TurnContext(0, "run a command", emitted.append))
        with contextlib.suppress(StopAsyncIteration):
            await agen.asend(None)
            while True:
                await agen.asend([])
        await agen.aclose()

    with agent.override(model=model):
        await _run()

    # The gated bash call was surfaced and approved.
    perms = [e for e in emitted if isinstance(e, events.PermissionRequested)]
    assert perms and perms[0].name == "bash"

    # bash actually executed on the resume leg: its result (the Exit-code report) reached the
    # model as a tool return — the deferred gate did not short-circuit execution.
    returns = [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "bash"
    ]
    assert returns, "the bash result must reach the model as a tool return"
    assert any("Exit code:" in r for r in returns)
