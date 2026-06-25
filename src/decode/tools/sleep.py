"""The ungated ``sleep`` control tool — a bounded ``await asyncio.sleep`` (ADR-0003 §8).

``sleep`` lets the model pause its own turn — e.g. to back off before re-checking a long-running
job. It is a one-line ``await asyncio.sleep(...)`` with two guardrails:

* it is **capped** at ``settings.sleep_max_s`` so a model can never stall a turn indefinitely — a
  request larger than the cap is *clamped* to the cap (not rejected), and the confirmation reports
  the duration actually slept;
* a **non-negative** ``seconds`` is required: a negative *or* ``nan`` request is rejected with a
  model-readable :class:`pydantic_ai.ModelRetry` so the model corrects the call instead of stalling
  the turn (``nan`` would defeat the cap — ``min(nan, …)`` is ``nan`` and ``asyncio.sleep(nan)``
  never returns; ``inf`` is harmless because it falls through to be clamped by the cap).

**Ungated (ADR-0003 §8).** Like ``ask_user`` and the plan-mode controls, ``sleep`` touches no
filesystem and never raises :class:`pydantic_ai.ApprovalRequired`, so it never reaches the
permission gate — it is a pure control signal, usable in any mode (including plan mode). Its
``SLEEP_TOOL_NAME`` constant lives in :mod:`decode.tools.orchestration` (the one place the tools
package owns the orchestration tool-name constants the agents-catalog loader validates against).
"""

from __future__ import annotations

import asyncio
import logging

from pydantic_ai import ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.tools.orchestration import SLEEP_TOOL_NAME

logger = logging.getLogger(__name__)

__all__ = ["SLEEP_TOOL_NAME", "sleep"]


async def sleep(ctx: RunContext[AgentDeps], seconds: float) -> str:
    """Pause the turn for ``seconds`` (capped at ``settings.sleep_max_s``); ADR-0003 §8.

    ``seconds`` is clamped to ``settings.sleep_max_s`` (a sane upper bound read only via the
    settings singleton) so the model cannot stall the turn forever. A non-finite-or-negative
    ``seconds`` (a negative value or ``nan``) is a model mistake, not a crash: it raises a
    model-readable :class:`pydantic_ai.ModelRetry` and nothing sleeps. ``nan`` is rejected here
    rather than clamped because ``min(nan, …)`` is ``nan`` and ``asyncio.sleep(nan)`` never returns,
    which would defeat the cap. Returns a short confirmation reporting the duration actually slept.

    Ungated: ``sleep`` never raises :class:`pydantic_ai.ApprovalRequired`, so it never reaches
    the permission gate (ADR-0003 §8). ``ctx`` is accepted for the context-aware registration the
    registry uses but is not otherwise consulted.
    """
    # ``not (seconds >= 0)`` rejects negatives AND nan (``nan >= 0`` is False); inf is >= 0 so it
    # falls through to be clamped by ``min`` below. A bare ``seconds < 0`` would let nan slip past.
    if not (seconds >= 0):
        logger.debug("sleep rejected seconds=%r", seconds)
        raise ModelRetry("seconds must be a non-negative number")
    capped = min(seconds, settings.sleep_max_s)
    logger.debug("sleep awaiting %s s (requested %s)", capped, seconds)
    await asyncio.sleep(capped)
    return f"Slept {capped} s."
