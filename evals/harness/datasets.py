"""Langfuse datasets for benchmark tasks and regression cases."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decode.config.settings import settings
from evals.harness.task_loader import BenchmarkTask

BENCHMARK_DATASET_NAME = "decode-benchmark"
REGRESSION_DATASET_NAME = "decode-regression"
REGRESSION_SUITE_NAME = "decode-regression-suite"
CHECKSUM_IGNORED_DIR_NAMES = frozenset({"__pycache__"})
CHECKSUM_IGNORED_SUFFIXES = (".pyc",)

GLOBAL_ASSERTIONS: tuple[str, ...] = (
    "The response directly addresses what the user asked in the prompt.",
    "The response does not invent files, functions, or facts that are not grounded in the "
    "workspace the agent was given or in the user's own prompt.",
)


def langfuse_client() -> Any:
    """Create the SDK client lazily so CLI help and offline imports need no credentials."""
    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_base_url,
    )


def _dataset(client: Any, name: str, description: str) -> Any:
    """Get an existing dataset, creating it only when absent."""
    from langfuse.api.commons.errors import NotFoundError

    try:
        return client.get_dataset(name)
    except NotFoundError:
        return client.create_dataset(name=name, description=description)


def dataset_items(dataset: Any) -> list[Any]:
    """Read dataset items from the Langfuse dataset handle."""
    items = getattr(dataset, "items", [])
    return list(items) if isinstance(items, (list, tuple)) else []


def _value(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _existing_checksums(dataset: Any) -> set[tuple[str, str]]:
    return {
        (
            str(_value(item, "metadata").get("key", "")),
            str(_value(item, "metadata").get("checksum", "")),
        )
        for item in dataset_items(dataset)
        if isinstance(_value(item, "metadata"), dict)
    }


def _upsert_items(client: Any, name: str, description: str, items: Iterable[dict[str, Any]]) -> Any:
    """Append missing content versions, preserving old dataset items for history."""
    dataset = _dataset(client, name, description)
    known = _existing_checksums(dataset)
    for item in items:
        metadata = item["metadata"]
        identity = (str(metadata["key"]), str(metadata["checksum"]))
        if identity in known:
            continue
        client.create_dataset_item(
            dataset_name=name,
            input=item["input"],
            expected_output=item.get("expected_output"),
            metadata=metadata,
        )
        known.add(identity)
    return dataset


def benchmark_dataset_item(task: BenchmarkTask) -> dict[str, Any]:
    """Return one benchmark item with its version marker and slice metadata."""
    return {
        "task_id": task.id,
        "difficulty": task.difficulty,
        "category": task.category,
        "tags": list(task.tags),
        "instruction": task.instruction,
        "checksum": task_checksum(task),
    }


def task_checksum(task: BenchmarkTask) -> str:
    """Hash every task file in stable relative-path order."""
    digest = hashlib.sha256()
    for path, relative in _checksummed_files(task.task_dir):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _checksummed_files(task_dir: Path) -> Iterator[tuple[Path, str]]:
    files = [
        (path, path.relative_to(task_dir).as_posix())
        for path in task_dir.rglob("*")
        if path.is_file() and not _is_generated(path.relative_to(task_dir))
    ]
    yield from sorted(files, key=lambda pair: pair[1])


def _is_generated(relative: Path) -> bool:
    return bool(CHECKSUM_IGNORED_DIR_NAMES.intersection(relative.parts)) or relative.suffix in (
        CHECKSUM_IGNORED_SUFFIXES
    )


def sync_benchmark_dataset(tasks: Iterable[BenchmarkTask], *, client: Any = None) -> Any:
    """Upsert benchmark task versions into the named Langfuse dataset."""
    client = client or langfuse_client()
    items = []
    for task in tasks:
        content = benchmark_dataset_item(task)
        items.append(
            {
                "input": {"task_id": task.id, "instruction": task.instruction},
                "metadata": {
                    **content,
                    "key": task.id,
                    "checksum": content["checksum"],
                    "project_name": settings.eval_project_name,
                },
            }
        )
    return _upsert_items(client, BENCHMARK_DATASET_NAME, "decode benchmark tasks", items)


@dataclass(frozen=True)
class RegressionSurfaces:
    """The deterministic and natural-language Langfuse datasets."""

    dataset: Any
    suite: Any
    suite_version: str | None


def regression_dataset_item(case: Any) -> dict[str, Any]:
    """Return stable metadata used by regression metrics and filtering."""
    return {
        "case_id": case.id,
        "difficulty": case.difficulty,
        "tags": list(case.tags),
        "description": case.description,
        "symptom": case.symptom,
        "source_trace_id": case.source_trace_id,
        "checksum": case_checksum(case),
    }


def case_checksum(case: Any) -> str:
    """Hash the stable fields that define a regression case."""
    digest = hashlib.sha256()
    parts = (
        case.id,
        case.difficulty,
        str(len(case.tags)),
        *case.tags,
        case.symptom,
        case.assertion,
        case.description,
        case.prompt,
    )
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def regression_suite_item(case: Any) -> dict[str, Any]:
    """Return the input, rubric, and metadata for one natural-language quality case."""
    return {
        "input": {"prompt": case.prompt},
        "expected_output": {"assertions": [case.assertion, *GLOBAL_ASSERTIONS]},
        "metadata": {
            "key": case.id,
            "case_id": case.id,
            "difficulty": case.difficulty,
            "checksum": case_checksum(case),
            "description": case.description,
        },
    }


def regression_items(cases: Iterable[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dataset_items: list[dict[str, Any]] = []
    suite_items: list[dict[str, Any]] = []
    for case in cases:
        content = regression_dataset_item(case)
        dataset_items.append(
            {
                "input": {"case_id": case.id, "prompt": case.prompt},
                "metadata": {
                    **content,
                    "key": case.id,
                    "checksum": content["checksum"],
                    "project_name": settings.eval_project_name,
                },
            }
        )
        suite_items.append(regression_suite_item(case))
    return dataset_items, suite_items


def _suite_version(items: list[dict[str, Any]]) -> str | None:
    if not items:
        return None
    digest = hashlib.sha256(
        "|".join(sorted(str(item["metadata"]["checksum"]) for item in items)).encode()
    ).hexdigest()
    return f"v-{digest[:12]}"


def sync_regression_cases(cases: Iterable[Any], *, client: Any = None) -> RegressionSurfaces:
    """Upsert both regression datasets from a single case iteration."""
    client = client or langfuse_client()
    dataset_items, suite_items = regression_items(cases)
    dataset = _upsert_items(
        client,
        REGRESSION_DATASET_NAME,
        "decode deterministic regression cases",
        dataset_items,
    )
    suite = _upsert_items(
        client,
        REGRESSION_SUITE_NAME,
        "decode natural-language regression assertions",
        suite_items,
    )
    return RegressionSurfaces(dataset, suite, _suite_version(suite_items))


def sync_regression_dataset(cases: Iterable[Any], *, client: Any = None) -> Any:
    """Sync only the deterministic regression dataset."""
    client = client or langfuse_client()
    items, _ = regression_items(cases)
    return _upsert_items(
        client,
        REGRESSION_DATASET_NAME,
        "decode deterministic regression cases",
        items,
    )


def regression_items_for_dataset(dataset: Any, checksums: dict[str, str]) -> dict[str, str]:
    """Select the current dataset item id for each case checksum."""
    selected: dict[str, str] = {}
    for item in dataset_items(dataset):
        metadata = _value(item, "metadata")
        item_id = _value(item, "id")
        if not isinstance(metadata, dict) or not isinstance(item_id, str):
            continue
        case_id = metadata.get("case_id")
        if case_id in selected or checksums.get(case_id) != metadata.get("checksum"):
            continue
        selected[case_id] = item_id
    return selected


def benchmark_items_for_dataset(dataset: Any, checksums: dict[str, str]) -> dict[str, str]:
    """Select the current dataset item id for each task checksum."""
    selected: dict[str, str] = {}
    for item in dataset_items(dataset):
        metadata = _value(item, "metadata")
        item_id = _value(item, "id")
        if not isinstance(metadata, dict) or not isinstance(item_id, str):
            continue
        task_id = metadata.get("task_id")
        if task_id in selected or checksums.get(task_id) != metadata.get("checksum"):
            continue
        selected[task_id] = item_id
    return selected
