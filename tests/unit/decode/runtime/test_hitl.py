"""Headless HITL: durable approvals + ``ask_user`` waits, driven offline (ADR-0008 §3, task 059).

These run the **real** Kitaru ``@flow`` + ``KitaruAgent`` on the local stack — no server, no network
— swapping only the model boundary (a scripted ``FunctionModel`` agent through the
``_build_hitl_runtime_agent`` seam) and resolving each durable wait inline via the
``inline_wait_resolver`` fixture (the hermetic stand-in for ``kitaru executions input``). Together
they prove the de-risk the task called for: the async-resolver → sync-``wait_for_input`` bridge works
under ``run_sync``, ``ask_user`` and a gated ``write`` pause on **named** durable waits, an injected
allow/deny verdict drives the tool, read-only tools run inline (no wait), and an unanswered wait
leaves the run **paused** for out-of-band resolution.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from click.testing import CliRunner
from pydantic import SecretStr
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

import decode.runtime.flow as flow_mod
from decode.agent.deps import AgentDeps
from decode.permissions.types import PermissionMode
from decode.runtime import run_hitl_agent_task
from decode.runtime.flow import (
    HITL_RUNTIME_AGENT_NAME,
    _build_hitl_deps,
    _hitl_wait_name,
    _to_hitl_durable_agent,
    flow_resolve_user_question,
)
from decode.tools import sleep as sleep_module
from decode.tools.registry import register_tools

# The real flow boots the Kitaru/ZenML stack; scope its two third-party deprecation warnings (see
# test_flow.py) so the strict ``filterwarnings=["error"]`` gate stays green here too.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


def _echo_agent(first_call: ModelResponse) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A real decode agent whose model calls one tool, then **echoes that tool's result** as text.

    Leg 1 returns ``first_call`` (a tool call). Once the tool has run, the next leg's request carries
    the tool's ``ToolReturnPart`` (or a ``RetryPromptPart`` when the call was denied), which the model
    echoes verbatim — so a test can prove the injected wait answer / approval verdict actually reached
    the tool and flowed back to the model, not just that the flow completed.
    """

    def model_fn(messages: list, info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        if isinstance(last, ModelRequest):
            for part in last.parts:
                if isinstance(part, ToolReturnPart):
                    return ModelResponse(parts=[TextPart(content=f"tool said: {part.content}")])
                if isinstance(part, RetryPromptPart):
                    return ModelResponse(
                        parts=[TextPart(content=f"retry: {part.model_response()}")]
                    )
        return first_call

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(model_fn),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        name=HITL_RUNTIME_AGENT_NAME,
    )
    register_tools(agent)
    return agent


def _patch_hitl_seam(monkeypatch: pytest.MonkeyPatch, agent: Agent) -> None:
    """Point the HITL runtime seam at ``agent`` wrapped in the real HITL ``KitaruAgent`` config."""
    durable = _to_hitl_durable_agent(agent)
    monkeypatch.setattr(flow_mod, "_build_hitl_runtime_agent", lambda: durable)


