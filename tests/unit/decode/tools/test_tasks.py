"""Unit tests for the gated ``todo_write`` tool (``decode.tools.tasks``) — ADR-0002 §3,7.

Pins the tool's two states (unapproved → raises; approved → replaces ``ctx.deps.task_store``
in place + emits ``TaskListUpdated``), the replace semantics, and one run through a real agent
with ``TestModel`` forcing the call (READ_ONLY → auto-allowed, no prompt). No network.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai import ApprovalRequired, RunContext
from pydantic_ai.models.test import TestModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.entities.task import Task
from decode.harness.runner import Boundary, TurnContext
from decode.permissions.gate import PermissionGate
from decode.tools import tasks as tasks_module
from decode.tools.askuser import deny_user_question_resolver


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _deps(*, emit=None, task_store: list[Task] | None = None) -> AgentDeps:
    return AgentDeps(
        cwd=Path("."),
        emit=emit if emit is not None else (lambda _e: None),
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=deny_user_question_resolver,
        task_store=task_store if task_store is not None else [],
    )


def _ctx(deps: AgentDeps, *, approved: bool) -> RunContext[AgentDeps]:
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=approved)  # type: ignore[arg-type]


def test_todo_write_requires_approval_when_not_approved():
    deps = _deps()

    with pytest.raises(ApprovalRequired):
        tasks_module.todo_write(_ctx(deps, approved=False), tasks=[])


def test_todo_write_does_not_touch_the_store_when_unapproved():
    store = [Task(id="1", content="old")]
    deps = _deps(task_store=store)

    with pytest.raises(ApprovalRequired):
        tasks_module.todo_write(_ctx(deps, approved=False), tasks=[Task(id="2", content="new")])

    # The store is untouched until the call is approved.
    assert deps.task_store == [Task(id="1", content="old")]


def test_todo_write_replaces_the_store_in_place_when_approved():
    store = [Task(id="1", content="old", status="completed")]
    deps = _deps(task_store=store)
    new = [
        Task(id="1", content="design", status="completed"),
        Task(id="2", content="build", status="in_progress"),
        Task(id="3", content="test", status="pending"),
    ]

    tasks_module.todo_write(_ctx(deps, approved=True), tasks=new)

    # TodoWrite semantics: the whole list is replaced, not appended to.
    assert deps.task_store == new
    # Replaced *in place* -> the same list object the deps was built with.
    assert deps.task_store is store


def test_todo_write_emits_a_task_list_updated_event():
    emitted: list[events.Event] = []
    deps = _deps(emit=emitted.append)

    tasks_module.todo_write(
        _ctx(deps, approved=True),
        tasks=[
            Task(id="1", content="design", status="completed"),
            Task(id="2", content="build", status="in_progress"),
            Task(id="3", content="test", status="pending"),
        ],
    )

    updates = [e for e in emitted if isinstance(e, events.TaskListUpdated)]
    assert len(updates) == 1
    rendered = "\n".join(updates[0].tasks)
    # The event carries status-marked checklist lines the TUI can render directly.
    assert "[x] design" in rendered
    assert "[~] build" in rendered
    assert "[ ] test" in rendered


def test_todo_write_can_clear_the_store():
    emitted: list[events.Event] = []
    deps = _deps(emit=emitted.append, task_store=[Task(id="1", content="old")])

    tasks_module.todo_write(_ctx(deps, approved=True), tasks=[])

    assert deps.task_store == []
    updates = [e for e in emitted if isinstance(e, events.TaskListUpdated)]
    assert updates and updates[-1].tasks == ()


# end-to-end: a real agent forced to call the tool


def _agent(mocker):
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


async def _drive_collecting(handler: AgentTurnHandler, ctx: TurnContext) -> list[Boundary]:
    """Drive the handler to completion, draining nothing at each boundary (like the runner)."""
    boundaries: list[Boundary] = []
    agen = handler(ctx)
    boundary = await agen.asend(None)
    while True:
        boundaries.append(boundary)
        try:
            boundary = await agen.asend([])
        except StopAsyncIteration:
            break
    await agen.aclose()
    return boundaries


async def test_todo_write_auto_allows_and_runs_through_a_real_agent(mocker):
    """TestModel forces the ``todo_write`` call; the gate auto-allows it and it runs end to end.

    ``todo_write`` is READ_ONLY (ADR-0003 §2): under DEFAULT mode the gate auto-allows it, so it
    runs with **no** ``PermissionRequested`` event and **without** the human resolver.
    """
    agent = _agent(mocker)
    emitted: list[events.Event] = []
    resolver_calls: list[PermissionRequest] = []

    async def guard_resolver(request: PermissionRequest) -> PermissionDecision:
        resolver_calls.append(request)  # pragma: no cover - must never run on an auto-allow
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=Path("."),
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=guard_resolver,
        resolve_user_question=deny_user_question_resolver,
        task_store=[],
    )
    handler = AgentTurnHandler(agent, deps=deps)
    ctx = TurnContext(0, "track the work", emitted.append)

    with agent.override(model=TestModel(call_tools=["todo_write"])):
        await _drive_collecting(handler, ctx)

    # The read-only tool auto-allowed: no prompt was surfaced and the resolver never ran...
    perms = [e for e in emitted if isinstance(e, events.PermissionRequested)]
    assert not perms, "a read-only tool must auto-allow with no PermissionRequested"
    assert resolver_calls == [], "an auto-allowed call must not reach the human resolver"
    # ...and it still ran: the store was populated and a TaskListUpdated emitted.
    assert deps.task_store, "auto-allowed todo_write must populate the task store"
    assert any(isinstance(e, events.TaskListUpdated) for e in emitted)
