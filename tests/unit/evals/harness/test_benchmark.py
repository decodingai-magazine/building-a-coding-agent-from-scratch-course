"""Offline tests for the Opik benchmark glue (ADR-0017 §3,4,5; task 106).

No infra and no keys: the sandbox seam is the in-memory :class:`~support.fake_sandbox.FakeExecutor`
(``install_fake``), the agent runs a scripted model (``install_model``), and ``opik.evaluation.evaluate``
/ ``opik.Opik`` are mocked. The tests cover the task-fn payload shape, the crashed-run ``agent_error``
surfacing, the ``evaluate`` wiring (scoped dataset ids, code metrics + single-task judge,
``experiment_config`` with model + git sha, single-threaded), and the selection filters.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support.eval_models import bash_then_finish, crashing_model
from support.fake_sandbox import FakeExecutor

from decode.config.settings import settings
from decode.tools.exec import ExecResult
from evals.harness.benchmark import (
    BenchmarkSelectionError,
    _select_tasks,
    experiment_config,
    make_benchmark_task_fn,
    run_benchmark,
)
from evals.harness.task_loader import load_benchmark_task


def test_task_fn_returns_the_metric_payload(greeting_task_dir: Path, install_fake, install_model):
    """The happy path: one bash call + a final line → the flat payload the landed metrics read."""
    task = load_benchmark_task(greeting_task_dir)
    install_fake(FakeExecutor())  # default verify_result = PASS (exit 0)
    install_model(bash_then_finish("echo hi", "all done"))
    task_fn = make_benchmark_task_fn({task.id: task}, sandbox="docker")

    payload = task_fn({"task_id": task.id})

    assert payload["output"] == "all done"
    assert payload["tool_calls"] == [{"name": "bash", "args": {"command": "echo hi"}}]
    assert payload["steps"] == 2  # the tool leg + the final-text leg
    assert payload["verify"] == {"exit_code": 0, "stdout": "PASS\n"}
    assert payload["max_steps"] == task.max_steps
    assert payload["agent_error"] is None
    assert payload["infra_error"] is None


def test_task_fn_records_a_failing_verify(greeting_task_dir: Path, install_fake, install_model):
    """A non-zero ``verify.sh`` rides the payload as ``verify.exit_code`` so the oracle metric fails it."""
    task = load_benchmark_task(greeting_task_dir)
    install_fake(FakeExecutor(verify_result=ExecResult("FAIL: nope\n", "", 1, timed_out=False)))
    install_model(bash_then_finish("true", "done"))
    task_fn = make_benchmark_task_fn({task.id: task}, sandbox="docker")

    payload = task_fn({"task_id": task.id})

    assert payload["verify"] == {"exit_code": 1, "stdout": "FAIL: nope\n"}


def test_task_fn_surfaces_a_crashed_agent(greeting_task_dir: Path, install_fake, install_model):
    """A crashed agent run grades as fail-with-reason (``agent_error`` set), not silently empty.

    The verify oracle still runs at grade time, so the item is graded rather than aborted — the
    task-103 QA gap closed at the evals layer (ADR-0017 §4).
    """
    task = load_benchmark_task(greeting_task_dir)
    install_fake(FakeExecutor())
    install_model(crashing_model("kaboom"))
    task_fn = make_benchmark_task_fn({task.id: task}, sandbox="docker")

    payload = task_fn({"task_id": task.id})

    assert payload["agent_error"] is not None
    assert "kaboom" in payload["agent_error"]
    assert payload["verify"]["exit_code"] == 0  # verify still graded the Workspace


def test_task_fn_returns_a_graded_payload_when_the_sandbox_never_comes_up(
    greeting_task_dir: Path, install_fake
):
    """A sandbox that never starts (daemon down / bad creds) grades as fail-with-reason, never raises.

    The blocking QA-round-1 bug: Opik's ``evaluate`` runs task fns with no per-item isolation, so with
    ``task_threads=1`` a raised task fn aborts the ENTIRE experiment. ``make_benchmark_task_fn`` must
    catch a sandbox-creation failure into ``infra_error`` and still return a payload the oracle grades
    ``0`` — the analogue of the sandbox-level ``test_teardown_and_mode_restore_run_on_failure`` but at
    ``select_executor``/backend-``start`` raising (task 106).
    """
    from evals.harness.metrics import VerifyOracleMetric

    task = load_benchmark_task(greeting_task_dir)
    fake = FakeExecutor(start_error="docker daemon unreachable")
    install_fake(fake)
    task_fn = make_benchmark_task_fn({task.id: task}, sandbox="docker")
    previous_mode = settings.sandbox_mode

    payload = task_fn({"task_id": task.id})  # must NOT raise

    assert payload["infra_error"] is not None
    assert "docker daemon unreachable" in payload["infra_error"]
    assert payload["agent_error"] is None
    assert payload["verify"] == {"exit_code": None, "stdout": ""}
    # The oracle metric grades the no-verify result 0.0 with a reason (never a crash).
    score = VerifyOracleMetric().score(verify=payload["verify"])
    assert score.value == 0.0
    assert score.reason
    # Teardown ran and the process-global seam / mode were restored despite the failure.
    assert fake.closed
    assert settings.sandbox_mode == previous_mode


def test_run_benchmark_wires_evaluate(mocker, greeting_task_dir: Path):
    """``run_benchmark`` scopes ``evaluate`` to the selected item with the code metrics + judge + config."""
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
    assert "VerifyOracleMetric" in metric_names
    assert "MaxStepsMetric" in metric_names
    assert any("GEval" in name for name in metric_names)  # greeting ships a 'tone' judge
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
                scoring_inputs={},
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


def test_run_benchmark_multi_task_omits_per_task_judges(mocker, greeting_task_dir: Path):
    """A run spanning >1 task uses code metrics only — a per-task judge can't grade another's output."""
    task = load_benchmark_task(greeting_task_dir)
    other = task.model_copy(update={"id": "002-other"})
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
    metric_names = [type(metric).__name__ for metric in kwargs["scoring_metrics"]]
    assert not any("GEval" in name for name in metric_names)
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
