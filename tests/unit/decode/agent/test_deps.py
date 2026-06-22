"""Unit tests for :class:`decode.agent.deps.AgentDeps`.

The deps object is what the agent loop hands to Pydantic AI as ``deps``. For chat-only
(task 004) it carries the working directory and an event sink the loop uses to stream
:mod:`decode.entities.events` to the TUI. Task 005 widens it with the permission ``gate``
(policy) and the async ``resolve_permission`` hook (route an ``ask`` to the human); task 011
adds the ``resolve_user_question`` hook (route the ``ask_user`` tool's question to the human).
Later tasks widen it further (session_log/task_store).
"""

from pathlib import Path

from decode.agent.deps import AgentDeps
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.entities.task import Task
from decode.permissions.gate import PermissionGate


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny(reason="test default")


async def _user_resolver(question: str) -> str:
    return "test answer"


def test_agent_deps_carries_cwd_and_event_sink():
    seen: list[events.Event] = []
    deps = AgentDeps(
        cwd=Path("/tmp/project"),
        emit=seen.append,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
    )

    assert deps.cwd == Path("/tmp/project")

    # The sink is a plain callable that pushes one event downstream.
    event = events.AssistantTextDelta(text="hi")
    deps.emit(event)
    assert seen == [event]


def test_agent_deps_carries_gate_and_resolver():
    gate = PermissionGate()
    deps = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=gate,
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
    )

    assert deps.gate is gate
    assert deps.resolve_permission is _deny_resolver
    # Task 011: the ask_user hook rides alongside the permission resolver.
    assert deps.resolve_user_question is _user_resolver


def test_agent_deps_task_store_defaults_to_an_empty_list():
    # Each run carries its own per-run TodoWrite store; it starts empty (task 009).
    first = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
    )
    second = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
    )

    assert first.task_store == []
    # The default is not shared across instances (no mutable-default aliasing).
    first.task_store.append(Task(id="1", content="x"))
    assert second.task_store == []


def test_agent_deps_carries_a_supplied_task_store():
    store = [Task(id="1", content="x")]
    deps = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
        task_store=store,
    )

    assert deps.task_store is store


def test_agent_deps_emit_is_a_callable_field():
    # `emit` is data, not a method: swapping the sink rebinds where events go.
    first: list[events.Event] = []
    second: list[events.Event] = []
    deps = AgentDeps(
        cwd=Path("."),
        emit=first.append,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
    )
    deps.emit(events.ThinkingDelta(text="a"))

    deps.emit = second.append
    deps.emit(events.ThinkingDelta(text="b"))

    assert [e.text for e in first] == ["a"]
    assert [e.text for e in second] == ["b"]
