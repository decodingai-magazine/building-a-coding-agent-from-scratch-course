"""Offline tests for the Opik benchmark glue (ADR-0022 §1,§6,§7).

No infra, no subprocess and no keys: :func:`~evals.harness.trial.run_trial` is mocked (the real thing
is pinned end-to-end in ``test_trial.py`` against a fake ``decode``) and ``opik.evaluation.evaluate``
/ ``opik.Opik`` are mocked. The tests cover the trial-to-payload mapping, where a job's Trial Dirs
land, the ``evaluate`` wiring (scoped dataset ids, code metrics, ``experiment_config`` with model +
git sha) and the selection filters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decode.config.settings import settings
from evals.harness.benchmark import (
    BenchmarkSelectionError,
    _select_tasks,
    experiment_config,
    make_benchmark_task_fn,
    new_job_dir,
    run_benchmark,
)
from evals.harness.task_loader import load_benchmark_task
from evals.harness.trial import TrialResult


def test_task_fn_maps_a_trial_onto_the_metric_payload(greeting_task_dir: Path, mocker):
    """One dataset item = one Trial; the payload is that verdict flattened for the metrics.

    The Trial itself is pinned by ``tests/unit/evals/harness/test_trial.py`` against a fake ``decode``
    subprocess — here only the mapping and the wiring are under test.
    """
    task = load_benchmark_task(greeting_task_dir)
    trial = mocker.patch(
        "evals.harness.benchmark.run_trial",
        return_value=TrialResult(
            task_id=task.id,
            trial_id="0a1b2c3d",
            status="agent_ok",
            reason=None,
            reward=1.0,
            timed_out=False,
            base_sha="a" * 40,
            branch="decode/0a1b2c3d",
            agent={"model": "gemini-3.5-flash"},
            summary={
                "output": "all done",
                "requests": 3,
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_usd": 0.01,
            },
            timings={"seed": 0.1, "run": 1.0, "verify": 0.2},
            trial_dir=Path("/tmp/runs/job/001__0a1b2c3d"),
        ),
    )
    task_fn = make_benchmark_task_fn(
        {task.id: task}, sandbox="docker", job_dir=Path("/tmp/runs/job")
    )

    payload = task_fn({"task_id": task.id})

    assert payload["reward"] == 1.0
    assert payload["status"] == "agent_ok"
    assert payload["output"] == "all done"
    assert payload["steps"] == 3
    assert payload["max_steps"] == task.max_steps
    assert payload["cost_usd"] == 0.01
    assert payload["trial_dir"] == "/tmp/runs/job/001__0a1b2c3d"
    assert payload["infra_error"] is None
    assert trial.call_args.kwargs["sandbox"] == "docker"
    assert trial.call_args.kwargs["job_dir"] == Path("/tmp/runs/job")
    assert len(trial.call_args.kwargs["trial_id"]) == 8


def test_task_fn_carries_an_infra_error_reason(greeting_task_dir: Path, mocker):
    """An excluded trial must be distinguishable from a lost one without re-deriving the taxonomy."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch(
        "evals.harness.benchmark.run_trial",
        return_value=TrialResult(
            task_id=task.id,
            trial_id="0a1b2c3d",
            status="infra_error",
            reason="the pristine clone of decode/0a1b2c3d failed",
            reward=None,
            timed_out=False,
            base_sha=None,
            branch=None,
            agent={},
            summary=None,
            timings={"seed": 0.1, "run": None, "verify": None},
            trial_dir=Path("/tmp/runs/job/001__0a1b2c3d"),
        ),
    )
    task_fn = make_benchmark_task_fn({task.id: task})

    payload = task_fn({"task_id": task.id})

    assert payload["reward"] is None
    assert payload["infra_error"] == "the pristine clone of decode/0a1b2c3d failed"
    assert payload["output"] == ""
    assert payload["steps"] == 0


def test_a_job_dir_lands_under_the_harness_home():
    """Trial Dirs are harness artifacts: ``<harness home>/.decode/evals/runs/<job>/`` (ADR-0022 §7)."""
    job_dir = new_job_dir()

    assert job_dir.parts[:3] == (str(settings.decode_dir), "evals", "runs")


def test_run_benchmark_wires_evaluate(mocker, greeting_task_dir: Path):
    """``run_benchmark`` scopes ``evaluate`` to the selected item with the code metrics + config."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate = mocker.patch("opik.evaluation.evaluate")
    opik_cls = mocker.patch("opik.Opik")
    dataset = opik_cls.return_value.get_or_create_dataset.return_value
    dataset.get_items.return_value = [{"id": "item-1", "task_id": task.id}]

    result = run_benchmark(task_id=task.id, sandbox="docker")

    assert result is evaluate.return_value
    _, kwargs = evaluate.call_args
    assert kwargs["dataset"] is dataset
    assert kwargs["project_name"] == settings.eval_project_name
    assert kwargs["dataset_item_ids"] == ["item-1"]
    assert kwargs["task_threads"] == 1  # the bash seam is process-global — no concurrent task fns
    metric_names = [type(metric).__name__ for metric in kwargs["scoring_metrics"]]
    assert metric_names == ["MaxStepsMetric"]  # the reward metric lands with the trial runner (161)
    config = kwargs["experiment_config"]
    assert config["agent_model"]
    assert config["git_sha"]
    assert config["sandbox"] == "docker"


def test_run_benchmark_forwards_the_trial_count(mocker, greeting_task_dir: Path):
    """``--trials k`` rides through to Opik's own ``evaluate(trial_count=k)`` axis (ADR-0017 §8)."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate = mocker.patch("opik.evaluation.evaluate")
    opik_cls = mocker.patch("opik.Opik")
    dataset = opik_cls.return_value.get_or_create_dataset.return_value
    dataset.get_items.return_value = [{"id": "item-1", "task_id": task.id}]

    run_benchmark(task_id=task.id, sandbox="docker", trials=3)

    _, kwargs = evaluate.call_args
    assert kwargs["trial_count"] == 3


