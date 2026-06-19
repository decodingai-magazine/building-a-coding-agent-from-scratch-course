"""Unit tests for :class:`decode.agent.deps.AgentDeps`.

The deps object is what the agent loop hands to Pydantic AI as ``deps``. For chat-only
(task 004) it carries the working directory and an event sink the loop uses to stream
:mod:`decode.entities.events` to the TUI. Later tasks widen it (gate/session_log/task_store)
— these tests only pin the task-004 surface.
"""

from pathlib import Path

from decode.agent.deps import AgentDeps
from decode.entities import events


def test_agent_deps_carries_cwd_and_event_sink():
    seen: list[events.Event] = []
    deps = AgentDeps(cwd=Path("/tmp/project"), emit=seen.append)

    assert deps.cwd == Path("/tmp/project")

    # The sink is a plain callable that pushes one event downstream.
    event = events.AssistantTextDelta(text="hi")
    deps.emit(event)
    assert seen == [event]


def test_agent_deps_emit_is_a_callable_field():
    # `emit` is data, not a method: swapping the sink rebinds where events go.
    first: list[events.Event] = []
    second: list[events.Event] = []
    deps = AgentDeps(cwd=Path("."), emit=first.append)
    deps.emit(events.ThinkingDelta(text="a"))

    deps.emit = second.append
    deps.emit(events.ThinkingDelta(text="b"))

    assert [e.text for e in first] == ["a"]
    assert [e.text for e in second] == ["b"]
