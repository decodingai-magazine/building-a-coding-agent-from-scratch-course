"""Online eval: score decode's LIVE REPL threads with one conversation judge (ADR-0017 §10; task 117).

The production-eval story at demo scale. Everything else in the suite grades a run the harness itself
drives; this track grades the traces decode ALREADY emitted from real REPL sessions (ADR-0014) — the
threads Opik keys by session id / Kitaru ``exec_id`` — inside the LIVE project
``settings.opik_project_name`` (``decode``/``decode-<env>``), NOT ``eval_project_name``. One
conversation-level LLM judge (:func:`make_conversation_metric`) runs over each recent thread via
``opik.evaluation.evaluate_threads`` and its scores are logged back onto those same live threads.

Two deliberate design points:

* **Judge routing is shared, not re-derived.** The judge model comes from
  :func:`evals.harness.judges.resolve_judge_model` — the exact string (or modal base-URL
  :class:`LiteLLMChatModel`) the G-Eval trace judges use — so the online judge follows decode's own
  provider with zero new routing.
* **Lazy opik, keyless ``--help``.** ``opik`` is imported only inside :func:`run_online_eval`; the key
  check (:func:`online_keys_missing`) reads only ``settings``. So ``python -m evals online --help`` and
  the friendly no-key skip never touch Opik or the network.

Verified against the INSTALLED ``opik==1.9.8`` ``evaluate_threads`` signature (task-117 log):
``(project_name, filter_string, eval_project_name, metrics, trace_input_transform,
trace_output_transform, verbose=1, num_workers=8, max_traces_per_thread=1000)`` — every one of the
first six is required (``filter_string``/``eval_project_name`` accept ``None``), so all six are passed
explicitly.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from decode.config.settings import settings
from evals.harness.judges import resolve_judge_model
from evals.harness.keys import eval_keys_missing

if TYPE_CHECKING:
    from opik.evaluation.metrics.conversation.conversation_thread_metric import (
        ConversationThreadMetric,
    )
    from opik.evaluation.threads.evaluation_result import ThreadsEvaluationResult

logger = logging.getLogger(__name__)

# The conversation-level judge's name on the live threads' feedback scores. ConversationalCoherenceMetric
# is a PRESET conversation judge (no custom criteria to phrase), so the 0-10 / "Score 1.0/0.0" phrasing
# collision the G-Eval probes must dodge (task-114 lesson) does not apply here — it applies to the UI
# online RULE the walkthrough in evals/README.md sets up, where the operator DOES write the criteria.
CONVERSATION_METRIC_NAME = "conversation_coherence"


def live_project_name() -> str:
    """The Opik project whose LIVE REPL threads this track scores (ADR-0014; ADR-0017 §10).

    Deliberately ``settings.opik_project_name`` (``decode``/``decode-<env>``), NOT
    ``settings.eval_project_name``: online eval grades REAL traffic in place, so the benchmark's
    "keep eval runs off the live project" rule is inverted here — scoring the live threads IS the point.
    """
    return settings.opik_project_name


def online_keys_missing() -> list[str]:
    """The env-var names online eval needs but does not have — empty means good to run (ADR-0017 §10).

    Delegates to the ONE shared, settings-backed, provider-aware preflight
    (:func:`evals.harness.keys.eval_keys_missing`) so this track cannot drift from the offline gates:
    the required set is identical — ``OPIK_API_KEY`` to reach the threads, plus the active provider's
    key so the conversation judge can actually grade (``gemini`` → ``GEMINI_API_KEY``, ``openrouter``
    → ``OPENROUTER_API_KEY``, ``modal`` → ``MODAL_ENDPOINT_URL``). Reads only ``settings`` (never
    ``opik``), so the CLI can decide to skip friendly without importing the Opik client or touching the
    network. An explicit ``EVAL_JUDGE_MODEL`` override does not change WHICH provider key LiteLLM will
    need, so the provider check stands regardless.
    """
    return eval_keys_missing()


def make_conversation_metric() -> ConversationThreadMetric:
    """The single conversation-level LLM judge run over each live thread (ADR-0017 §7,§10).

    ``ConversationalCoherenceMetric`` reads the whole user/assistant transcript and judges whether the
    assistant's turns stay coherent and on-topic across the session — a conversation-scale response-
    quality proxy, the natural fit for multi-turn REPL threads. Its judge model is
    :func:`resolve_judge_model` (decode's own provider routing), so no LLM call happens at construction.
    """
    from opik.evaluation.metrics import ConversationalCoherenceMetric

    return ConversationalCoherenceMetric(
        model=resolve_judge_model(),
        name=CONVERSATION_METRIC_NAME,
    )


def _stringify(value: Any) -> str:
    """Coerce a trace's ``input``/``output`` payload to the plain string a conversation turn needs.

    ``evaluate_threads`` hands the transforms whatever shape a trace recorded — a bare string, or the
    dict/list pydantic-ai + Opik store. A string passes through; a dict prefers the usual text-bearing
    keys (``output``/``content``/``text``/``input``) before falling back to compact JSON; anything else
    is JSON-encoded (``default=str`` so a stray non-serializable value degrades to its repr, never
    raises inside Opik's worker pool).
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("output", "content", "text", "input", "response"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def trace_input_transform(value: Any) -> str:
    """Map a trace's recorded ``input`` to the user-turn string the conversation judge reads."""
    return _stringify(value)


def trace_output_transform(value: Any) -> str:
    """Map a trace's recorded ``output`` to the assistant-turn string the conversation judge reads."""
    return _stringify(value)


def run_online_eval(
    *, filter_string: str | None = None, max_traces_per_thread: int = 1000
) -> ThreadsEvaluationResult:
    """Score the live project's recent threads with the conversation judge (ADR-0017 §10).

    Calls ``opik.evaluation.evaluate_threads`` over :func:`live_project_name` with the single
    conversation metric; ``eval_project_name=None`` logs the scores back onto those SAME live threads
    (online eval grades traffic in place). ``filter_string`` is an optional Opik OQL clause to scope the
    run to recent/relevant threads (e.g. ``'start_time > "2026-07-01T00:00:00Z"'``); ``None`` evaluates
    every thread in the project. ``opik`` is imported here, not at module scope, so the CLI stays
    keyless until this actually runs. Returns Opik's ``ThreadsEvaluationResult`` (its ``results`` carry
    per-thread ``scores``).
    """
    from opik.evaluation import evaluate_threads

    project = live_project_name()
    metric = make_conversation_metric()
    logger.info("[eval] scoring live threads in project=%s (filter=%r)", project, filter_string)
    return evaluate_threads(
        project_name=project,
        filter_string=filter_string,
        eval_project_name=None,
        metrics=[metric],
        trace_input_transform=trace_input_transform,
        trace_output_transform=trace_output_transform,
        max_traces_per_thread=max_traces_per_thread,
    )


def format_thread_scores(result: ThreadsEvaluationResult) -> list[str]:
    """One human-readable line per scored thread — ``<thread_id>: <metric>=<value> ...`` (task 117).

    A pure formatter over Opik's ``ThreadsEvaluationResult`` (no I/O), so the CLI's printout is unit-
    tested without a real run. A thread that produced no scores is still listed (``<thread_id>: no
    scores``) so an empty judge result is visible rather than silently dropped; a score whose
    ``scoring_failed`` is set is shown as ``<metric>=failed`` with its reason.
    """
    lines: list[str] = []
    for thread in result.results:
        if not thread.scores:
            lines.append(f"{thread.thread_id}: no scores")
            continue
        parts: list[str] = []
        for score in thread.scores:
            if getattr(score, "scoring_failed", False):
                reason = (score.reason or "").strip()
                parts.append(f"{score.name}=failed" + (f" ({reason})" if reason else ""))
            else:
                parts.append(f"{score.name}={score.value:.2f}")
        lines.append(f"{thread.thread_id}: " + " ".join(parts))
    return lines
