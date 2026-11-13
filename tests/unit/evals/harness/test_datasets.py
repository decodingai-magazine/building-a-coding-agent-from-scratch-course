"""Langfuse dataset payload, versioning, and idempotence tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langfuse.api.commons.errors import NotFoundError

from evals.harness.datasets import (
    BENCHMARK_DATASET_NAME,
    REGRESSION_DATASET_NAME,
    REGRESSION_SUITE_NAME,
    benchmark_dataset_item,
    case_checksum,
    regression_dataset_item,
    regression_items,
    regression_suite_item,
    sync_benchmark_dataset,
    sync_regression_cases,
    sync_regression_dataset,
    task_checksum,
)
from evals.harness.task_loader import load_benchmark_task, load_benchmark_tasks
from evals.regression.case import RegressionCase


class FakeLangfuse:
    def __init__(self) -> None:
        self.datasets: dict[str, Any] = {}
        self.created_items: list[tuple[str, dict[str, Any]]] = []

    def get_dataset(self, name: str) -> Any:
        if name not in self.datasets:
            raise NotFoundError({"message": name})
        return self.datasets[name]

    def create_dataset(self, *, name: str, description: str) -> Any:
        dataset = SimpleNamespace(name=name, description=description, items=[])
        self.datasets[name] = dataset
        return dataset

    def create_dataset_item(self, **payload: Any) -> Any:
        item = SimpleNamespace(id=f"item-{len(self.created_items)}", **payload)
        self.datasets[payload["dataset_name"]].items.append(item)
        self.created_items.append((payload["dataset_name"], payload))
        return item


def _case(case_id: str = "case-1", **overrides: object) -> RegressionCase:
    fields: dict[str, object] = {
        "id": case_id,
        "prompt": "do it",
        "fixture": lambda _workspace: None,
        "metrics": [object()],
        "difficulty": "easy",
        "description": "A test case.",
        "symptom": "The behavior regressed.",
        "assertion": "The response addresses the request.",
        "tags": ["behavior"],
    }
    fields.update(overrides)
    return RegressionCase(**fields)  # type: ignore[arg-type]


def test_benchmark_item_and_checksum(greeting_task_dir: Path) -> None:
    task = load_benchmark_task(greeting_task_dir)
    item = benchmark_dataset_item(task)
    assert item["task_id"] == task.id
    assert item["instruction"] == task.instruction
    assert item["checksum"] == task_checksum(task)
    assert len(task_checksum(task)) == 64


def test_checksum_changes_when_a_task_file_changes(valid_task_dir: Path) -> None:
    task = load_benchmark_task(valid_task_dir)
    before = task_checksum(task)
    verifier = valid_task_dir / "tests" / "test.sh"
    verifier.write_text(verifier.read_text() + "\n# change\n", encoding="utf-8")
    assert task_checksum(load_benchmark_task(valid_task_dir)) != before


def test_case_dataset_item_versions_stable_case_fields() -> None:
    case = _case()
    item = regression_dataset_item(case)
    assert item["case_id"] == case.id
    assert item["checksum"] == case_checksum(case)
    assert len(case_checksum(case)) == 64
    assert case_checksum(_case(description="different")) != item["checksum"]


def test_suite_item_carries_assertion_and_global_bars() -> None:
    item = regression_suite_item(_case())
    assert item["input"] == {"prompt": "do it"}
    assert item["expected_output"]["assertions"][0] == "The response addresses the request."
    assert len(item["expected_output"]["assertions"]) == 3


def test_regression_items_are_built_from_one_case_iteration() -> None:
    dataset_items, suite_items = regression_items([_case()])
    assert dataset_items[0]["metadata"]["case_id"] == suite_items[0]["metadata"]["case_id"]
    assert dataset_items[0]["metadata"]["checksum"] == suite_items[0]["metadata"]["checksum"]


def test_sync_creates_langfuse_dataset_items_idempotently(greeting_task_dir: Path) -> None:
    client = FakeLangfuse()
    tasks = load_benchmark_tasks(greeting_task_dir.parent)

    first = sync_benchmark_dataset(tasks, client=client)
    second = sync_benchmark_dataset(tasks, client=client)

    assert first is second
    assert first.name == BENCHMARK_DATASET_NAME
    assert len(client.created_items) == 1
    assert client.created_items[0][1]["input"]["task_id"] == tasks[0].id
    assert client.created_items[0][1]["metadata"]["checksum"] == task_checksum(tasks[0])


def test_dataset_keeps_old_checksum_but_syncs_new_case_version() -> None:
    client = FakeLangfuse()
    sync_regression_dataset([_case()], client=client)
    sync_regression_dataset([_case(description="updated")], client=client)

    dataset = client.datasets[REGRESSION_DATASET_NAME]
    assert len(dataset.items) == 2
    assert dataset.items[0].metadata["checksum"] != dataset.items[1].metadata["checksum"]


def test_sync_regression_writes_both_datasets_with_a_content_version() -> None:
    client = FakeLangfuse()

    result = sync_regression_cases([_case()], client=client)

    assert result.dataset.name == REGRESSION_DATASET_NAME
    assert result.suite.name == REGRESSION_SUITE_NAME
    assert result.suite_version.startswith("v-")
    assert len(client.created_items) == 2
    assert {name for name, _ in client.created_items} == {
        REGRESSION_DATASET_NAME,
        REGRESSION_SUITE_NAME,
    }


def test_empty_sync_creates_no_items() -> None:
    client = FakeLangfuse()
    sync_benchmark_dataset([], client=client)
    assert client.created_items == []


def test_dataset_lookup_errors_other_than_not_found_are_not_hidden() -> None:
    class BrokenClient(FakeLangfuse):
        def get_dataset(self, name: str) -> Any:
            raise RuntimeError(f"request failed for {name}")

    client = BrokenClient()

    try:
        sync_benchmark_dataset([], client=client)
    except RuntimeError as exc:
        assert "request failed" in str(exc)
    else:
        raise AssertionError("dataset lookup failure was swallowed")

    assert client.datasets == {}
