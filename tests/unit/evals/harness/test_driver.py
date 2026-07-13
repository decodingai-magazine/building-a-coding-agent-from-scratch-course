"""Offline unit tests for the eval agent driver (ADR-0017 §4).

Drives ``run_agent_once`` through the REAL ``build_agent()`` + ``Runner`` with a scripted
``FunctionModel`` (installed as the agent's base model by ``install_model``), and asserts the
returned :class:`EvalRunRecord`: output, tool calls (from ``ToolCallPart``s), steps and summed
usage (from ``ModelResponse.usage``), the deny-resolver path, the ``max_requests`` cap, and a
pre-filled ``message_history``. No network, no keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from support.eval_models import (
    echo_line,
    read_then_finish,
    runaway_reader,
    write_then_finish,
)

from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.rules import Rule, RuleSet
from decode.permissions.types import PermissionMode
from evals.harness.driver import (
    CAP_STOP_TEXT,
    EvalRunRecord,
    ToolCallRecord,
    run_agent_once,
    run_agent_once_sync,
)

_NOTES = "notes.txt"
_NOTES_BODY = "remember to ship the eval suite"
_FINAL = "I read the notes file"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A temp working tree with a readable notes file the scripted models target."""
    (tmp_path / _NOTES).write_text(_NOTES_BODY, encoding="utf-8")
    return tmp_path


async def test_run_agent_once_returns_a_populated_record(workspace, install_model):
    """The happy path: one read then a final line → a fully populated record from the history."""
    install_model(read_then_finish(_NOTES, _FINAL))

    record = await run_agent_once("read the notes file", cwd=workspace)

    assert isinstance(record, EvalRunRecord)
    assert record.output == _FINAL
    # One model-request per leg: the tool-calling leg + the resume leg that produced text.
    assert record.steps == 2
    assert record.tool_calls == [ToolCallRecord(name="read", args={"path": _NOTES})]
    assert record.denied_tools == []
    # The read actually returned the file's contents through the real tool (proof the stack ran).
    assert any(_NOTES_BODY in str(tc) for tc in _tool_return_texts(record))


async def test_tokens_are_summed_from_each_model_response_usage(workspace, install_model):
    """``input_tokens`` / ``output_tokens`` sum every ``ModelResponse.usage`` — not a trace read."""
    install_model(read_then_finish(_NOTES, _FINAL))

    record = await run_agent_once("read the notes file", cwd=workspace)

    responses = [m for m in record.messages if isinstance(m, ModelResponse)]
    assert record.input_tokens == sum(m.usage.input_tokens for m in responses)
    assert record.output_tokens == sum(m.usage.output_tokens for m in responses)
    assert record.input_tokens > 0  # the scripted model reports usage per request


async def test_tool_calls_come_from_tool_call_parts(workspace, install_model):
    """Tool calls are extracted from the message history's ``ToolCallPart``s, in order."""
    install_model(read_then_finish(_NOTES, _FINAL))

    record = await run_agent_once("read the notes file", cwd=workspace)

    names = [tc.name for tc in record.tool_calls]
    assert names == ["read"]
    assert record.tool_calls[0].args == {"path": _NOTES}


async def test_gate_mode_and_deny_resolver_deny_a_mutation(workspace, install_model):
    """Under ``DEFAULT`` mode the mutating ``write`` reaches the resolver; the default denies it."""
    install_model(write_then_finish("out.txt", "hi", "stopped"))

    record = await run_agent_once("write a file", cwd=workspace, gate_mode=PermissionMode.DEFAULT)

    assert record.denied_tools == ["write"]
    assert not (workspace / "out.txt").exists(), "a denied write must never hit disk"


async def test_custom_resolve_permission_can_allow(workspace, install_model):
    """A probe-supplied ``resolve_permission`` overrides the deny default — here it allows the write."""
    install_model(write_then_finish("out.txt", "approved body", "done"))

    async def allow(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    record = await run_agent_once(
        "write a file",
        cwd=workspace,
        gate_mode=PermissionMode.DEFAULT,
        resolve_permission=allow,
    )

    assert record.denied_tools == []
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "approved body"


async def test_permission_rules_auto_allow_a_mutation(workspace, install_model):
    """An allow rule auto-allows the write under ``DEFAULT`` mode — no resolver reached, file lands.

    Proves ``permission_rules`` threads into the gate's user rule set (the default deny resolver
    would otherwise block the write, so a created file is proof the rule fired).
    """
    install_model(write_then_finish("ruled.txt", "by rule", "done"))
    rules = RuleSet(allow=[Rule(tool_name="write")])

    record = await run_agent_once(
        "write a file",
        cwd=workspace,
        gate_mode=PermissionMode.DEFAULT,
        permission_rules=rules,
    )

    assert record.denied_tools == []
    assert (workspace / "ruled.txt").read_text(encoding="utf-8") == "by rule"


async def test_max_requests_caps_a_runaway_run_gracefully(workspace, install_model):
    """A runaway model is stopped at the cap with a plain-text output, not a crash or a hang."""
    install_model(runaway_reader(_NOTES))

    record = await run_agent_once("loop forever", cwd=workspace, max_requests=3)

    # Exactly ``max_requests`` real requests, then one substituted stop response → steps == cap + 1.
    assert record.steps == 4
    assert len(record.tool_calls) == 3
    assert record.output == CAP_STOP_TEXT


async def test_message_history_is_pre_filled(workspace, install_model):
    """A pre-filled ``message_history`` is carried into the run (the compaction probe needs this)."""
    install_model(echo_line("acknowledged"))
    prior = [
        ModelRequest(parts=[UserPromptPart(content="earlier question")]),
        ModelResponse(parts=[TextPart(content="earlier answer")]),
    ]

    record = await run_agent_once("follow up", cwd=workspace, message_history=list(prior))

    # The seeded prefix survives verbatim at the front of the run's history.
    assert record.messages[: len(prior)] == prior
    prompts = [
        str(part.content)
        for message in record.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert "earlier question" in prompts
    assert "follow up" in prompts


def test_run_agent_once_sync_wraps_the_async_driver(workspace, install_model):
    """``run_agent_once_sync`` is a sync entrypoint (Opik ``evaluate()`` task fns cannot be async)."""
    install_model(read_then_finish(_NOTES, _FINAL))

    record = run_agent_once_sync("read the notes file", cwd=workspace)

    assert record.output == _FINAL
    assert record.steps == 2


def _tool_return_texts(record: EvalRunRecord) -> list[str]:
    """Every tool-return content string in the record's message history."""
    from pydantic_ai.messages import ToolReturnPart

    return [
        str(part.content)
        for message in record.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
