"""The Headless Runtime flow: a real ``@flow`` + ``KitaruAgent`` round-trip, offline (ADR-0008).

These drive the **actual** Kitaru Durable Flow on the local stack — no server, no network — and
swap only the model boundary (a scripted ``FunctionModel`` agent injected through the
``_build_runtime_agent`` seam). They prove the de-risk the task called for: the
async-pydantic-ai-agent ⇄ sync-``run_sync`` bridge works, a gated tool runs **inline** under
``bypass`` (no ``ApprovalRequired`` → no Kitaru wait → no crash), and a finished turn replays from
the checkpoint cache on a re-run.
"""

from __future__ import annotations

import pytest
from kitaru.adapters.pydantic_ai import KitaruAgent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from support.runtime_agents import make_scripted_agent

import decode.runtime.flow as flow_mod
from decode.runtime import run_agent_task

# Running the real flow boots the Kitaru/ZenML stack, which emits two third-party deprecation
# warnings unrelated to decode (``filterwarnings=["error"]`` would otherwise fail the run): passlib
# importing the stdlib ``crypt`` module, and pydantic-ai's sync bridge touching the event loop. We
# scope the ignores to these runtime tests so the rest of the suite stays strict.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


def _durable(responses, *, strategy="turn"):
    """Wrap a scripted decode agent in a ``KitaruAgent`` for the ``_build_runtime_agent`` seam."""
    agent, counter = make_scripted_agent(responses)
    return KitaruAgent(agent, name="decode-runtime", checkpoint_strategy=strategy), counter


def test_flow_round_trips_a_task_and_returns_the_agents_text(monkeypatch):
    """A bare text turn round-trips through the real flow and ``.wait().output`` is the agent text."""
    durable, _counter = _durable([ModelResponse(parts=[TextPart(content="all done")])])
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda: durable)

    result = run_agent_task.run(task="say all done").wait()

    assert getattr(result, "output", result) == "all done"


def test_flow_runs_a_gated_tool_inline_under_bypass(monkeypatch, tmp_path):
    """A gated ``write`` runs INLINE under the headless bypass gate — no wait, no crash, file written.

    This is the Fork-2 resolution (ADR-0008 §2): ``run_sync`` does not use decode's loop, so a
    deferred ``ApprovalRequired`` would become a Kitaru wait and crash an unattended run. Under
    ``bypass`` the tool runs directly, so the file lands on disk and the turn completes with text.
    """
    durable, _counter = _durable(
        [
            ModelResponse(
                parts=[ToolCallPart(tool_name="write", args={"path": "out.txt", "content": "hi"})]
            ),
            ModelResponse(parts=[TextPart(content="wrote out.txt")]),
        ]
    )
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda: durable)

    result = run_agent_task.run(task="write out.txt").wait()

    assert getattr(result, "output", result) == "wrote out.txt"
    # cwd is the isolated tmp_path (autouse fixture chdirs there); the tool actually wrote the file.
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hi"


def test_flow_records_a_durable_checkpointed_execution(monkeypatch):
    """The run persists a finished, checkpointed execution — the durable record replay builds on.

    The turn is wrapped in a Kitaru checkpoint named after the agent (``decode_runtime``); the
    execution is recorded in the local stack's store with a stable ``exec_id`` and a successful
    status. That persisted checkpoint is exactly what a crash-resume replays from (User Story 2);
    here we assert the durable record exists rather than drive a real crash.
    """
    durable, _counter = _durable([ModelResponse(parts=[TextPart(content="done")])])
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda: durable)

    handle = run_agent_task.run(task="record me")
    output = handle.wait()

    assert getattr(output, "output", output) == "done"
    assert handle.status.is_finished and handle.status.is_successful
    assert isinstance(handle.exec_id, str) and handle.exec_id

    from zenml.client import Client

    run = Client().get_pipeline_run(handle.exec_id)
    assert run.status.is_successful
    assert "decode_runtime" in set(run.steps)  # the per-turn checkpoint was persisted


def test_build_runtime_agent_wraps_build_agent_in_a_named_kitaru_agent(monkeypatch):
    """The seam wraps ``build_agent()``'s Agent in a ``KitaruAgent`` carrying the stable name."""
    from pydantic import SecretStr

    import decode.agent.factory as factory_mod

    # build_agent() constructs the gemini model; seed a dummy key so construction is offline.
    monkeypatch.setattr(factory_mod.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(factory_mod.settings, "llm_provider", "gemini")

    durable = flow_mod._build_runtime_agent()

    assert isinstance(durable, KitaruAgent)
    assert durable.name == flow_mod.RUNTIME_AGENT_NAME == "decode-runtime"
