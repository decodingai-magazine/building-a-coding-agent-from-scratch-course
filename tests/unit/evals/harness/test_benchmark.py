"""Offline tests for the Opik Benchmark Job (ADR-0022 §1,§6,§7).

No infra, no subprocess and no keys: :func:`~evals.harness.trial.run_trial` is mocked (the real thing
is pinned end-to-end in ``test_trial.py`` against a fake ``decode``) and ``opik.evaluation.evaluate``
/ ``opik.Opik`` are mocked. Covered here: the trial-to-payload mapping, the task fn's
never-raise contract, where a job's Trial Dirs land, the ``evaluate`` wiring (checksum-scoped dataset
ids, the reward metric, ``experiment_scoring_functions``, ``experiment_config``, threads, experiment
name), and the two Opik-shaped readers — the experiment scoring functions and the summary the table
is rendered from — pinned against the INSTALLED opik 2.2.36 with hand-built ``TestResult`` objects.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from opik.evaluation.evaluation_result import compute_experiment_scores
from opik.evaluation.test_case import TestCase as OpikTestCase
from opik.evaluation.test_result import TestResult as OpikTestResult

from decode.config.settings import settings
from evals.harness.benchmark import (
    EXPERIMENT_SCORING_FUNCTIONS,
    BenchmarkSelectionError,
    _select_tasks,
    agent_model,
    default_job_name,
    default_threads,
    experiment_config,
    make_benchmark_task_fn,
    new_job_dir,
    run_benchmark,
    summarize,
    trial_payload,
    validate_job_name,
)
from evals.harness.datasets import task_checksum
from evals.harness.task_loader import load_benchmark_task
from evals.harness.trial import TrialResult


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
        trial_dir=Path("/tmp/runs/job/001-greeting__0a1b2c3d"),
    )


def _test_result(
    *,
    task_id: str = "001-greeting",
    difficulty: str = "easy",
    status: str = "agent_ok",
    reward: float | None = 1.0,
    cost_usd: float | None = None,
    trial_id: int = 0,
) -> OpikTestResult:
    """One Opik ``TestResult`` shaped exactly as ``evaluate()`` builds it around our task fn."""
    return OpikTestResult(
        test_case=OpikTestCase(
            trace_id=f"trace-{task_id}-{trial_id}",
            dataset_item_id=f"item-{task_id}",
            task_output={"status": status, "reward": reward, "cost_usd": cost_usd},
            dataset_item_content={"task_id": task_id, "difficulty": difficulty},
        ),
        score_results=[],
        trial_id=trial_id,
    )


def _wire_opik(mocker, task, *, items: list[dict] | None = None):
    """Patch ``opik.Opik`` + ``evaluate`` and return ``(evaluate, client)`` for one selected task."""
    evaluate = mocker.patch("opik.evaluation.evaluate")
    opik_cls = mocker.patch("opik.Opik")
    dataset = opik_cls.return_value.get_or_create_dataset.return_value
    dataset.get_items.return_value = (
        items
        if items is not None
        else [{"id": "item-1", "task_id": task.id, "checksum": task_checksum(task)}]
    )
    return evaluate, opik_cls.return_value


# --- the task fn: one dataset item = one Trial ---


def test_task_fn_maps_a_trial_onto_the_metric_payload(greeting_task_dir: Path, mocker):
    """One dataset item = one Trial; the payload is that verdict flattened for the metrics."""
    task = load_benchmark_task(greeting_task_dir)
    run_trial = mocker.patch(
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
    task_fn = make_benchmark_task_fn(
        {task.id: task}, sandbox="docker", job_dir=Path("/tmp/runs/job"), model="qwen-x"
    )

    payload = task_fn({"task_id": task.id})

    assert payload["reward"] == 1.0
    assert payload["status"] == "agent_ok"
    assert payload["output"] == "all done"
    assert payload["steps"] == 3
    assert payload["max_steps"] == task.max_steps
    assert payload["cost_usd"] == 0.01
    assert payload["trial_dir"] == "/tmp/runs/job/001-greeting__0a1b2c3d"
    assert payload["infra_error"] is None
    assert run_trial.call_args.kwargs["sandbox"] == "docker"
    assert run_trial.call_args.kwargs["job_dir"] == Path("/tmp/runs/job")
    assert run_trial.call_args.kwargs["model"] == "qwen-x"
    assert len(run_trial.call_args.kwargs["trial_id"]) == 8  # <task>__<short-id> is the Trial Dir


def test_task_fn_carries_an_infra_error_reason(greeting_task_dir: Path, mocker):
    """An excluded trial must be distinguishable from a lost one without re-deriving the taxonomy."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch(
        "evals.harness.benchmark.run_trial",
        return_value=_trial(
            status="infra_error",
            reward=None,
            reason="the pristine clone of decode/0a1b2c3d failed",
        ),
    )
    task_fn = make_benchmark_task_fn({task.id: task}, sandbox="docker", job_dir=Path("/tmp/j"))

    payload = task_fn({"task_id": task.id})

    assert payload["reward"] is None
    assert payload["infra_error"] == "the pristine clone of decode/0a1b2c3d failed"
    assert payload["output"] == ""
    assert payload["steps"] == 0