@pytest.fixture
def _fast_approval_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the adapter's native ``write``/``edit``/``bash`` approval wait so a pause is testable.

    The approval wait is created *inside* the Kitaru adapter as ``kitaru.wait(timeout=None)``, which
    falls back to ZenML's fixed ``600s`` default — it does **not** honor ``runtime_wait_timeout_s``
    (a documented limitation, ADR-0008 §3). Polling a real 600s deadline would hang the test, so we
    wrap ``kitaru.wait`` to inject a 1s timeout **only when none was passed** (i.e. exactly the native
    approval path); waits decode drives itself (``wait_for_input``) pass an explicit timeout and are
    left untouched. The pause *mechanism* is identical regardless of the timeout value, so this
    faithfully exercises the unanswered-approval pause without waiting 600 seconds.
    """
    import kitaru

    real_wait = kitaru.wait

    def fast_wait(*args, timeout=None, **kwargs):
        return real_wait(*args, timeout=(1 if timeout is None else timeout), **kwargs)

    monkeypatch.setattr(kitaru, "wait", fast_wait)


# ---------------------------------------------------------------------------
# ask_user → named durable question wait
# ---------------------------------------------------------------------------
def test_ask_user_pauses_on_a_named_wait_and_the_answer_becomes_the_tool_result(
    monkeypatch, inline_wait_resolver
):
    """``ask_user`` resolves through a flow-scope ``wait_for_input``; the answer is the tool result."""
    inline_wait_resolver.answers = ["staging"]
    question = "which environment should I target?"
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(parts=[ToolCallPart(tool_name="ask_user", args={"question": question})])
        ),
    )

    result = run_hitl_agent_task("deploy my app")

    assert result.paused is False
    # The model echoed the ask_user tool result, proving the injected answer flowed back.
    assert result.output == "tool said: staging"
    # The flow paused on exactly one wait, named deterministically from the question (Replay reuse),
    # and carrying the model's question text.
    assert inline_wait_resolver.names == [_hitl_wait_name(question)]
    assert inline_wait_resolver.questions == [question]


# ---------------------------------------------------------------------------
# write → durable approval wait (allow / deny)
# ---------------------------------------------------------------------------
def test_write_approval_allow_runs_the_tool(monkeypatch, inline_wait_resolver, tmp_path):
    """An injected ``true`` verdict lets a gated ``write`` run — the file lands on disk."""
    inline_wait_resolver.answers = ["true"]
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(
                parts=[ToolCallPart(tool_name="write", args={"path": "out.txt", "content": "hi"})]
            )
        ),
    )

    result = run_hitl_agent_task("create out.txt with hi")

    assert result.paused is False
    # The approved write actually wrote the file (cwd is the isolated tmp_path).
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hi"
    # The write paused on a durable approval wait before running.
    assert len(inline_wait_resolver.names) == 1
    assert result.output is not None and result.output.startswith("tool said:")


def test_write_approval_deny_stops_the_run_without_writing(
    monkeypatch, inline_wait_resolver, tmp_path
):
    """An injected ``false`` verdict denies the write — no file, and the run stops with a denial.

    The Kitaru adapter resolves a denied approval by raising out of ``run_sync`` (no feed-the-denial-
    back-to-the-model path the way decode's interactive gate has — ADR-0008 §3), so a deny *stops* the
    run. The flow catches that and finishes with a denial message rather than crashing.
    """
    inline_wait_resolver.answers = ["false"]
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(
                parts=[ToolCallPart(tool_name="write", args={"path": "out.txt", "content": "hi"})]
            )
        ),
    )

    result = run_hitl_agent_task("create out.txt with hi")

    assert result.paused is False
    # The denied write never ran, so no file landed on disk.
    assert not (tmp_path / "out.txt").exists()
    assert len(inline_wait_resolver.names) == 1  # the approval wait was created before the deny
    assert result.output is not None and "denied" in result.output.lower()


# ---------------------------------------------------------------------------
# read-only tools run inline (no wait)
# ---------------------------------------------------------------------------
def test_read_only_tool_runs_inline_without_a_wait(monkeypatch, inline_wait_resolver, tmp_path):
    """A read-only ``read`` runs inline under the gating HITL gate — it never creates a wait."""
    (tmp_path / "note.txt").write_text("hello from note", encoding="utf-8")
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": "note.txt"})])
        ),
    )

    result = run_hitl_agent_task("read note.txt")

    assert result.paused is False
    assert inline_wait_resolver.names == []  # no durable wait — read-only ran inline
    assert result.output is not None and "hello from note" in result.output


# ---------------------------------------------------------------------------
# an unanswered wait leaves the run paused
# ---------------------------------------------------------------------------
def test_unanswered_wait_leaves_the_run_paused(monkeypatch, inline_wait_resolver):
    """With no operator answer the durable wait stays pending and the run pauses for out-of-band resolution."""
    inline_wait_resolver.answers = []  # never resolved
    monkeypatch.setattr(flow_mod.settings, "runtime_wait_timeout_s", 1.0)  # give up polling quickly
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(
                parts=[ToolCallPart(tool_name="ask_user", args={"question": "which env?"})]
            )
        ),
    )

    result = run_hitl_agent_task("deploy my app")

    assert result.paused is True
    assert result.output is None
    assert isinstance(result.exec_id, str) and result.exec_id
    assert inline_wait_resolver.names  # the wait was created (and named) before pausing


def test_unanswered_write_approval_leaves_the_run_paused(
    monkeypatch, inline_wait_resolver, _fast_approval_wait
):
    """An unanswered ``write`` **approval** wait (not ask_user) pauses the run for out-of-band resolution.

    The headline destructive-write story: a gated ``write`` raises ``ApprovalRequired`` → the adapter
    opens a durable approval wait. With no operator verdict the wait stays pending and the run pauses,
    exactly like the ``ask_user`` pause — only this path is the native approval wait (bounded by the
    adapter's fixed default, shortened here by ``_fast_approval_wait``), not a decode-driven one.
    """
    inline_wait_resolver.answers = []  # the operator never approves
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(
                parts=[ToolCallPart(tool_name="write", args={"path": "out.txt", "content": "hi"})]
            )
        ),
    )

    result = run_hitl_agent_task("create out.txt with hi")

    assert result.paused is True
    assert result.output is None
    assert isinstance(result.exec_id, str) and result.exec_id
    assert inline_wait_resolver.names  # the approval wait was created (and named) before pausing


# ---------------------------------------------------------------------------
# sleep → durable timer: the async→sync kitaru.wait bridge, end-to-end (task 060, ADR-0008 §4)
# ---------------------------------------------------------------------------
def test_sleep_becomes_a_durable_flow_scope_wait(monkeypatch, inline_wait_resolver):
    """A ``sleep`` in the durable run pauses on a flow-scope ``kitaru.wait`` named "sleep", not ``asyncio.sleep``.

    The de-risk the task called for: the **async** ``sleep`` tool body calls the **sync**
    ``kitaru.wait`` from inside ``run_sync`` (``allow_sync_tool_body_waits=True``) with no event-loop
    error and no deadlock. The wait lands at flow scope (the inline resolver records it, named
    "sleep"), the durable sleeper clamps to the cap, and the flow completes once the wait resolves —
    proving the seam the flow installs is the durable one, not the in-process sleep.
    """
    inline_wait_resolver.answers = ["resume"]  # the timer "fires" → continue the flow
    monkeypatch.setattr(sleep_module.settings, "sleep_max_s", 3.0)
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(parts=[ToolCallPart(tool_name="sleep", args={"seconds": 10_000})])
        ),
    )

    result = run_hitl_agent_task("back off then continue")

    assert result.paused is False
    # The capped confirmation flowed back through the model — the durable wait returned, no hang.
    assert result.output == "tool said: Slept 3.0 s."
    # Exactly one flow-scope wait, named "sleep" (the durable timer), was created and resolved.
    assert inline_wait_resolver.names == [sleep_module.SLEEP_TOOL_NAME]
    # The seam was reset on flow exit — a later in-process ``sleep`` uses ``asyncio.sleep`` again.
    assert sleep_module._SLEEPER is sleep_module._interactive_sleep


def test_durable_sleeper_context_installs_then_resets_the_seam():
    """:func:`_durable_sleeper` swaps in the durable seam inside the block and restores it on exit."""
    assert sleep_module._SLEEPER is sleep_module._interactive_sleep
    with flow_mod._durable_sleeper():
        assert sleep_module._SLEEPER is sleep_module._durable_sleep
    assert sleep_module._SLEEPER is sleep_module._interactive_sleep


def test_durable_sleeper_context_resets_even_on_error():
    """The reset is in a ``finally`` — an exception inside the run must not leak the durable seam."""
    with pytest.raises(RuntimeError), flow_mod._durable_sleeper():
        assert sleep_module._SLEEPER is sleep_module._durable_sleep
        raise RuntimeError("boom")
    assert sleep_module._SLEEPER is sleep_module._interactive_sleep


# ---------------------------------------------------------------------------
# unit: the resolver bridge + deps + agent config (fast, no flow)
# ---------------------------------------------------------------------------
def test_hitl_wait_name_is_deterministic_and_question_derived():
    """A wait name is a pure function of the question, so a Replay reuses the saved answer."""
    question = "which env?"
    expected = f"ask_user:{hashlib.sha1(question.encode('utf-8')).hexdigest()[:8]}"
    assert _hitl_wait_name(question) == expected
    assert _hitl_wait_name(question) == _hitl_wait_name(question)
    assert _hitl_wait_name("a different question") != _hitl_wait_name(question)


def test_flow_resolve_user_question_bridges_to_wait_for_input(monkeypatch):
    """The async resolver calls the sync ``wait_for_input`` with the stable name + str schema + timeout."""
    captured: dict = {}

    def fake_wait_for_input(*, question, name, schema, timeout):
        captured.update(question=question, name=name, schema=schema, timeout=timeout)
        return 123  # non-str, to prove coercion

    monkeypatch.setattr(flow_mod, "wait_for_input", fake_wait_for_input)
    monkeypatch.setattr(flow_mod.settings, "runtime_wait_timeout_s", 42.0)

    answer = asyncio.run(flow_resolve_user_question("pick one?"))

    assert answer == "123"  # coerced to str (the tool-result contract)
    assert captured == {
        "question": "pick one?",
        "name": _hitl_wait_name("pick one?"),
        "schema": str,
        "timeout": 42,
    }


def test_build_hitl_deps_is_gating_with_the_durable_resolver():
    """HITL deps run a gating gate, flag the durable-wait path, and use the wait bridge resolver."""
    deps = _build_hitl_deps()

    assert deps.gate.mode is PermissionMode.DEFAULT  # gating (not BYPASS)
    assert deps.headless_durable_waits is True
    assert deps.resolve_user_question is flow_resolve_user_question


def test_to_hitl_durable_agent_forces_calls_and_opts_out_the_waiting_tools():
    """The HITL ``KitaruAgent`` forces ``calls`` granularity and opts the wait-capable tools out."""
    from kitaru.adapters.pydantic_ai import KitaruAgent

    agent = _echo_agent(ModelResponse(parts=[TextPart(content="done")]))
    durable = _to_hitl_durable_agent(agent)

    assert isinstance(durable, KitaruAgent)
    assert durable.name == HITL_RUNTIME_AGENT_NAME
    # The wait-capable tools are opted out of their per-call checkpoints so their waits land at flow
    # scope; read-only tools are not. ``sleep`` joins them (task 060): once the durable sleeper is
    # installed it pauses on a flow-scope ``kitaru.wait``, so it needs the same opt-out as the gated
    # waiters even though it is ungated.
    assert (
        frozenset({"write", "edit", "bash", "ask_user", "exit_plan_mode", "sleep"})
        == flow_mod._HITL_WAIT_TOOL_NAMES
    )


# ---------------------------------------------------------------------------
# CLI: ``decode run --hitl``
# ---------------------------------------------------------------------------
@pytest.fixture
def _provider_ok(monkeypatch):
    """Seed the gemini provider config so the ``decode run`` provider guard passes (offline)."""
    import decode.cli as cli_mod

    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)


def test_cli_run_hitl_prints_the_resolved_output(monkeypatch, inline_wait_resolver, _provider_ok):
    """``decode run --hitl`` drives the HITL flow and prints the agent's text once the wait resolves."""
    from decode.cli import cli

    inline_wait_resolver.answers = ["staging"]
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(
                parts=[ToolCallPart(tool_name="ask_user", args={"question": "which env?"})]
            )
        ),
    )

    result = CliRunner().invoke(cli, ["run", "--hitl", "deploy my app"])

    assert result.exit_code == 0
    assert "tool said: staging" in result.output


