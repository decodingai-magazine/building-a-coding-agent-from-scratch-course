"""Offline tests for Langfuse-backed benchmark execution and local rollups."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from decode.config.settings import settings
from evals.harness.benchmark import (
    BenchmarkSelectionError,
    default_job_name,
    default_threads,
    experiment_config,
    make_benchmark_task_fn,
    new_job_dir,
    pass_at_k_score,
    run_benchmark,
    summarize,
    trial_payload,
    validate_job_name,
)
from evals.harness.datasets import task_checksum
from evals.harness.langfuse_experiments import ExperimentCase, ExperimentItem
from evals.harness.task_loader import load_benchmark_task
from evals.harness.trial import TrialResult

STARTED_AT = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


def _trial(
    task_id: str = "001-greeting",
    *,
    status: str = "agent_ok",
    reward: float | None = 1.0,
    reason: str | None = None,
    summary: dict | None = None,
) -> TrialResult:
    return TrialResult(
        task_id=task_id,
        trial_id="0a1b2c3d",
        status=status,  # type: ignore[arg-type]
        reason=reason,
        reward=reward,
        timed_out=False,
        base_sha="a" * 40,
        branch="decode/0a1b2c3d" if status != "infra_error" else None,
        agent={"model": "gemini-3.5-flash"},
        summary=summary,
        timings={"seed": 0.1, "run": 1.0, "verify": 0.2},
        started_at=STARTED_AT,
        finished_at=STARTED_AT + timedelta(seconds=1.5),
        trial_dir=Path("runs/job/001-greeting__0a1b2c3d"),
    )


def _fake_result(**overrides):
    return SimpleNamespace(
        name="bench-test",
        experiment_id="experiment-1",
        item_results=[],
        **overrides,
    )


def _client(task, *, items=None):
    from unittest.mock import Mock

    client = Mock()
    dataset_items = items or [
        SimpleNamespace(
            id="item-1",
            input={"task_id": task.id, "instruction": task.instruction},
            metadata={
                "key": task.id,
                "task_id": task.id,
                "checksum": task_checksum(task),
                "difficulty": task.difficulty,
            },
        )
    ]
    client.get_dataset.return_value = SimpleNamespace(name="decode-benchmark", items=dataset_items)
    client.run_experiment.return_value = _fake_result()
    return client


def test_task_fn_maps_trial_to_metric_payload(greeting_task_dir: Path, mocker):
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch(
        "evals.harness.benchmark.run_trial",
        return_value=_trial(
            summary={
                "output": "all done",
                "requests": 3,
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_usd": 0.01,
            }
        ),
    )
    fn = make_benchmark_task_fn(
        {task.id: task}, sandbox="docker", job_dir=Path("runs/job"), model="qwen-x"
    )

    payload = fn({"task_id": task.id})

    assert payload["reward"] == 1.0
    assert payload["output"] == "all done"
    assert payload["steps"] == 3
    assert payload["cost_usd"] == 0.01
    assert payload["input_tokens"] == 10
    assert payload["output_tokens"] == 5


def test_trial_payload_marks_unpriced_infra_error(greeting_task_dir: Path):
    task = load_benchmark_task(greeting_task_dir)
    payload = trial_payload(
        _trial(status="infra_error", reward=None, reason="fixture failure"), task
    )
    assert payload["status"] == "infra_error"
    assert payload["infra_error"] is not None


def test_job_name_and_directory_are_safe():
    assert validate_job_name("bench-2026.11") == "bench-2026.11"
    with pytest.raises(ValueError):
        validate_job_name("../escape")
    assert new_job_dir("bench-test") == Path(settings.decode_dir) / "evals" / "runs" / "bench-test"


def test_default_name_is_sortable_and_threads_follow_sandbox():
    assert default_job_name().startswith("bench-")
    assert default_threads("docker") == 1
    assert default_threads("modal") == 4


def test_experiment_config_records_kitaru_and_run_shape(mocker):
    mocker.patch("evals.harness.benchmark.git_sha", return_value="abc")
    mocker.patch("evals.harness.benchmark.decode_version", return_value="1.2")
    mocker.patch.object(settings, "kitaru_agent_id", "agent-1")

    config = experiment_config(
        sandbox="modal", trials=3, threads=2, job_name="bench-x", model="model-x"
    )

    assert config["git_sha"] == "abc"
    assert config["kitaru_agent_id"] == "agent-1"
    assert config["trials"] == 3
    assert config["threads"] == 2
    assert config["model"] == "model-x"


def test_run_benchmark_repeats_current_dataset_item_per_trial(mocker, greeting_task_dir: Path):
    task = load_benchmark_task(greeting_task_dir)
    client = _client(task)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    mocker.patch("evals.harness.benchmark.run_trial", return_value=_trial(task.id))

    run = run_benchmark(
        task_id=task.id,
        sandbox="docker",
        trials=3,
        threads=2,
        job_name="bench-test",
        client=client,
    )

    call = client.run_experiment.call_args.kwargs
    assert len(call["data"]) == 3
    assert [item["metadata"]["trial_index"] for item in call["data"]] == [0, 1, 2]
    assert call["metadata"]["threads"] == 2
    assert call["max_concurrency"] == 2
    assert run.experiment_name == "bench-test"


def test_run_benchmark_refuses_to_run_when_current_dataset_item_is_missing(
    mocker, greeting_task_dir: Path
):
    task = load_benchmark_task(greeting_task_dir)
    client = _client(
        task,
        items=[
            SimpleNamespace(
                id="stale",
                input={"task_id": task.id},
                metadata={"key": task.id, "task_id": task.id, "checksum": "stale"},
            )
        ],
    )
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])

    with pytest.raises(BenchmarkSelectionError, match="matching dataset item"):
        run_benchmark(task_id=task.id, client=client)
    client.run_experiment.assert_not_called()


def test_run_benchmark_refuses_empty_selection(mocker):
    client = mocker.Mock()
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[])
    with pytest.raises(BenchmarkSelectionError):
        run_benchmark(client=client)
    client.run_experiment.assert_not_called()


def _item(task_id: str, difficulty: str, status: str, reward: float | None) -> ExperimentItem:
    return ExperimentItem(
        test_case=ExperimentCase(
            dataset_item_content={"task_id": task_id, "difficulty": difficulty},
            task_output={
                "status": status,
                "reward": reward,
                "cost_usd": 0.01,
                "input_tokens": 10,
                "output_tokens": 5,
                "run_seconds": 1.0,
                "started_at": STARTED_AT.isoformat(),
                "finished_at": (STARTED_AT + timedelta(seconds=1)).isoformat(),
            },
        ),
        score_results=[],
    )


def test_local_rollup_preserves_pass_k_and_excludes_infra_errors():
    result = SimpleNamespace(
        test_results=[
            _item("a", "easy", "agent_ok", 1.0),
            _item("a", "easy", "agent_fail", 0.0),
            _item("b", "hard", "infra_error", None),
        ]
    )

    summary = summarize(result, trials=2)
    assert summary.total.tasks == 1
    assert summary.total.pass_at_k == 1.0
    assert pass_at_k_score(result.test_results).value == 1.0


def test_summary_is_graceful_without_results():
    assert summarize(SimpleNamespace(), trials=1).total.tasks == 0