def test_task_fn_turns_an_unexpected_raise_into_an_infra_error(greeting_task_dir: Path, mocker):
    """Opik gives task fns no per-item isolation: ONE raise would abort the whole Experiment."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch(
        "evals.harness.benchmark.run_trial", side_effect=RuntimeError("the disk went away")
    )
    task_fn = make_benchmark_task_fn({task.id: task}, sandbox="docker", job_dir=Path("/tmp/j"))

    payload = task_fn({"task_id": task.id})

    assert payload["status"] == "infra_error"
    assert "the disk went away" in payload["infra_error"]
    assert payload["reward"] is None


def test_task_fn_survives_an_item_naming_an_unknown_task(greeting_task_dir: Path, mocker):
    """A stale dataset item (a deleted task) is an Infra Error, not a crashed Experiment."""
    task = load_benchmark_task(greeting_task_dir)
    task_fn = make_benchmark_task_fn({task.id: task}, sandbox="docker", job_dir=Path("/tmp/j"))

    payload = task_fn({"task_id": "010-git-hygiene"})

    assert payload["status"] == "infra_error"
    assert "010-git-hygiene" in payload["infra_error"]


def test_trial_payload_of_a_timed_out_trial_is_an_agent_failure(greeting_task_dir: Path):
    """A timeout is graded like any other loss — never an Infra Error (ADR-0022 §4)."""
    task = load_benchmark_task(greeting_task_dir)

    payload = trial_payload(
        _trial(status="agent_fail", reward=0.0, reason="the agent timed out after 600s"), task
    )

    assert payload["status"] == "agent_fail"
    assert payload["reward"] == 0.0
    assert payload["infra_error"] is None


# --- the job dir + defaults ---


def test_a_job_dir_lands_under_the_harness_home():
    """Trial Dirs are harness artifacts: ``<harness home>/.decode/evals/runs/<job>/`` (ADR-0022 §7)."""
    job_dir = new_job_dir("bench-20260911-101500")

    assert job_dir.parts[-4:] == (
        str(settings.decode_dir),
        "evals",
        "runs",
        "bench-20260911-101500",
    )


@pytest.mark.parametrize("job_name", ["../../evil", "a/b", "", ".", "-lead", "x" * 65])
def test_a_job_name_that_is_not_one_path_segment_is_refused(job_name):
    """``--job-name`` names a directory AND an Opik experiment: one safe segment, or nothing.

    ``new_job_dir("../../evil")`` otherwise resolves OUTSIDE ``.decode/evals/runs/`` and a real
    (billed) job starts writing Trial Dirs onto the host filesystem.
    """
    with pytest.raises(ValueError, match="job name"):
        new_job_dir(job_name)


def test_a_readable_job_name_is_accepted():
    assert new_job_dir("bench-20260911-120000").name == "bench-20260911-120000"
    assert validate_job_name("bench-20260911-120000") == "bench-20260911-120000"


def test_the_default_job_name_passes_its_own_guard():
    """A future prefix/format change must not make the default name trip the validator."""
    name = default_job_name()

    assert validate_job_name(name) == name


def test_the_default_job_name_is_a_utc_timestamp():
    name = default_job_name()

    assert name.startswith("bench-")
    stamp = name.removeprefix("bench-")
    assert len(stamp) == len("20260911-101500") and stamp[8] == "-"


def test_default_threads_are_one_for_docker_and_four_for_modal():
    """Docker trials share the host's CPU for the image warm-up; modal trials are remote."""
    assert default_threads("docker") == 1
    assert default_threads("modal") == 4