def test_run_benchmark_default_trial_count_is_one(mocker, greeting_task_dir: Path):
    """No ``--trials`` means a single trial per item — the historical default (ADR-0017 §8)."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate = mocker.patch("opik.evaluation.evaluate")
    opik_cls = mocker.patch("opik.Opik")
    dataset = opik_cls.return_value.get_or_create_dataset.return_value
    dataset.get_items.return_value = [{"id": "item-1", "task_id": task.id}]

    run_benchmark(task_id=task.id, sandbox="docker")

    _, kwargs = evaluate.call_args
    assert kwargs["trial_count"] == 1


@pytest.mark.parametrize("trials", [0, -5])
def test_run_benchmark_rejects_a_non_positive_trial_count(mocker, greeting_task_dir: Path, trials):
    """``trials < 1`` is a loud programmatic error — never a clean return over zero real trials.

    Opik's ``evaluate(trial_count=-5)`` would ``range()``-loop zero times and return cleanly, so the
    guard must trip BEFORE evaluate is ever reached (else the run reports a nonsense ``pass@-5``).
    """
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate = mocker.patch("opik.evaluation.evaluate")

    with pytest.raises(ValueError, match="trials"):
        run_benchmark(task_id=task.id, sandbox="docker", trials=trials)

    evaluate.assert_not_called()


def test_run_benchmark_attaches_aggregates_to_the_experiment(mocker, greeting_task_dir: Path):
    """After ``evaluate`` returns, the derived pass@k/cost scores land on the experiment traces (§8).

    The 1.9.8 stand-in for ``experiment_scoring_functions``: ``run_benchmark`` summarizes the result
    post-hoc and logs the aggregates as trace feedback scores via the same Opik client.
    """
    from opik.evaluation.metrics.score_result import ScoreResult
    from opik.evaluation.test_case import TestCase
    from opik.evaluation.test_result import TestResult

    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate = mocker.patch("opik.evaluation.evaluate")
    evaluate.return_value.test_results = [
        TestResult(
            test_case=TestCase(
                trace_id="trace-1",
                dataset_item_id="item-1",
                task_output={},
                dataset_item_content={"task_id": task.id},
            ),
            score_results=[ScoreResult(name="verify_oracle", value=1.0)],
            trial_id=0,
        )
    ]
    opik_cls = mocker.patch("opik.Opik")
    client = opik_cls.return_value
    client.get_or_create_dataset.return_value.get_items.return_value = [
        {"id": "item-1", "task_id": task.id}
    ]

    run_benchmark(task_id=task.id, sandbox="docker", trials=1)

    client.log_traces_feedback_scores.assert_called_once()


def test_run_benchmark_survives_an_aggregate_attach_failure(mocker, greeting_task_dir: Path):
    """A failed feedback-score log must NOT sink a completed benchmark run — it returns the result."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate = mocker.patch("opik.evaluation.evaluate")
    opik_cls = mocker.patch("opik.Opik")
    client = opik_cls.return_value
    client.get_or_create_dataset.return_value.get_items.return_value = [
        {"id": "item-1", "task_id": task.id}
    ]
    client.log_traces_feedback_scores.side_effect = RuntimeError("opik unreachable")

    result = run_benchmark(task_id=task.id, sandbox="docker")

    assert result is evaluate.return_value  # the run still returns cleanly


def test_run_benchmark_scopes_evaluate_to_every_selected_item(mocker, greeting_task_dir: Path):
    """An unfiltered run evaluates every task's dataset item, not just the first."""
    task = load_benchmark_task(greeting_task_dir)
    other = task.model_copy(update={"task": task.task.model_copy(update={"name": "002-other"})})
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task, other])
    evaluate = mocker.patch("opik.evaluation.evaluate")
    opik_cls = mocker.patch("opik.Opik")
    dataset = opik_cls.return_value.get_or_create_dataset.return_value
    dataset.get_items.return_value = [
        {"id": "a", "task_id": task.id},
        {"id": "b", "task_id": other.id},
    ]

    run_benchmark(sandbox="docker")

    _, kwargs = evaluate.call_args
    assert set(kwargs["dataset_item_ids"]) == {"a", "b"}


def test_run_benchmark_raises_when_no_task_matches(mocker):
    """An empty selection is a loud, friendly stop — never a silent zero-item experiment."""
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[])

    with pytest.raises(BenchmarkSelectionError):
        run_benchmark(task_id="does-not-exist")


def test_experiment_config_has_model_provider_git_sha_and_sandbox():
    config = experiment_config("modal")

    assert config["provider"] == settings.llm_provider
    assert config["agent_model"]
    assert config["git_sha"]  # this repo is a git checkout
    assert config["sandbox"] == "modal"


def test_select_tasks_filters_by_id_and_difficulty(greeting_task_dir: Path):
    task = load_benchmark_task(greeting_task_dir)

    assert _select_tasks([task], task_id=task.id, difficulty=None) == [task]
    assert _select_tasks([task], task_id="nope", difficulty=None) == []
    assert _select_tasks([task], task_id=None, difficulty="easy") == [task]
    assert _select_tasks([task], task_id=None, difficulty="hard") == []
