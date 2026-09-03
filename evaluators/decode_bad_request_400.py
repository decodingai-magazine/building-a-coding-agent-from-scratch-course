"""Kitaru evaluator ``decode-bad-request-400`` — accepted behavior from investigation
``decode-provider-failures-1`` (cohort ``decode-bad-request-400@1``).

FAIL (behavior present) when the session's terminal error is a
``pydantic_ai.exceptions.ModelHTTPError`` with ``status_code: 400`` — decode sent a malformed
model request ("wrong input") — AND the session produced no assistant-facing output.

PASS when the session produced output, has no error, or failed for a reviewed-acceptable reason
(transport/outage errors such as 503, ConnectError, RemoteProtocolError; user aborts).

Missing evidence (no error and no output and non-terminal status) stays unresolved: ``passed`` is
``None``, never a silent Fail.
"""

from typing import Any

from kitaru.task.evaluator import EvaluationResult, SessionView

_RESULT_NAME = "bad_request_crash"
_MODEL_HTTP_ERROR = "ModelHTTPError"
_BAD_REQUEST_MARKER = "status_code: 400"


def _has_output(session: SessionView) -> bool:
    outputs = session.session.outputs
    if outputs is None:
        return False
    if isinstance(outputs, (str, list, dict)):
        return bool(outputs)
    return True


def evaluate(session: SessionView, **params: Any) -> EvaluationResult | list[EvaluationResult]:
    """Flag sessions ending in a malformed-request HTTP 400 crash with no user-facing output."""
    del params
    record = session.session
    error = record.error or ""
    status = str(record.status or "")

    if not error and not _has_output(session) and status == "in_progress":
        return EvaluationResult(
            name=_RESULT_NAME,
            passed=None,
            value="unresolved",
            explanation="Session is non-terminal with no error and no output; evidence missing.",
        )

    detected = (
        _MODEL_HTTP_ERROR in error and _BAD_REQUEST_MARKER in error and not _has_output(session)
    )
    if detected:
        explanation = (
            "Terminal error is ModelHTTPError with status_code: 400 (malformed model request) "
            "and the session has no assistant-facing output."
        )
    else:
        explanation = (
            "No 400 bad-request crash: session produced output, has no error, or failed for a "
            "reviewed-acceptable reason (transport/outage/abort)."
        )
    return EvaluationResult(
        name=_RESULT_NAME,
        score=detected,
        passed=not detected,
        explanation=explanation,
    )
