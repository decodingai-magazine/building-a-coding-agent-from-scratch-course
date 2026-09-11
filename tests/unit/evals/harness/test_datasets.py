"""Dataset + Test Suite sync for both eval tracks, offline with a mocked Opik client (ADR-0022 §6,8).

No network and no keys: the tests inject a stubbed ``opik.Opik`` (or a mock ``client``) and assert the
payloads — one item per task (``task_id`` / ``difficulty`` / ``tags``) and, for one Regression Case,
BOTH surfaces from one definition (the dataset item the metrics gate and the Test Suite item its
natural-language assertion grades) — plus the idempotent-by-construction call shape
(``get_or_create_dataset`` / ``get_or_create_test_suite`` then a single ``insert``).
"""

from __future__ import annotations

from pathlib import Path

from decode.config.settings import settings
from evals.harness.datasets import (
    BENCHMARK_DATASET_NAME,
    GLOBAL_ASSERTIONS,
    REGRESSION_DATASET_NAME,
    REGRESSION_SUITE_NAME,
    SUITE_EXECUTION_POLICY,
    benchmark_dataset_item,
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


def _case(
    case_id: str = "c1",
    tags: list[str] | None = None,
    **overrides: object,
) -> RegressionCase:
    fields: dict[str, object] = {
        "id": case_id,
        "prompt": "do it",
        "fixture": lambda _w: None,
        "metrics": [object()],
        "difficulty": "easy",
        "symptom": "harness invariant: it does it.",
        "assertion": "The response says it did it.",
        "tags": tags or ["behavior"],
    }
    fields.update(overrides)
    return RegressionCase(**fields)  # type: ignore[arg-type]


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


def test_regression_dataset_item_carries_the_key_tier_tags_symptom_and_provenance() -> None:
    """The v2 item (ADR-0022 §8): what a human filters, sorts and READS an experiment row by."""
    item = regression_dataset_item(
        _case("smoke-read-tool", tags=["read-discipline"], source_trace_id="trace-7")
    )

    assert item == {
        "case_id": "smoke-read-tool",
        "difficulty": "easy",
        "tags": ["read-discipline"],
        "symptom": "harness invariant: it does it.",
        "source_trace_id": "trace-7",
    }


def test_an_invented_case_carries_no_source_trace() -> None:
    """Only a MINED case has a trace behind it; an invented one says so with ``None``, not a guess."""
    assert regression_dataset_item(_case())["source_trace_id"] is None


def test_regression_suite_item_is_the_prompt_keys_and_the_assertion() -> None:
    """Surface (b) grades the agent's ANSWER against the case's one English quality bar (§8)."""
    item = regression_suite_item(_case("01-read-vs-cat", difficulty="medium"))

    assert item == {
        "data": {"prompt": "do it", "case_id": "01-read-vs-cat", "difficulty": "medium"},
        "assertions": ["The response says it did it."],
    }


def test_regression_item_tags_are_copied_not_aliased() -> None:
    case = _case()

    item = regression_dataset_item(case)

    assert item["tags"] == list(case.tags)
    assert item["tags"] is not case.tags


def test_one_pass_over_the_cases_builds_both_surfaces_in_order() -> None:
    """ONE definition, ONE loop, two registrations — the dataset item and the suite item (§8)."""
    cases = [_case("a"), _case("b")]

    dataset_items, suite_items = regression_items(cases)

    assert [item["case_id"] for item in dataset_items] == ["a", "b"]
    assert [item["data"]["case_id"] for item in suite_items] == ["a", "b"]


def test_sync_regression_cases_writes_the_dataset_and_the_test_suite(mocker) -> None:
    """``sync --regression`` upserts BOTH Opik surfaces from the same case list (§8)."""
    cases = [_case("a", tags=["x"]), _case("b", tags=["y"])]
    client = mocker.Mock()
    dataset = client.get_or_create_dataset.return_value
    suite = client.get_or_create_test_suite.return_value

    surfaces = sync_regression_cases(cases, client=client)

    client.get_or_create_dataset.assert_called_once_with(
        REGRESSION_DATASET_NAME, project_name=settings.eval_project_name
    )
    client.get_or_create_test_suite.assert_called_once_with(
        name=REGRESSION_SUITE_NAME,
        project_name=settings.eval_project_name,
        global_assertions=list(GLOBAL_ASSERTIONS),
        global_execution_policy=SUITE_EXECUTION_POLICY,
    )
    dataset.insert.assert_called_once_with([regression_dataset_item(case) for case in cases])
    suite.insert.assert_called_once_with([regression_suite_item(case) for case in cases])
    assert (surfaces.dataset, surfaces.suite) == (dataset, suite)


def test_sync_regression_cases_can_name_the_suite_for_a_filtered_run(mocker) -> None:
    """A tier run registers its own suite — ``run_tests`` has no per-item scoping (§8)."""
    client = mocker.Mock()

    sync_regression_cases([_case("a")], client=client, suite_name="decode-regression-suite-easy")

    assert client.get_or_create_test_suite.call_args.kwargs["name"] == (
        "decode-regression-suite-easy"
    )


def test_sync_regression_dataset_never_touches_the_suite_api(mocker) -> None:
    """The paid gate path upserts the dataset ONLY — a suite hiccup must not abort a billed run."""
    client = mocker.Mock()
    dataset = client.get_or_create_dataset.return_value

    result = sync_regression_dataset([_case("a", tags=["x"])], client=client)

    client.get_or_create_dataset.assert_called_once_with(
        REGRESSION_DATASET_NAME, project_name=settings.eval_project_name
    )
    client.get_or_create_test_suite.assert_not_called()
    dataset.insert.assert_called_once_with([regression_dataset_item(_case("a", tags=["x"]))])
    assert result is dataset


def test_sync_regression_default_client_is_a_real_opik(mocker) -> None:
    opik_cls = mocker.patch("evals.harness.datasets.opik.Opik")
    client = opik_cls.return_value

    sync_regression_dataset([], client=None)

    opik_cls.assert_called_once_with()
    client.get_or_create_dataset.assert_called_once_with(
        REGRESSION_DATASET_NAME, project_name=settings.eval_project_name
    )


def test_sync_regression_of_no_cases_creates_both_surfaces_but_inserts_nothing(mocker) -> None:
    client = mocker.Mock()
    dataset = client.get_or_create_dataset.return_value
    suite = client.get_or_create_test_suite.return_value

    surfaces = sync_regression_cases([], client=client)

    dataset.insert.assert_not_called()
    suite.insert.assert_not_called()
    assert (surfaces.dataset, surfaces.suite) == (dataset, suite)