# --- run_benchmark: the evaluate() wiring ---


def test_run_benchmark_wires_evaluate(mocker, greeting_task_dir: Path):
    """The whole Opik surface in one assertion block (ADR-0022 §6)."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate, _ = _wire_opik(mocker, task)

    run = run_benchmark(task_id=task.id, sandbox="docker", trials=2, job_name="bench-x")

    assert run.result is evaluate.return_value
    assert run.job_dir.name == "bench-x"
    _, kwargs = evaluate.call_args
    assert kwargs["dataset_item_ids"] == ["item-1"]
    assert kwargs["trial_count"] == 2
    assert kwargs["task_threads"] == 1
    assert kwargs["experiment_name"] == "bench-x"
    assert [type(metric).__name__ for metric in kwargs["scoring_metrics"]] == ["RewardMetric"]
    assert kwargs["experiment_scoring_functions"] == EXPERIMENT_SCORING_FUNCTIONS


def test_run_benchmark_experiment_config_carries_the_full_provenance(
    mocker, greeting_task_dir: Path
):
    """Two Experiment rows must be tellable apart by what actually changed (ADR-0022 §6)."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate, _ = _wire_opik(mocker, task)

    run_benchmark(task_id=task.id, sandbox="modal", trials=3, job_name="bench-y", model="qwen-x")

    config = evaluate.call_args.kwargs["experiment_config"]
    assert config["model"] == "qwen-x"
    assert config["provider"] == settings.llm_provider
    assert config["git_sha"]  # this repo is a git checkout
    assert config["sandbox"] == "modal"
    assert config["decode_version"]
    assert config["trials"] == 3
    assert config["job_name"] == "bench-y"


def test_experiment_config_kitaru_agent_id_is_none_when_unset(mocker):
    """Never a placeholder: an unset ``KITARU_AGENT_ID`` means the run recorded no Session."""
    mocker.patch.object(settings, "kitaru_agent_id", "")

    config = experiment_config(sandbox="docker", trials=1, job_name="bench-x", model=None)

    assert config["kitaru_agent_id"] is None


def test_experiment_config_reports_a_set_kitaru_agent_id(mocker):
    """With recording configured the id joins Opik to the Kitaru Sessions (ADR-0022 §10)."""
    mocker.patch.object(settings, "kitaru_agent_id", "agent-42")

    config = experiment_config(sandbox="docker", trials=1, job_name="bench-x", model=None)

    assert config["kitaru_agent_id"] == "agent-42"


def test_run_benchmark_leaves_the_project_to_the_dataset(mocker, greeting_task_dir: Path):
    """opik 2.2.36 resolves the trace project from the DATASET and deprecates the parameter.

    ``sync_benchmark_dataset`` creates ``decode-benchmark-v2`` inside ``decode-evals``, so passing it
    again would only log a deprecation warning on every run.
    """
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate, client = _wire_opik(mocker, task)
    client.get_or_create_dataset.return_value.project_name = settings.eval_project_name

    run_benchmark(task_id=task.id, sandbox="docker")

    assert evaluate.call_args.kwargs["project_name"] is None


