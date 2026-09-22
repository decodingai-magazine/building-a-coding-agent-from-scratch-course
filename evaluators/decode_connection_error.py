"""Kitaru evaluator ``decode-connection-error`` — behavior from investigation ``my-discovery-1``
(cohort ``decode-connection-error@1``).

FAIL (behavior present) when the session ended because the model request never connected:
``Connection error.`` (``pydantic_ai.exceptions.ModelAPIError`` over ``httpx.ConnectError``), i.e.
the provider endpoint was unreachable.

PASS when the session has no error, or failed for any other reason (an HTTP status from a
reachable provider, the request limit, an abort).

Missing evidence (no error and non-terminal status) stays unresolved: ``passed`` is ``None``,
never a silent Fail.
"""

from typing import Any

from kitaru.task.evaluator import EvaluationResult, SessionView

_RESULT_NAME = "connection_error_crash"
# Recorded sessions keep only the message; imported ones keep the qualified exception name too.
_MARKERS = ("Connection error", "ConnectError")


def evaluate(session: SessionView, **params: Any) -> EvaluationResult | list[EvaluationResult]:
    """Flag sessions whose model request never reached the provider."""
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
        "Terminal error is a connection failure: the provider endpoint was unreachable."
        if detected
        else "No connection failure: the session has no error or failed for another reason."
    )
    return EvaluationResult(
        name=_RESULT_NAME, score=detected, passed=not detected, explanation=explanation
    )
