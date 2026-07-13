"""Shared fixtures for the benchmark loader / oracle-sanity / dataset tests (ADR-0017 §2,5).

Exposes the on-disk fixture benchmark task (``tests/unit/evals/fixtures/tasks/001-greeting``) two
ways: :func:`greeting_task_dir` points at the committed original (read-only use), while
:func:`valid_task_dir` hands back a fresh writable copy under ``tmp_path`` so a test can mutate
``task.yaml`` or delete ``verify.sh`` to prove each contract violation is rejected.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

# ``tests/unit/evals/conftest.py`` -> ``tests/unit/evals/`` -> the committed fixture tasks tree.
FIXTURE_TASKS_DIR = Path(__file__).resolve().parent / "fixtures" / "tasks"
GREETING_TASK_DIR = FIXTURE_TASKS_DIR / "001-greeting"


@pytest.fixture
def greeting_task_dir() -> Path:
    """The committed valid fixture task folder (do not mutate — use :func:`valid_task_dir`)."""
    return GREETING_TASK_DIR


@pytest.fixture
def valid_task_dir(tmp_path: Path) -> Path:
    """A fresh writable copy of the greeting fixture task, for mutate-and-reject loader tests."""
    dest = tmp_path / "001-greeting"
    shutil.copytree(GREETING_TASK_DIR, dest)
    return dest