def test_run_benchmark_names_the_project_for_a_dataset_without_one(mocker, greeting_task_dir: Path):
    """The fallback: a dataset created before the project was pinned still logs under decode-evals."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate, client = _wire_opik(mocker, task)
    client.get_or_create_dataset.return_value.project_name = None

    run_benchmark(task_id=task.id, sandbox="docker")

    assert evaluate.call_args.kwargs["project_name"] == settings.eval_project_name


def test_run_benchmark_threads_default_to_the_sandbox_policy(mocker, greeting_task_dir: Path):
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate, _ = _wire_opik(mocker, task)

    run_benchmark(task_id=task.id, sandbox="modal")

    assert evaluate.call_args.kwargs["task_threads"] == 4


def test_run_benchmark_threads_can_be_overridden(mocker, greeting_task_dir: Path):
    """Subprocess trials are independent, so an operator may widen the fan-out (ADR-0022 §6)."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate, _ = _wire_opik(mocker, task)

    run_benchmark(task_id=task.id, sandbox="docker", threads=3)

    assert evaluate.call_args.kwargs["task_threads"] == 3


def test_run_benchmark_selects_the_item_matching_the_task_folder_on_disk(
    mocker, greeting_task_dir: Path
):
    """Opik never deletes a superseded item — the checksum is what keeps a stale one out of the run."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate, _ = _wire_opik(
        mocker,
        task,
        items=[
            {"id": "stale", "task_id": task.id, "checksum": "0" * 64},
            {"id": "fresh", "task_id": task.id, "checksum": task_checksum(task)},
        ],
    )

    run_benchmark(task_id=task.id, sandbox="docker")

    assert evaluate.call_args.kwargs["dataset_item_ids"] == ["fresh"]


def test_run_benchmark_stops_when_a_task_has_no_fresh_dataset_item(mocker, greeting_task_dir: Path):
    """A stale-only match must STOP: ``dataset_item_ids=None`` would run the whole dataset.

    The mirror of the empty-selection guard — never a silent 19-task paid run because a checksum
    moved and the sync did not land.
    """
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate, _ = _wire_opik(
        mocker, task, items=[{"id": "stale", "task_id": task.id, "checksum": "0" * 64}]
    )

    with pytest.raises(BenchmarkSelectionError, match=task.id):
        run_benchmark(task_id=task.id, sandbox="docker")

    evaluate.assert_not_called()


def test_run_benchmark_scopes_evaluate_to_every_selected_item(mocker, greeting_task_dir: Path):
    """An unfiltered run evaluates every task's fresh dataset item, not just the first."""
    task = load_benchmark_task(greeting_task_dir)
    other = task.model_copy(update={"task": task.task.model_copy(update={"name": "002-other"})})
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task, other])
    evaluate, _ = _wire_opik(
        mocker,
        task,
        items=[
            {"id": "a", "task_id": task.id, "checksum": task_checksum(task)},
            {"id": "b", "task_id": other.id, "checksum": task_checksum(other)},
        ],
    )

    run_benchmark(sandbox="docker")

    assert set(evaluate.call_args.kwargs["dataset_item_ids"]) == {"a", "b"}


@pytest.mark.parametrize("trials", [0, -5])
def test_run_benchmark_rejects_a_non_positive_trial_count(mocker, greeting_task_dir: Path, trials):
    """Opik's ``evaluate(trial_count=-5)`` loops zero times and returns cleanly — guard first."""
    task = load_benchmark_task(greeting_task_dir)
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[task])
    evaluate = mocker.patch("opik.evaluation.evaluate")

    with pytest.raises(ValueError, match="trials"):
        run_benchmark(task_id=task.id, sandbox="docker", trials=trials)

    evaluate.assert_not_called()


def test_run_benchmark_raises_when_no_task_matches(mocker):
    """An empty selection is a loud, friendly stop — never a silent zero-item experiment."""
    mocker.patch("evals.harness.benchmark.load_benchmark_tasks", return_value=[])

    with pytest.raises(BenchmarkSelectionError):
        run_benchmark(task_id="does-not-exist")


def test_select_tasks_filters_by_id_and_difficulty(greeting_task_dir: Path):
    task = load_benchmark_task(greeting_task_dir)

    assert _select_tasks([task], task_id=task.id, difficulty=None) == [task]
    assert _select_tasks([task], task_id="nope", difficulty=None) == []
    assert _select_tasks([task], task_id=None, difficulty="easy") == [task]
    assert _select_tasks([task], task_id=None, difficulty="hard") == []


