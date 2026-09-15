"""Case 21 (MINED) — a run ends with an answer, not with three empty model responses (ADR-0022 §8).

Mined from the LIVE ``decode-prod`` project on 2026-09-11 (``evals/regression/mining/``): signature
``errors | pydantic_ai.exceptions.UnexpectedModelBehavior | - | -``, trace
``019f5cd5-7cfd-7498-b10e-3d62f6d776af`` (thread ``c72c45fb-a22a-4ed7-b5ac-06c80daf2e81``, tagged
``regression-case`` in Opik so the online view links back here).

**What the trace shows.** The user asked for one shell command and its output. Four ``chat
gemini-2.5-flash`` spans fired back to back, each returning
``{"role": "assistant", "parts": [], "finish_reason": "stop"}`` — an assistant turn with NOTHING in
it. pydantic-ai retries an unusable response three times and then raises
``UnexpectedModelBehavior: Exceeded maximum output retries (3)``, so the run ended with a terminal
error, zero tool calls, and no answer at all. That pair — a terminal error AND no assistant-facing
output — is the same shape the Kitaru evaluator ``decode-bad-request-400`` flags remotely.

**The case.** The trace's prompt verbatim, over a minimal Workspace (the original ran against a
cloned repo in a docker sandbox; the graded behavior needs only a cwd whose listing is not empty).
ONE deterministic metric — :class:`~evals.harness.metrics.AnsweredWithoutErrorMetric`, the
``agent_error``-absent grader — because the regression IS "the user got nothing": whether the answer
also quotes the command's output is the ``assertion``'s job on the Test Suite surface, not a second
number here.

``fixed_in`` is ``"unfixed"``: nothing on this branch changes how decode handles a model that returns
empty turns (pydantic-ai's retry ceiling still ends the run), so the case stands as the guard that
notices if it comes back on this prompt.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import AnsweredWithoutErrorMetric
from evals.regression.case import RegressionCase

# The command the production prompt asked for, quoted here exactly as the trace recorded it.
COMMAND = "uname -m; pwd; echo SANDBOX_OK"

_NOTE = "note.txt"
_NOTE_BODY = "A workspace with one file in it, so `pwd` and a listing have something to show.\n"


def _fixture(workspace: Path) -> None:
    """Seed one small file — the minimum that makes the trace's Workspace real, nothing more."""
    (workspace / _NOTE).write_text(_NOTE_BODY, encoding="utf-8")


CASE = RegressionCase(
    id="21-empty-model-response",
    prompt=f"Use the bash tool to run {COMMAND!r}. Report exactly what it printed.",
    fixture=_fixture,
    difficulty="easy",
    description=(
        "Tests that a bash-and-report turn ends with an answer instead of dying on the retry ceiling "
        "after empty model responses."
    ),
    symptom=(
        "three model turns in a row came back with no parts at all, so the run died on the retry "
        "ceiling (UnexpectedModelBehavior) with no tool call and no answer for the user."
    ),
    assertion=(
        "The response reports what the shell command printed, rather than ending with no answer at "
        "all."
    ),
    metrics=[AnsweredWithoutErrorMetric()],
    max_requests=6,
    tags=["mined", "empty-response", "bash"],
    source_trace_id="019f5cd5-7cfd-7498-b10e-3d62f6d776af",
    thread_id="c72c45fb-a22a-4ed7-b5ac-06c80daf2e81",
    fixed_in="unfixed",
)
