"""Case 23 (MINED, case zero) — the ``ModelHTTPError 400`` that would not reproduce (ADR-0022 §8).

The known failure the mining session set out to capture: a run whose terminal error is
``pydantic_ai.exceptions.ModelHTTPError`` with ``status_code: 400`` — decode sent the provider a
request it refused — AND which produced no assistant-facing output. It is guarded remotely today by
the Kitaru evaluator ``evaluators/decode_bad_request_400.py`` over cohort
``decode-bad-request-400@1``. This module is the attempt to pull it onto the offline gate, and the
attempt is the deliverable: it ships DECLARED but skipped, carrying the fixture that was actually
tried so that unskipping it re-runs the experiment rather than re-inventing it.

**Why it ships skipped** (evidence gathered 2026-09-11, all of it in this task's log):

* The live window holds no 400. ``python -m evals mine --preset errors --limit 200`` returns two
  ``ModelHTTPError`` traces and both are **503** outages — ``019f60fd-7a5c-7374-8a49-202dcecf2951``
  (``status_code: 503, model_name: gemini-2.5-flash``) and
  ``01a08d78-c569-7bab-91e1-2e1ca5d4c02b`` (``status_code: 503, model_name: Qwen/…``). The evaluator
  itself calls a 503 a reviewed-acceptable transport failure, not this bug.
* The original session lives in Kitaru, and the managed workspace answers ``HTTP 404`` today
  (``kitaru status``), so the request body that earned the 400 cannot be read back. Task 165
  bootstraps a local server and re-imports the cohort; that is when this case gets its real fixture.
* Three fixture reproductions were run for real against the live ``gemini`` route — a resumed history
  holding an assistant turn with EMPTY parts (the shape trace
  ``019f5cd5-7cfd-7498-b10e-3d62f6d776af`` recorded, and the most plausible way a persisted session
  re-sends something a provider refuses), one holding an empty TEXT part, and one ending in an orphan
  tool call with no result. None produced a 400: all three answered normally. The orphan-tool-call
  variant is actively repaired first — ``decode.agent.loop`` logs
  ``healing 1 unprocessed tool call(s) left by a crashed or aborted turn`` and synthesizes the missing
  return (``AgentTurnHandler._heal_dangling_tool_calls``), which is decode already defending the most
  likely malformed-history path.

So the declared fixture below is the empty-parts variant, and :data:`SKIP_REASON` says what it cost to
learn that it is not enough. The metric stays the one a live reproduction would grade —
:class:`~evals.harness.metrics.AnsweredWithoutErrorMetric`, whose rule (terminal error AND no answer)
is the offline twin of the Kitaru evaluator's.

``source_trace_id`` / ``thread_id`` are ``None`` on purpose: the failure's provenance is the Kitaru
cohort ``decode-bad-request-400@1`` (session ``01a02529-ebf7-7133-869f-ea3f4a7bc493``), not an Opik
trace, and pointing the fields at one of the 503 traces would file this case under a failure it is
not.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart

from evals.harness.metrics import AnsweredWithoutErrorMetric
from evals.regression.case import RegressionCase

SKIP_REASON = (
    "no 400 to reproduce: the live project's two ModelHTTPError traces are both 503 outages, the "
    "Kitaru workspace holding cohort decode-bad-request-400@1 answers HTTP 404, and three "
    "malformed-history fixtures (empty parts / empty text part / orphan tool call) all answered "
    "normally against the live gemini route on 2026-09-11 — the orphan tool call is healed by "
    "AgentTurnHandler._heal_dangling_tool_calls. Unskip when task 165 re-imports the cohort and the "
    "offending request body can be read back."
)

_NOTE = "note.txt"
_NOTE_BODY = "the answer is 42\n"


def _fixture(workspace: Path) -> None:
    """Seed the one file the resumed conversation is about."""
    (workspace / _NOTE).write_text(_NOTE_BODY, encoding="utf-8")


def empty_assistant_turn_history() -> list[ModelMessage]:
    """A resumed conversation whose assistant turn has NO parts — the shape a 400 was suspected of.

    Mirrors what the live trace recorded (``{"role": "assistant", "parts": [], "finish_reason":
    "stop"}``) and what a session log would persist after such a turn, so the next prompt re-sends it.
    """
    return [
        ModelRequest(parts=[UserPromptPart(content=f"What does {_NOTE} say?")]),
        ModelResponse(parts=[]),
    ]


CASE = RegressionCase(
    id="23-bad-request-400",
    prompt=f"Now read {_NOTE} and tell me exactly what it says.",
    fixture=_fixture,
    difficulty="hard",
    symptom=(
        "a run ended in ModelHTTPError 400 — the provider refused the request decode sent — with no "
        "assistant-facing output at all."
    ),
    assertion=(
        "The response answers the question about the seeded file, rather than ending with no answer "
        "at all."
    ),
    metrics=[AnsweredWithoutErrorMetric()],
    message_history=empty_assistant_turn_history,
    max_requests=6,
    tags=["mined", "provider-error", "case-zero"],
    skip_reason=SKIP_REASON,
    fixed_in="unfixed",
)