def test_agent_model_prefers_the_run_override():
    assert agent_model("qwen-x") == "qwen-x"
    assert agent_model() == settings.active_model


# --- the experiment scoring functions, pinned against the installed opik ---


def test_experiment_scoring_functions_match_the_installed_opik_signature():
    """Opik 2.2.36 calls each function with ``List[TestResult]`` and logs the ``ScoreResult``s back.

    Driven through opik's OWN ``compute_experiment_scores``, which swallows a raising function with a
    warning — so a wrong signature would silently produce NO scores. Asserting the names is what
    turns that silence into a failing test.
    """
    results = [
        _test_result(status="agent_ok", reward=1.0, cost_usd=0.10, trial_id=0),
        _test_result(status="agent_fail", reward=0.0, cost_usd=0.10, trial_id=1),
    ]

    scores = compute_experiment_scores(EXPERIMENT_SCORING_FUNCTIONS, results)

    by_name = {score.name: score for score in scores}
    assert set(by_name) == {
        "pass_at_1",
        "pass_at_k",
        "pass_hat_k",
        "flaky_rate",
        "success_per_dollar",
        "mean_cost_usd",
        "infra_error_rate",
    }
    assert by_name["pass_at_1"].value == 0.5
    assert by_name["pass_at_k"].value == 1.0
    assert by_name["pass_hat_k"].value == 0.0
    assert by_name["flaky_rate"].value == 1.0
    assert by_name["mean_cost_usd"].value == 0.10
    assert by_name["success_per_dollar"].value == 5.0
    assert by_name["infra_error_rate"].value == 0.0


def test_experiment_scores_exclude_infra_errors_from_the_pass_rates():
    """The ADR-0022 §4 rule on the Experiment row: the lost trial is counted, never scored."""
    results = [
        _test_result(status="agent_ok", reward=1.0, trial_id=0),
        _test_result(status="infra_error", reward=None, trial_id=1),
    ]

    by_name = {
        score.name: score
        for score in compute_experiment_scores(EXPERIMENT_SCORING_FUNCTIONS, results)
    }

    assert by_name["pass_at_1"].value == 1.0  # the one graded trial passed
    assert by_name["infra_error_rate"].value == 0.5


def test_unpriced_cost_scores_are_failed_scores_not_zeroes():
    """No provider cost ⇒ ``scoring_failed`` so Opik's average skips it; a 0 would read as free."""
    results = [_test_result(status="agent_ok", reward=1.0, cost_usd=None)]

    by_name = {
        score.name: score
        for score in compute_experiment_scores(EXPERIMENT_SCORING_FUNCTIONS, results)
    }

    assert by_name["mean_cost_usd"].scoring_failed is True
    assert by_name["success_per_dollar"].scoring_failed is True
    assert by_name["pass_at_1"].scoring_failed is False


# --- the summary the CLI table is rendered from ---


def test_summarize_groups_trials_by_task_and_tier(mocker):
    """The table's rows come off the same ``TestResult`` list the experiment scores do."""
    result = mocker.Mock()
    result.test_results = [
        _test_result(task_id="001-a", difficulty="easy", status="agent_ok", trial_id=0),
        _test_result(
            task_id="001-a", difficulty="easy", status="agent_fail", reward=0.0, trial_id=1
        ),
        _test_result(
            task_id="002-b", difficulty="hard", status="infra_error", reward=None, trial_id=0
        ),
    ]

    summary = summarize(result, trials=2)

    assert [row.task_id for row in summary.per_task] == ["001-a", "002-b"]
    assert summary.per_task[0].pass_at_1 == 0.5
    assert summary.per_task[0].is_flaky is True
    assert summary.per_task[1].graded == 0
    assert [rollup.label for rollup in summary.per_difficulty] == ["easy", "hard"]
    assert summary.total.infra_errors == 1


def test_summarize_is_graceful_on_a_result_without_test_results(mocker):
    """The CLI renders the table over whatever ``evaluate`` returned — a mock must not crash it."""
    summary = summarize(mocker.Mock(), trials=1)

    assert summary.per_task == []
    assert summary.total.tasks == 0
