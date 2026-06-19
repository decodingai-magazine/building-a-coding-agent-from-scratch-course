"""Unit tests for the canonical event union (``decode.entities.events``).

The events are the contract between the harness (emits) and the TUI (renders), so the
tests pin the two load-bearing properties: every event carries a unique discriminant
``kind`` literal, and the events are frozen (safe to pass across the queue/stream).
"""

import dataclasses

import pytest

from decode.entities import events

ALL_EVENT_TYPES = [
    events.TurnStarted,
    events.TurnFinished,
    events.AssistantTextDelta,
    events.ThinkingDelta,
    events.ToolCallStarted,
    events.ToolResult,
    events.PermissionRequested,
    events.AskUserRequested,
    events.TaskListUpdated,
    events.AgentError,
]


def test_every_event_kind_discriminant_is_unique():
    samples = [
        events.TurnStarted(turn_id=1, prompt="hi"),
        events.TurnFinished(turn_id=1),
        events.AssistantTextDelta(text="x"),
        events.ThinkingDelta(text="x"),
        events.ToolCallStarted(tool_call_id="t", name="bash", args=""),
        events.ToolResult(tool_call_id="t", name="bash", output="ok"),
        events.PermissionRequested(tool_call_id="t", name="bash", args=""),
        events.AskUserRequested(tool_call_id="t", question="?"),
        events.TaskListUpdated(),
        events.AgentError(message="boom"),
    ]
    kinds = [e.kind for e in samples]

    assert len(kinds) == len(set(kinds))
    assert len(samples) == len(ALL_EVENT_TYPES)


@pytest.mark.parametrize("event_type", ALL_EVENT_TYPES)
def test_events_are_frozen_dataclasses(event_type):
    assert dataclasses.is_dataclass(event_type)
    params = event_type.__dataclass_params__
    assert params.frozen is True


def test_turn_finished_defaults_to_not_aborted():
    assert events.TurnFinished(turn_id=1).aborted is False


def test_tool_result_defaults_to_ok():
    assert events.ToolResult(tool_call_id="t", name="bash", output="done").ok is True


def test_task_list_updated_defaults_to_empty_tuple():
    assert events.TaskListUpdated().tasks == ()


def test_assistant_delta_is_hashable_and_immutable():
    delta = events.AssistantTextDelta(text="hello")

    hash(delta)  # frozen + slotted -> hashable
    with pytest.raises(dataclasses.FrozenInstanceError):
        delta.text = "mutated"  # type: ignore[misc]
