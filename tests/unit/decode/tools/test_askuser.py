"""Unit tests for the blocking ``ask_user`` tool (``decode.tools.askuser``) — ADR-0002 §2,7.

``ask_user`` is ungated (it IS the human-interaction tool — gating it would double-prompt).
These tests pin its contract without a model or terminal: it emits ``AskUserRequested``,
returns the resolved free-text answer, and maps a missing interactive user (headless) or a
cancelled request (abort/shutdown) to a clean ``ModelRetry`` — never a hang.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from decode.agent.deps import AgentDeps, UserQuestionResolver
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.tools import askuser as askuser_module


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(
    *,
    resolve_user_question: UserQuestionResolver,
    emit: object = None,
) -> RunContext[AgentDeps]:
    sink = emit if emit is not None else (lambda _e: None)
    deps = AgentDeps(
        cwd=Path("."),
        emit=sink,  # type: ignore[arg-type]
        gate=PermissionGate(),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=resolve_user_question,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=False)  # type: ignore[arg-type]


async def test_ask_user_emits_ask_user_requested_with_the_question():
    emitted: list[events.Event] = []

    async def resolver(question: str) -> str:
        return "blue"

    ctx = _ctx(resolve_user_question=resolver, emit=emitted.append)
    await askuser_module.ask_user(ctx, question="what is your favourite colour?")

    asks = [e for e in emitted if isinstance(e, events.AskUserRequested)]
    assert asks, "ask_user must surface the question via an AskUserRequested event"
    assert asks[0].question == "what is your favourite colour?"


async def test_ask_user_returns_the_resolved_free_text_answer():
    async def resolver(question: str) -> str:
        return "the user typed this"

    ctx = _ctx(resolve_user_question=resolver)
    result = await askuser_module.ask_user(ctx, question="anything?")

    assert result == "the user typed this"


async def test_ask_user_passes_the_question_to_the_resolver():
    seen: list[str] = []

    async def resolver(question: str) -> str:
        seen.append(question)
        return "ok"

    ctx = _ctx(resolve_user_question=resolver)
    await askuser_module.ask_user(ctx, question="which file?")

    assert seen == ["which file?"]


async def test_ask_user_is_not_gated_runs_without_approval():
    # ask_user is the human-interaction tool itself; it must NOT raise ApprovalRequired
    # (tool_call_approved is False in _ctx) — asking before asking would double-prompt.
    async def resolver(question: str) -> str:
        return "answer"

    ctx = _ctx(resolve_user_question=resolver)
    result = await askuser_module.ask_user(ctx, question="q?")

    assert result == "answer"


async def test_ask_user_model_retries_when_no_interactive_user_is_attached():
    # The headless default raises NoInteractiveUserError; ask_user maps it to a model-readable
    # ModelRetry so an unattended run never hangs waiting for a human.
    ctx = _ctx(resolve_user_question=askuser_module.deny_user_question_resolver)

    with pytest.raises(ModelRetry) as excinfo:
        await askuser_module.ask_user(ctx, question="are you there?")

    assert "no interactive user" in str(excinfo.value).lower()


async def test_ask_user_model_retries_when_the_request_is_cancelled():
    # Abort / shutdown cancels the pending request; ask_user must surface a clean ModelRetry
    # instead of letting the CancelledError crash the turn or hang.
    async def cancelling_resolver(question: str) -> str:
        raise asyncio.CancelledError

    ctx = _ctx(resolve_user_question=cancelling_resolver)

    with pytest.raises(ModelRetry):
        await askuser_module.ask_user(ctx, question="still there?")


async def test_deny_user_question_resolver_raises_no_interactive_user_error():
    with pytest.raises(askuser_module.NoInteractiveUserError):
        await askuser_module.deny_user_question_resolver("a question")
