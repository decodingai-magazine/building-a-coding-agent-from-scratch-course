"""Every authored task file carries its task's canary GUID (ADR-0022 §2; task 157).

A benchmark leaks the moment its tasks are scraped into a training corpus: a model that memorised
``001-greeting`` measures nothing. The guard is Terminal-Bench's — one uuid4 per task, stamped as a
comment into every authored TEXT file of the folder, so a corpus can be searched for it and a
contaminated model is detectable. Seed data files under ``environment/`` are exempt: they are what
the agent edits.

This scan runs over every task the loader discovers (the fixture plus the real benchmark set), so a
task authored without a canary — or one that copy-pasted a sibling's GUID — cannot land.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from evals.harness.task_loader import (
    BENCHMARK_TASKS_DIR,
    CANARY_PATTERN,
    BenchmarkTask,
    load_benchmark_tasks,
)

# ``tests/unit/evals/benchmark/`` -> ``tests/unit/evals/`` -> the committed fixture tasks tree.
_FIXTURE_TASKS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tasks"

_TASKS = load_benchmark_tasks(_FIXTURE_TASKS_DIR) + load_benchmark_tasks(BENCHMARK_TASKS_DIR)


def _required_files(task: BenchmarkTask) -> list[Path]:
    """The authored text files that MUST carry the canary — ``setup.sh`` only when the task ships one."""
    files = [
        task.task_dir / "task.toml",
        task.task_dir / "instruction.md",
        task.verifier_script,
        task.oracle_script,
    ]
    if task.setup_script.is_file():
        files.append(task.setup_script)
    return files


def _guid(path: Path) -> str | None:
    """The canary GUID stamped in ``path``, or ``None`` when the file carries no canary line."""
    match = CANARY_PATTERN.search(path.read_text(encoding="utf-8"))
    return match.group("guid") if match else None


@pytest.mark.parametrize("task", _TASKS, ids=lambda task: task.id)
def test_every_authored_file_carries_the_tasks_canary(task: BenchmarkTask) -> None:
    guids = {path: _guid(path) for path in _required_files(task)}

    missing = [str(path) for path, guid in guids.items() if guid is None]
    assert not missing, f"{task.id}: no canary line in {missing}"
    assert len(set(guids.values())) == 1, f"{task.id}: files disagree on the canary GUID: {guids}"


@pytest.mark.parametrize("task", _TASKS, ids=lambda task: task.id)
def test_the_canary_guid_is_a_uuid4(task: BenchmarkTask) -> None:
    guid = _guid(task.task_dir / "task.toml")

    assert guid is not None
    assert uuid.UUID(guid).version == 4, f"{task.id}: canary GUID {guid} is not a uuid4"


def test_each_task_has_its_own_canary_guid() -> None:
    """A copy-pasted GUID would make two tasks indistinguishable in a contamination search."""
    guids = {task.id: _guid(task.task_dir / "task.toml") for task in _TASKS}

    assert len(set(guids.values())) == len(guids), f"duplicate canary GUIDs: {guids}"
