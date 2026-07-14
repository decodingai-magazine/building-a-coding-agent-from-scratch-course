"""Probe 16 — an early fact survives auto-compaction and is recalled (ADR-0006; ADR-0017 §2,6).

Compaction-survival discipline (ADR-0006): when a conversation grows near the context window, decode
compacts the older turns into a summary so the agent can keep working — and a fact stated early must
survive that summary. The probe seeds a near-limit pre-filled history (``near_limit_history``) whose
FIRST turn states a distinctive fact (a deploy token), then asks the agent to recall it. The run passes
when the answer contains the fact (:class:`OutputContainsMetric`).

**Making compaction actually fire (the 111 QA lesson).** The real trigger is window-relative:
``input_tokens >= window * (1 - reserve)`` with a default window of 1,048,576 tokens — a few-thousand-
token history never crosses it. So the probe forces a small window AND a small keep-recent tail via
``settings_overrides`` (rolled back after the run), and sets ``enable_compaction`` so the driver wires
the summarizer (its OWN model — real provider live, scripted offline). Under these settings a real model
reports enough input tokens to cross the trigger and the older turns — including the early-fact turn —
are summarized. ``settings_overrides`` is unit-asserted to cross the threshold offline
(``should_compact`` is ``True`` for the seeded history), and ``compact()`` is shown to collapse it; the
end-to-end fire needs a real provider's token accounting, so the live spot-run carries that
([HUMAN]-verified, since a scripted ``FunctionModel`` streams a stub ~50-token usage that cannot cross
any trigger). ``BYPASS`` gate — recall needs no approval.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelMessage

from evals.harness.metrics import OutputContainsMetric
from evals.regression.fixtures import near_limit_history
from evals.regression.probe import RegressionProbe

# The distinctive early fact the agent must recall through compaction (a value it could not guess).
EARLY_FACT = "The production deploy token is deploy-zx9-4471."
# The unique substring the recall answer must contain.
FACT_NEEDLE = "deploy-zx9-4471"

# The near-limit history's coarse token size (chars/4). Sized above the forced trigger window below so
# a real provider's reported input tokens cross ``window * (1 - reserve)``.
HISTORY_TARGET_TOKENS = 3000
# The forced compaction settings for this run only (rolled back after). A small window makes the
# trigger reachable; a small keep-recent tail makes ``split_tail`` actually cut so there IS something to
# summarize (the default 20k tail would swallow the whole history and compaction would no-op).
COMPACTION_WINDOW_TOKENS = 2000
COMPACTION_KEEP_RECENT_TOKENS = 500


def _fixture(_workspace: Path) -> None:
    """No files on disk — the fact lives in the pre-filled conversation, not the Workspace."""


def _history() -> list[ModelMessage]:
    """Build the near-limit history whose first turn states :data:`EARLY_FACT`."""
    return near_limit_history(target_tokens=HISTORY_TARGET_TOKENS, early_fact=EARLY_FACT)


PROBE = RegressionProbe(
    id="16-compaction-survival",
    prompt=(
        "Earlier in this conversation I gave you the production deploy token. What is it? "
        "Answer with the token value."
    ),
    fixture=_fixture,
    # No MaxStepsMetric here: the pre-filled near-limit history inflates the ModelResponse count, so a
    # step budget would grade the seeded turns, not the agent's work. Recall-in-answer is the behavior.
    metrics=[
        OutputContainsMetric(FACT_NEEDLE, name="output_contains_deploy_token"),
    ],
    message_history=_history,
    settings_overrides={
        "compaction_context_window_tokens": COMPACTION_WINDOW_TOKENS,
        "compaction_keep_recent_tokens": COMPACTION_KEEP_RECENT_TOKENS,
    },
    enable_compaction=True,
    max_requests=3,
    tags=["compaction-survival", "context-engineering"],
)
