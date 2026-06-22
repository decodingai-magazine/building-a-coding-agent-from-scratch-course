"""Unit tests for the :class:`decode.entities.task.Task` model (ADR-0002 §7).

``Task`` is the in-memory TodoWrite item the model maintains within a session. Like the
other entities it is frozen + slotted so it is cheap and safe to pass across the
queue/stream boundary, but it validates its ``status`` against the three allowed states
(the model supplies these, so a bad value must be rejected loudly rather than silently
stored). These tests pin the validation and the immutability without going through a tool.
"""

import dataclasses

import pytest

from decode.entities.task import Task


def test_task_carries_id_content_and_status():
    task = Task(id="1", content="write the parser", status="in_progress")

    assert task.id == "1"
    assert task.content == "write the parser"
    assert task.status == "in_progress"


def test_task_status_defaults_to_pending():
    assert Task(id="1", content="do a thing").status == "pending"


@pytest.mark.parametrize("status", ["pending", "in_progress", "completed"])
def test_task_accepts_every_valid_status(status):
    assert Task(id="1", content="x", status=status).status == status


def test_task_rejects_an_unknown_status():
    with pytest.raises(ValueError, match="status"):
        Task(id="1", content="x", status="done")  # type: ignore[arg-type]


def test_task_rejects_empty_content():
    with pytest.raises(ValueError, match="content"):
        Task(id="1", content="   ")


def test_task_is_frozen_and_hashable():
    task = Task(id="1", content="x")

    hash(task)  # frozen + slotted -> hashable
    with pytest.raises(dataclasses.FrozenInstanceError):
        task.content = "mutated"  # type: ignore[misc]
