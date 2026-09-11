"""Dataset sync for both eval tracks, offline with a mocked Opik client (ADR-0017 §2,6; tasks 105, 111).

No network and no keys: the tests inject a stubbed ``opik.Opik`` (or a mock ``client``) and assert the
payloads — one item per task (``task_id`` / ``difficulty`` / ``tags``) and one per probe (``probe_id`` /
``tags``) — plus the idempotent-by-construction call shape (``get_or_create_dataset`` then a single
``insert``).
"""

from __future__ import annotations

from pathlib import Path

from decode.config.settings import settings
from evals.harness.datasets import (
    BENCHMARK_DATASET_NAME,
    REGRESSION_DATASET_NAME,
    benchmark_dataset_item,
    regression_dataset_item,
    sync_benchmark_dataset,
    sync_regression_dataset,
    task_checksum,
)
from evals.harness.task_loader import load_benchmark_task, load_benchmark_tasks
from evals.regression.probe import RegressionProbe


def _probe(probe_id: str = "p1", tags: list[str] | None = None) -> RegressionProbe:
    return RegressionProbe(
        id=probe_id,
        prompt="do it",
        fixture=lambda _w: None,
        metrics=[object()],
        tags=tags or ["behavior"],
    )


def test_item_payload_has_the_slice_labels_the_instruction_and_a_checksum(
    greeting_task_dir: Path,
) -> None:
    """The v2 item (ADR-0022 §6): keys + slice labels + the prompt + the folder's content hash."""
    task = load_benchmark_task(greeting_task_dir)

    item = benchmark_dataset_item(task)

    assert item == {
        "task_id": "001-greeting",
        "difficulty": "easy",
        "category": task.category,
        "tags": ["files", "fixture"],
        "instruction": task.instruction,
        "checksum": task_checksum(task),
    }


def test_checksum_is_stable_across_calls(greeting_task_dir: Path) -> None:
    """Same bytes on disk ⇒ same checksum, so Opik's content dedupe leaves the item alone."""
    task = load_benchmark_task(greeting_task_dir)

    assert task_checksum(task) == task_checksum(task)
    assert len(task_checksum(task)) == 64  # sha256 hexdigest


def test_checksum_changes_when_any_task_file_changes(valid_task_dir: Path) -> None:
    """Editing a task folder must mint a NEW checksum — that is what makes a stale item spottable."""
    before = task_checksum(load_benchmark_task(valid_task_dir))

    verifier = valid_task_dir / "tests" / "test.sh"
    verifier.write_text(verifier.read_text() + "\n# a tweak\n", encoding="utf-8")

    assert task_checksum(load_benchmark_task(valid_task_dir)) != before


def test_checksum_changes_when_a_file_is_renamed(valid_task_dir: Path) -> None:
    """The path is hashed alongside the bytes, so a pure rename is a different task folder."""
    before = task_checksum(load_benchmark_task(valid_task_dir))

    (valid_task_dir / "environment" / "extra.txt").write_text("x", encoding="utf-8")
    first = task_checksum(load_benchmark_task(valid_task_dir))
    (valid_task_dir / "environment" / "extra.txt").rename(
        valid_task_dir / "environment" / "other.txt"
    )

    assert first != before
    assert task_checksum(load_benchmark_task(valid_task_dir)) != first


def test_checksum_ignores_generated_bytecode(valid_task_dir: Path) -> None:
    """``__pycache__`` is a build artifact of running the task's python — never part of its identity."""
    before = task_checksum(load_benchmark_task(valid_task_dir))
    cache = valid_task_dir / "environment" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "mod.cpython-312.pyc").write_bytes(b"\x00\x01")

    assert task_checksum(load_benchmark_task(valid_task_dir)) == before


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

    client.get_or_create_dataset.assert_called_once_with(
        BENCHMARK_DATASET_NAME, project_name=settings.eval_project_name
    )
    dataset.insert.assert_called_once_with([benchmark_dataset_item(tasks[0])])
    assert result is dataset


def test_sync_default_client_is_a_real_opik(mocker) -> None:
    opik_cls = mocker.patch("evals.harness.datasets.opik.Opik")
    client = opik_cls.return_value

    sync_benchmark_dataset([], client=None)

    opik_cls.assert_called_once_with()
    client.get_or_create_dataset.assert_called_once_with(
        BENCHMARK_DATASET_NAME, project_name=settings.eval_project_name
    )


def test_sync_of_no_tasks_creates_dataset_but_inserts_nothing(mocker) -> None:
    client = mocker.Mock()
    dataset = client.get_or_create_dataset.return_value

    result = sync_benchmark_dataset([], client=client)

    client.get_or_create_dataset.assert_called_once_with(
        BENCHMARK_DATASET_NAME, project_name=settings.eval_project_name
    )
    dataset.insert.assert_not_called()
    assert result is dataset


def test_regression_item_payload_has_the_probe_key_and_tags() -> None:
    item = regression_dataset_item(_probe("smoke-read-tool", tags=["read-discipline"]))

    assert item == {"probe_id": "smoke-read-tool", "tags": ["read-discipline"]}


def test_regression_item_tags_are_copied_not_aliased() -> None:
    probe = _probe()

    item = regression_dataset_item(probe)

    assert item["tags"] == list(probe.tags)
    assert item["tags"] is not probe.tags


def test_sync_regression_upserts_one_item_per_probe(mocker) -> None:
    probes = [_probe("a", tags=["x"]), _probe("b", tags=["y"])]
    client = mocker.Mock()
    dataset = client.get_or_create_dataset.return_value

    result = sync_regression_dataset(probes, client=client)

    client.get_or_create_dataset.assert_called_once_with(REGRESSION_DATASET_NAME)
    dataset.insert.assert_called_once_with(
        [{"probe_id": "a", "tags": ["x"]}, {"probe_id": "b", "tags": ["y"]}]
    )
    assert result is dataset


def test_sync_regression_default_client_is_a_real_opik(mocker) -> None:
    opik_cls = mocker.patch("evals.harness.datasets.opik.Opik")
    client = opik_cls.return_value

    sync_regression_dataset([], client=None)

    opik_cls.assert_called_once_with()
    client.get_or_create_dataset.assert_called_once_with(REGRESSION_DATASET_NAME)


def test_sync_regression_of_no_probes_creates_dataset_but_inserts_nothing(mocker) -> None:
    client = mocker.Mock()
    dataset = client.get_or_create_dataset.return_value

    result = sync_regression_dataset([], client=client)

    client.get_or_create_dataset.assert_called_once_with(REGRESSION_DATASET_NAME)
    dataset.insert.assert_not_called()
    assert result is dataset
