"""Kitaru evaluator ``decode-request-limit`` — behavior from investigation ``my-discovery-1``
(cohort ``decode-request-limit@1``).

FAIL (behavior present) when the session ended on pydantic-ai's request limit: decode stopped
mid-task with ``UsageLimitExceeded`` ("The next request would exceed the request_limit of N"),
so the task was never finished.

PASS when the session has no error, or failed for any other reason.

Missing evidence (no error and non-terminal status) stays unresolved: ``passed`` is ``None``,
never a silent Fail.
"""

from typing import Any

from kitaru.task.evaluator import EvaluationResult, SessionView

_RESULT_NAME = "request_limit_cutoff"
# Recorded sessions keep only the message; imported ones keep the qualified exception name too.
_MARKERS = ("UsageLimitExceeded", "exceed the request_limit")


def evaluate(session: SessionView, **params: Any) -> EvaluationResult | list[EvaluationResult]:
    """Flag sessions cut off by the request limit before finishing the task."""
    del params
    record = session.session
    error = record.error or ""

    if not error and str(record.status or "") == "in_progress":
        return EvaluationResult(
            name=_RESULT_NAME,
            passed=None,
            value="unresolved",
            explanation="Session is non-terminal with no error; evidence missing.",
        )

    detected = any(marker in error for marker in _MARKERS)
    explanation = (
        "Terminal error is the request limit (UsageLimitExceeded): the task was cut off."
        if detected
        else "No request-limit cutoff: the session has no error or failed for another reason."
    )
    return EvaluationResult(
        name=_RESULT_NAME, score=detected, passed=not detected, explanation=explanation
    )
