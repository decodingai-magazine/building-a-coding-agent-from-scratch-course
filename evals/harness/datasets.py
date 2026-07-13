"""Sync the benchmark tasks into an Opik dataset (ADR-0017 §2; task 105).

The Opik dataset ``decode-benchmark-v1`` is the axis every ``evaluate()`` run scores against. One
item per task carries the mechanical facts a metric or an experiment row needs — ``task_id``,
``difficulty``, ``tags``. The heavy assets (prompt, setup/verify/solution folders) stay on disk;
the dataset only needs the key and the slice labels.

Dataset names are code constants, not settings (ADR-0017 §2) — a benchmark version is a property of
the suite, not an operator knob. :func:`sync_benchmark_dataset` is idempotent: ``get_or_create``
never duplicates the dataset and Opik ``insert`` deduplicates items by content, so re-running is
safe. ``opik`` is imported at this module's scope on purpose — the ``python -m evals`` CLI stays
opik-free by importing this module lazily, only inside the ``sync`` command (ADR-0017 §1).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

import opik

from evals.harness.task_loader import BenchmarkTask

if TYPE_CHECKING:
    from opik.api_objects.dataset.dataset import Dataset

# The single benchmark dataset version (ADR-0017 §2). Bumping the suite bumps this constant.
BENCHMARK_DATASET_NAME = "decode-benchmark-v1"


def benchmark_dataset_item(task: BenchmarkTask) -> dict[str, Any]:
    """The Opik dataset item for one task: its key plus the slice labels (ADR-0017 §2).

    ``tags`` is copied into a fresh list so the item never aliases the task's mutable field.
    """
    return {"task_id": task.id, "difficulty": task.difficulty, "tags": list(task.tags)}


def sync_benchmark_dataset(
    tasks: Iterable[BenchmarkTask], *, client: opik.Opik | None = None
) -> Dataset:
    """Upsert one dataset item per task into ``decode-benchmark-v1`` (ADR-0017 §2).

    Uses ``get_or_create_dataset`` (never duplicates the dataset) and a single ``insert`` of all
    items (Opik deduplicates by content, so re-syncing is idempotent). Pass ``client`` to inject a
    stub in tests; the default constructs a real :class:`opik.Opik`, which needs Opik config. An
    empty ``tasks`` is a valid no-op sync (the real benchmark set lands in tasks 108-110). Returns
    the Opik ``Dataset`` handle.
    """
    client = client or opik.Opik()
    dataset = client.get_or_create_dataset(BENCHMARK_DATASET_NAME)
    items: Sequence[dict[str, Any]] = [benchmark_dataset_item(task) for task in tasks]
    if items:
        dataset.insert(items)
    return dataset
