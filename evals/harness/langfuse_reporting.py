"""Report Langfuse experiment scores back onto the resulting Langfuse sessions."""

from __future__ import annotations

from typing import Any

from decode.observability.eval_scores import EvalScore, report_eval_scores


def report_benchmark_scores(result: Any) -> int | None:
    """Report one reward score for each benchmark trial with a Langfuse session."""
    scores: list[EvalScore] = []
    for test_result in _test_results(result):
        test_case = getattr(test_result, "test_case", None)
        output = getattr(test_case, "task_output", None)
        if not isinstance(output, dict):
            continue
        session_id = _session_id(output)
        value = _number(output.get("reward"))
        if session_id is None or value is None:
            continue
        scores.append(
            EvalScore(
                session_id=session_id,
                name="benchmark_reward",
                value=value,
                comment=f"Benchmark trial for task {output.get('task_id', 'unknown')}.",
                metadata={
                    "task_id": str(output.get("task_id", "")),
                    "trial_id": str(output.get("trial_id", "")),
                },
            )
        )
    return report_eval_scores(scores)


def report_regression_scores(result: Any) -> int | None:
    """Report every numeric per-case metric score to its agent-run session."""
    scores: list[EvalScore] = []
    for test_result in _test_results(result):
        test_case = getattr(test_result, "test_case", None)
        output = getattr(test_case, "task_output", None)
        if not isinstance(output, dict):
            continue
        session_id = _session_id(output)
        if session_id is None:
            continue
        for score in getattr(test_result, "score_results", []) or []:
            name = getattr(score, "name", None)
            value = _number(getattr(score, "value", None))
            if not isinstance(name, str) or value is None:
                continue
            if getattr(score, "scoring_failed", False):
                continue
            scores.append(
                EvalScore(
                    session_id=session_id,
                    name=f"regression_{name}",
                    value=value,
                    comment="Deterministic regression metric.",
                    metadata={"case_id": str(output.get("case_id", ""))},
                )
            )
    return report_eval_scores(scores)


def report_suite_scores(
    session_ids: dict[str, str], *, pass_rate: float, suite_version: str | None
) -> int | None:
    """Attach the suite's aggregate pass rate to each case session run by that suite."""
    value = _number(pass_rate)
    if value is None:
        return report_eval_scores([])
    return report_eval_scores(
        [
            EvalScore(
                session_id=session_id,
                name="suite_pass_rate",
                value=value,
                comment=f"Aggregate suite pass rate ({suite_version or 'unversioned'}).",
                metadata={"case_id": case_id, "suite_version": suite_version or ""},
            )
            for case_id, session_id in session_ids.items()
        ]
    )


def _test_results(result: Any) -> list[Any]:
    values = getattr(result, "test_results", None)
    return values if isinstance(values, list) else []


def _session_id(output: dict[str, Any]) -> str | None:
    value = output.get("session_id")
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
