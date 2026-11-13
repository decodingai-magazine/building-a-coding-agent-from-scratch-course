"""Offline tests for the Langfuse-backed natural-language regression suite."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from decode.config.settings import settings
from evals.harness.datasets import REGRESSION_SUITE_NAME, RegressionSurfaces
from evals.harness.metrics import ScoreResult
from evals.harness.test_suite import (
    SUITE_PASS_BAR,
    SuitePassRateError,
    SuiteSelectionError,
    _suite_judge,
    assert_pass_rate,
    make_suite_task_fn,
    run_test_suite,
)
from evals.regression.case import RegressionCase


def _case(case_id: str, prompt: str = "do the thing", **overrides: object) -> RegressionCase:
    fields: dict[str, object] = {
        "id": case_id,
        "prompt": prompt,
        "fixture": lambda _w: None,
        "metrics": [object()],
        "difficulty": "easy",
        "description": "Tests that it behaves.",
        "symptom": "harness invariant: it behaves.",
        "assertion": "The response behaves.",
    }
    fields.update(overrides)
    return RegressionCase(**fields)  # type: ignore[arg-type]


def _experiment_result(*, passed: bool = True):
    evaluation = SimpleNamespace(
        name="assertion_pass",
        value=1.0 if passed else 0.0,
        comment="checked",
        metadata={},
    )
    item = SimpleNamespace(
        input={"case_id": "a", "prompt": "do the thing"},
        metadata={"case_id": "a"},
    )
    item_result = SimpleNamespace(
        item=item,
        output={"input": {"prompt": "do the thing"}, "output": "done"},
        evaluations=[evaluation],
    )
    return SimpleNamespace(
        name=REGRESSION_SUITE_NAME,
        experiment_id="experiment-1",
        item_results=[item_result],
    )


def test_suite_task_fn_keeps_only_the_prompt_and_answer(mocker):
    case = _case("17-grounded-answer", prompt="what is the Quibbler responsible for?")
    fake_regression = mocker.Mock(
        return_value={
            "output": "The Quibbler deduplicates webhooks.",
            "file_state": {"README.md": "# Sample"},
            "agent_error": None,
            "session_id": "session-1",
        }
    )
    mocker.patch("evals.harness.test_suite.make_regression_task_fn", return_value=fake_regression)
    session_ids: dict[str, str] = {}

    task = make_suite_task_fn({case.id: case}, session_ids=session_ids)
    result = task({"input": {"case_id": case.id}})

    fake_regression.assert_called_once_with({"case_id": case.id})
    assert result == {
        "input": {"prompt": case.prompt},
        "output": "The Quibbler deduplicates webhooks.",
    }
    assert session_ids == {case.id: "session-1"}
    assert "file_state" not in result["input"]


def test_suite_judge_passes_prompt_and_answer_to_local_judge(mocker):
    judge = mocker.Mock()
    judge.score.return_value = ScoreResult(name="llm_judge", value=0.8, reason="grounded")
    make_judge = mocker.patch("evals.harness.test_suite.make_judge", return_value=judge)

    evaluation = _suite_judge(
        input={"case_id": "a", "prompt": "do the thing"},
        output={"input": {"prompt": "do the thing"}, "output": "done"},
        expected_output={"assertions": ["The answer is grounded."]},
        metadata={"case_id": "a"},
    )

    assert make_judge.call_args.args[1] == "- The answer is grounded."
    judge.score.assert_called_once_with(
        input={"case_id": "a", "prompt": "do the thing"}, output="done"
    )
    assert evaluation.name == "assertion_pass"
    assert evaluation.value == 1.0
    assert evaluation.comment == "grounded"


def test_run_test_suite_uses_langfuse_experiments_serially(mocker):
    cases = [_case("a"), _case("b")]
    mocker.patch("evals.harness.test_suite.load_cases", return_value=cases)
    sync = mocker.patch(
        "evals.harness.test_suite.sync_regression_cases",
        return_value=RegressionSurfaces(dataset=object(), suite=object(), suite_version="v1"),
    )
    client = mocker.Mock()
    client.run_experiment.return_value = _experiment_result()

    run = run_test_suite(client=client)

    assert run.result.pass_rate == 1.0
    assert run.suite_version == "v1"
    assert [case.id for case in sync.call_args.args[0]] == ["a", "b"]
    kwargs = client.run_experiment.call_args.kwargs
    assert kwargs["name"] == REGRESSION_SUITE_NAME
    assert kwargs["run_name"] == "v1"
    assert len(kwargs["data"]) == 2
    assert kwargs["data"][0]["input"]["case_id"] == "a"
    assert kwargs["max_concurrency"] == 1
    assert kwargs["evaluators"]
    assert Path(run.report_path).parent == Path(settings.decode_dir) / "evals" / "suite-reports"


def test_suite_pass_rate_counts_failed_judgments(mocker):
    mocker.patch("evals.harness.test_suite.load_cases", return_value=[_case("a")])
    mocker.patch(
        "evals.harness.test_suite.sync_regression_cases",
        return_value=RegressionSurfaces(dataset=object(), suite=object(), suite_version="v1"),
    )
    client = mocker.Mock()
    client.run_experiment.return_value = _experiment_result(passed=False)

    run = run_test_suite(client=client)

    assert run.result.pass_rate == 0.0


def test_a_filtered_run_syncs_only_selected_cases(mocker):
    cases = [_case("a"), _case("b", difficulty="hard")]
    mocker.patch("evals.harness.test_suite.load_cases", return_value=cases)
    sync = mocker.patch(
        "evals.harness.test_suite.sync_regression_cases",
        return_value=RegressionSurfaces(dataset=object(), suite=object(), suite_version="v1"),
    )
    client = mocker.Mock()
    client.run_experiment.return_value = _experiment_result()

    run_test_suite(difficulty="hard", client=client)

    assert [case.id for case in sync.call_args.args[0]] == ["b"]
    assert len(client.run_experiment.call_args.kwargs["data"]) == 1


def test_skip_guarded_cases_are_not_registered_or_run(mocker):
    cases = [_case("a"), _case("skipped", skip_reason="MCP has not shipped")]
    mocker.patch("evals.harness.test_suite.load_cases", return_value=cases)
    sync = mocker.patch(
        "evals.harness.test_suite.sync_regression_cases",
        return_value=RegressionSurfaces(dataset=object(), suite=object(), suite_version="v1"),
    )
    client = mocker.Mock()
    client.run_experiment.return_value = _experiment_result()

    run_test_suite(client=client)

    assert [case.id for case in sync.call_args.args[0]] == ["a"]
    assert len(client.run_experiment.call_args.kwargs["data"]) == 1


def test_run_test_suite_raises_when_the_filter_matches_nothing(mocker):
    mocker.patch("evals.harness.test_suite.load_cases", return_value=[_case("a")])

    with pytest.raises(SuiteSelectionError):
        run_test_suite(case_id="nope", client=mocker.Mock())


def test_assert_pass_rate_passes_at_and_above_the_bar():
    assert_pass_rate(SUITE_PASS_BAR)
    assert_pass_rate(1.0)


def test_assert_pass_rate_raises_below_the_bar():
    with pytest.raises(SuitePassRateError, match="below"):
        assert_pass_rate(SUITE_PASS_BAR - 0.01)