def test_cli_run_hitl_reports_a_paused_execution(monkeypatch, inline_wait_resolver, _provider_ok):
    """An unresolved wait makes ``decode run --hitl`` exit zero with the out-of-band resolution hint."""
    from decode.cli import cli

    inline_wait_resolver.answers = []  # never resolved → the run pauses
    monkeypatch.setattr(flow_mod.settings, "runtime_wait_timeout_s", 1.0)
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(
                parts=[ToolCallPart(tool_name="ask_user", args={"question": "which env?"})]
            )
        ),
    )

    result = CliRunner().invoke(cli, ["run", "--hitl", "deploy my app"])

    assert result.exit_code == 0
    # The pause + how to resolve it out-of-band is surfaced on stderr.
    assert "paused" in result.stderr.lower()
    assert "kitaru executions input" in result.stderr


def test_cli_run_hitl_reports_a_paused_write_approval(
    monkeypatch, inline_wait_resolver, _fast_approval_wait, _provider_ok
):
    """An unanswered ``write`` **approval** makes ``decode run --hitl`` exit 0 with the exec-id + hint.

    The destructive-write counterpart of the ask_user pause CLI test: a gated ``write`` opens a
    native approval wait, no operator approves it, and the CLI surfaces the paused execution id plus
    the ``kitaru executions input`` resolution hint on stderr, exit 0 — the documented behavior.
    """
    from decode.cli import cli

    inline_wait_resolver.answers = []  # never approved → the run pauses
    _patch_hitl_seam(
        monkeypatch,
        _echo_agent(
            ModelResponse(
                parts=[ToolCallPart(tool_name="write", args={"path": "out.txt", "content": "hi"})]
            )
        ),
    )

    result = CliRunner().invoke(cli, ["run", "--hitl", "create out.txt with hi"])

    assert result.exit_code == 0
    assert "paused" in result.stderr.lower()
    assert "kitaru executions input" in result.stderr
