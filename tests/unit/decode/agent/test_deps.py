"""Unit tests for :class:`decode.agent.deps.AgentDeps`.

The deps object is what the agent loop hands to Pydantic AI as ``deps``. For chat-only
(task 004) it carries the working directory and an event sink the loop uses to stream
:mod:`decode.entities.events` to the TUI. Task 005 widens it with the permission ``gate``
(policy) and the async ``resolve_permission`` hook (route an ``ask`` to the human); task 011
adds the ``resolve_user_question`` hook (route the ``ask_user`` tool's question to the human).
Later tasks widen it further (session_log/task_store).
"""

from pathlib import Path

from decode.agent.deps import AgentDeps, VerboseFlag
from decode.agents.loader import load_agent
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


def test_agent_deps_active_agent_defaults_to_build(mocker):
    # The active agent (ADR-0003 §7) defaults to `build` (the full-tool persona) so a deps built
    # without one behaves as M1 did. `run_app` / the selection helper override it.
    deps = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
    )

    assert deps.active_agent.name == "build"


def test_agent_deps_active_agent_is_mutable(mocker):
    # `/agent` (task 022) mutates `deps.active_agent` in place — no agent rebuild (ADR-0003 §7).
    deps = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
    )
    plan = load_agent("plan")

    deps.active_agent = plan

    assert deps.active_agent is plan


def test_agent_deps_carries_a_supplied_active_agent():
    plan = load_agent("plan")
    deps = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
        active_agent=plan,
    )

    assert deps.active_agent is plan


def test_agent_deps_harness_home_defaults_to_cwd():
    # ADR-0012 §6: a deps built without ``harness_home`` (every none-mode caller + pre-split test) gets
    # ``harness_home == cwd`` — the equal-roots back-compat case, byte-identical to before the split.
    deps = AgentDeps(
        cwd=Path("/tmp/project"),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
    )

    assert deps.harness_home == Path("/tmp/project")
    assert deps.harness_home == deps.cwd


def test_agent_deps_harness_home_is_independent_of_cwd_when_supplied():
    # A sandbox launch splits them: ``cwd`` = the Workspace (tool scope), ``harness_home`` = the launch
    # cwd (artifact root). They are carried independently (ADR-0012 §6).
    deps = AgentDeps(
        cwd=Path("/tmp/project/.decode/sandbox"),
        harness_home=Path("/tmp/project"),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_user_resolver,
    )

    assert deps.cwd == Path("/tmp/project/.decode/sandbox")
    assert deps.harness_home == Path("/tmp/project")
    assert deps.harness_home != deps.cwd


def test_agent_deps_verbose_flag_defaults_off_and_is_per_instance():
    # The Ctrl+O verbose toggle: OFF by default, and a MUTABLE per-instance object (like the gate),
    # so the TUI keybind can flip it live mid-turn and every reader sees the new value.
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

    assert first.verbose.enabled is False
    assert first.verbose is not second.verbose  # per-instance, never a shared default


def test_verbose_flag_toggle_flips_in_place_and_reports_the_new_state():
    flag = VerboseFlag()

    assert flag.toggle() is True
    assert flag.enabled is True
    assert flag.toggle() is False
    assert flag.enabled is False


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
