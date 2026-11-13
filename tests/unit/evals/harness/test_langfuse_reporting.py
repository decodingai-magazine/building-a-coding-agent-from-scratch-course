from __future__ import annotations

from types import SimpleNamespace

from evals.harness.langfuse_reporting import (
    report_benchmark_scores,
    report_regression_scores,
    report_suite_scores,
)


def test_benchmark_scores_are_mapped_to_the_trial_session(mocker):
    report = mocker.patch("evals.harness.langfuse_reporting.report_eval_scores")
    result = SimpleNamespace(
        test_results=[
            SimpleNamespace(
                test_case=SimpleNamespace(
                    task_output={
                        "task_id": "task-1",
                        "trial_id": "trial-1",
                        "session_id": "session-1",
                        "reward": 0.0,
                    }
                )
            ),
            SimpleNamespace(
                test_case=SimpleNamespace(task_output={"session_id": None, "reward": 1.0})
            ),
        ]
    )

    assert report_benchmark_scores(result) is report.return_value

    (scores,) = report.call_args.args
    assert len(scores) == 1
    assert scores[0].session_id == "session-1"
    assert scores[0].name == "benchmark_reward"
    assert scores[0].value == 0.0
    assert scores[0].metadata == {"task_id": "task-1", "trial_id": "trial-1"}


def test_regression_scores_skip_failed_metrics(mocker):
    report = mocker.patch("evals.harness.langfuse_reporting.report_eval_scores")
    result = SimpleNamespace(
        test_results=[
            SimpleNamespace(
                test_case=SimpleNamespace(
                    task_output={"case_id": "case-1", "session_id": "session-1"}
                ),
                score_results=[
                    SimpleNamespace(name="tool_discipline", value=0.8, scoring_failed=False),
                    SimpleNamespace(name="broken_metric", value=0.0, scoring_failed=True),
                ],
            )
        ]
    )

    report_regression_scores(result)

    (scores,) = report.call_args.args
    assert len(scores) == 1
    assert scores[0].session_id == "session-1"
    assert scores[0].name == "regression_tool_discipline"
    assert scores[0].value == 0.8
    assert scores[0].metadata == {"case_id": "case-1"}


def test_suite_score_records_run_pass_rate_per_case_session(mocker):
    report = mocker.patch("evals.harness.langfuse_reporting.report_eval_scores")

    report_suite_scores(
        {"case-1": "session-1", "case-2": "session-2"},
        pass_rate=0.75,
        suite_version="v3",
    )

    (scores,) = report.call_args.args
    assert [score.session_id for score in scores] == ["session-1", "session-2"]
    assert all(score.name == "suite_pass_rate" and score.value == 0.75 for score in scores)
    assert [score.metadata for score in scores] == [
        {"case_id": "case-1", "suite_version": "v3"},
        {"case_id": "case-2", "suite_version": "v3"},
    ]
