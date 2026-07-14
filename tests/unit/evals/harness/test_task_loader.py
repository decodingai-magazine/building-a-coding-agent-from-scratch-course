"""Loader validation for the benchmark task-folder contract (ADR-0017 §2; task 105).

The valid greeting fixture loads cleanly; every contract violation — missing ``task.yaml`` /
``verify.sh``, a blank ``prompt``, a bad ``difficulty``, a non-positive ``max_steps``, an unknown
key, a non-mapping yaml — must raise :class:`BenchmarkTaskError` naming the offending folder rather
than loading silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from evals.harness.task_loader import (
    BenchmarkTaskError,
    load_benchmark_task,
    load_benchmark_tasks,
)

# A minimal well-formed ``task.yaml`` payload the mutate-and-reject tests start from.
_BASE_YAML: dict[str, object] = {
    "id": "001-greeting",
    "prompt": "Create greeting.txt containing hello world.",
    "max_steps": 5,
    "difficulty": "easy",
    "tags": ["files"],
}


def _write_task_yaml(task_dir: Path, **overrides: object) -> None:
    """Overwrite ``task_dir/task.yaml`` with the base payload plus ``overrides``."""
    data = {**_BASE_YAML, **overrides}
    (task_dir / "task.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_loads_a_valid_task(greeting_task_dir: Path) -> None:
    task = load_benchmark_task(greeting_task_dir)

    assert task.id == "001-greeting"
    assert task.difficulty == "easy"
    assert task.max_steps == 5
    assert task.tags == ["files", "fixture"]
    assert task.task_dir == greeting_task_dir
    assert task.verify_script == greeting_task_dir / "verify" / "verify.sh"
    assert task.verify_script.is_file()


def test_parses_optional_judges(greeting_task_dir: Path) -> None:
    task = load_benchmark_task(greeting_task_dir)

    assert [judge.name for judge in task.judges] == ["tone"]
    assert task.judges[0].task_introduction
    assert task.judges[0].evaluation_criteria


def test_scan_finds_the_fixture_task(greeting_task_dir: Path) -> None:
    tasks = load_benchmark_tasks(greeting_task_dir.parent)

    assert [task.id for task in tasks] == ["001-greeting"]


def test_scan_of_missing_root_is_empty(tmp_path: Path) -> None:
    assert load_benchmark_tasks(tmp_path / "does-not-exist") == []


def test_scan_ignores_non_task_dirs_and_files(valid_task_dir: Path, tmp_path: Path) -> None:
    # A README beside the task and a stray dir without task.yaml must both be skipped.
    (tmp_path / "README.md").write_text("docs", encoding="utf-8")
    (tmp_path / "notes").mkdir()

    tasks = load_benchmark_tasks(tmp_path)

    assert [task.id for task in tasks] == ["001-greeting"]


def test_missing_task_yaml_is_rejected(valid_task_dir: Path) -> None:
    (valid_task_dir / "task.yaml").unlink()

    with pytest.raises(BenchmarkTaskError, match=r"missing task\.yaml"):
        load_benchmark_task(valid_task_dir)


def test_missing_verify_script_is_rejected(valid_task_dir: Path) -> None:
    (valid_task_dir / "verify" / "verify.sh").unlink()

    with pytest.raises(BenchmarkTaskError, match=r"verify\.sh"):
        load_benchmark_task(valid_task_dir)


def test_blank_prompt_is_rejected(valid_task_dir: Path) -> None:
    _write_task_yaml(valid_task_dir, prompt="   \n  ")

    with pytest.raises(BenchmarkTaskError, match="prompt"):
        load_benchmark_task(valid_task_dir)


def test_empty_prompt_is_rejected(valid_task_dir: Path) -> None:
    _write_task_yaml(valid_task_dir, prompt="")

    with pytest.raises(BenchmarkTaskError, match="prompt"):
        load_benchmark_task(valid_task_dir)


def test_bad_difficulty_is_rejected(valid_task_dir: Path) -> None:
    _write_task_yaml(valid_task_dir, difficulty="trivial")

    with pytest.raises(BenchmarkTaskError, match="difficulty"):
        load_benchmark_task(valid_task_dir)


def test_non_positive_max_steps_is_rejected(valid_task_dir: Path) -> None:
    _write_task_yaml(valid_task_dir, max_steps=0)

    with pytest.raises(BenchmarkTaskError, match="max_steps"):
        load_benchmark_task(valid_task_dir)


def test_unknown_key_is_rejected(valid_task_dir: Path) -> None:
    _write_task_yaml(valid_task_dir, surprise="nope")

    with pytest.raises(BenchmarkTaskError, match=r"surprise|extra"):
        load_benchmark_task(valid_task_dir)


def test_non_mapping_yaml_is_rejected(valid_task_dir: Path) -> None:
    (valid_task_dir / "task.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(BenchmarkTaskError, match="must be a mapping"):
        load_benchmark_task(valid_task_dir)


def test_error_names_the_offending_folder(valid_task_dir: Path) -> None:
    _write_task_yaml(valid_task_dir, difficulty="wrong")

    with pytest.raises(BenchmarkTaskError, match=str(valid_task_dir)):
        load_benchmark_task(valid_task_dir)
