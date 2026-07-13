"""Benchmark dataset sync, verified offline with a mocked Opik client (ADR-0017 §2; task 105).

No network and no keys: the tests inject a stubbed ``opik.Opik`` (or a mock ``client``) and assert
the payloads — one item per task carrying ``task_id`` / ``difficulty`` / ``tags`` — plus the
idempotent-by-construction call shape (``get_or_create_dataset`` then a single ``insert``).
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.datasets import (
    BENCHMARK_DATASET_NAME,
    benchmark_dataset_item,
    sync_benchmark_dataset,
)
from evals.harness.task_loader import load_benchmark_task, load_benchmark_tasks


def test_item_payload_has_the_slice_labels(greeting_task_dir: Path) -> None:
    task = load_benchmark_task(greeting_task_dir)

    item = benchmark_dataset_item(task)

    assert item == {
        "task_id": "001-greeting",
        "difficulty": "easy",
        "tags": ["files", "fixture"],
    }


def test_item_tags_are_copied_not_aliased(greeting_task_dir: Path) -> None:
    task = load_benchmark_task(greeting_task_dir)

    item = benchmark_dataset_item(task)

    assert item["tags"] == list(task.tags)
    assert item["tags"] is not task.tags


def test_sync_upserts_one_item_per_task(mocker, greeting_task_dir: Path) -> None:
    tasks = load_benchmark_tasks(greeting_task_dir.parent)
    client = mocker.Mock()
    dataset = client.get_or_create_dataset.return_value

    result = sync_benchmark_dataset(tasks, client=client)

    client.get_or_create_dataset.assert_called_once_with(BENCHMARK_DATASET_NAME)
    dataset.insert.assert_called_once_with(
        [{"task_id": "001-greeting", "difficulty": "easy", "tags": ["files", "fixture"]}]
    )
    assert result is dataset


def test_sync_default_client_is_a_real_opik(mocker) -> None:
    opik_cls = mocker.patch("evals.harness.datasets.opik.Opik")
    client = opik_cls.return_value

    sync_benchmark_dataset([], client=None)

    opik_cls.assert_called_once_with()
    client.get_or_create_dataset.assert_called_once_with(BENCHMARK_DATASET_NAME)


def test_sync_of_no_tasks_creates_dataset_but_inserts_nothing(mocker) -> None:
    client = mocker.Mock()
    dataset = client.get_or_create_dataset.return_value

    result = sync_benchmark_dataset([], client=client)

    client.get_or_create_dataset.assert_called_once_with(BENCHMARK_DATASET_NAME)
    dataset.insert.assert_not_called()
    assert result is dataset
