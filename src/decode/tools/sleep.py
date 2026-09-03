"""The ungated ``sleep`` control tool — a bounded ``await asyncio.sleep``.

Two guardrails: the duration is clamped to ``settings.sleep_max_s``, and a negative/``nan``
request raises a model-readable :class:`pydantic_ai.ModelRetry`. Ungated: a pure control signal
that never raises :class:`pydantic_ai.ApprovalRequired` (ADR-0003 §8). The await is a plain
in-process :func:`asyncio.sleep` in every mode — the durable flow-scope ``kitaru.wait`` seam died
with the durable runtime (ADR-0019 §1).
"""

from __future__ import annotations

import asyncio
import logging

from pydantic_ai import ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.tools.orchestration import SLEEP_TOOL_NAME

logger = logging.getLogger(__name__)

__all__ = [
    "SLEEP_TOOL_NAME",
    "sleep",
]


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
    # ``not (seconds >= 0)`` rejects negatives AND nan; inf falls through to the clamp below.
    if not (seconds >= 0):
        logger.debug("sleep rejected seconds=%r", seconds)
        raise ModelRetry("seconds must be a non-negative number")
    capped = min(seconds, settings.sleep_max_s)
    logger.debug("sleep awaiting %s s (requested %s)", capped, seconds)
    await asyncio.sleep(capped)
    return f"Slept {capped} s."
