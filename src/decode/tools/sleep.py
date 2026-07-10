"""The ungated ``sleep`` control tool — a bounded ``await asyncio.sleep``.

Two guardrails: the duration is clamped to ``settings.sleep_max_s``, and a negative/``nan``
request raises a model-readable :class:`pydantic_ai.ModelRetry`. Ungated: a pure control signal
that never raises :class:`pydantic_ai.ApprovalRequired` (ADR-0003 §8). The await goes through
the mode-aware :data:`_SLEEPER` seam — in-process :func:`asyncio.sleep` interactively, a durable
flow-scope ``kitaru.wait`` under the Headless Runtime (ADR-0008 §4); the guardrails run before
the seam in both modes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from pydantic_ai import ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.tools.orchestration import SLEEP_TOOL_NAME

logger = logging.getLogger(__name__)

__all__ = [
    "SLEEP_TOOL_NAME",
    "install_durable_sleeper",
    "reset_sleeper",
    "sleep",
]

# Every sleeper takes the **already-capped** duration: the guardrails run once, in ``sleep``.
Sleeper = Callable[[float], Awaitable[None]]


async def _interactive_sleep(capped: float) -> None:
    """The default seam: in-process ``await asyncio.sleep(capped)``.

    A thin wrapper (not a bare reference) so ``asyncio.sleep`` resolves at call time and a test
    can still patch ``decode.tools.sleep.asyncio.sleep``.
    """
    await asyncio.sleep(capped)


# The mode-aware seam (ADR-0008 §4): defaults interactive; the Headless Runtime swaps in
# :func:`_durable_sleep` for a durable run and resets it afterwards.
_SLEEPER: Sleeper = _interactive_sleep


async def _durable_sleep(capped: float) -> None:
    """The headless durable sleeper: pause on a flow-scope ``kitaru.wait`` instead of sleeping inline.

    Installed by :mod:`decode.runtime.flow` (ADR-0008 §4). Calling the sync :func:`kitaru.wait`
    from this async body is correct: under ``KitaruAgent.run_sync`` the agent's loop runs on
    Kitaru's workflow thread, exactly where a flow-scope wait must be created (offloading to a
    worker thread would trip Kitaru's flow-scope guard). ``timeout`` is typed ``int``, hence the
    coercion; no ``schema`` — a pure timer gate, not a request for human input.
    """
    import kitaru

    logger.debug("durable sleep waiting %s s (capped) via kitaru.wait", capped)
    kitaru.wait(name=SLEEP_TOOL_NAME, timeout=int(capped))


def install_durable_sleeper() -> None:
    """Install the durable sleeper as the active seam (Headless Runtime entry; ADR-0008 §4).

    Called by :mod:`decode.runtime.flow` immediately before a durable ``run_sync`` so a ``sleep`` in
    that run pauses on a flow-scope ``kitaru.wait``. Always paired with :func:`reset_sleeper` in a
    ``finally`` so the durable sleeper never leaks into a later in-process interactive ``sleep``.
    """
    global _SLEEPER
    _SLEEPER = _durable_sleep


def reset_sleeper() -> None:
    """Restore the default in-process :func:`asyncio.sleep` seam (reset on durable-flow exit).

    The companion of :func:`install_durable_sleeper`: the Headless Runtime calls it in a ``finally``
    so a subsequent interactive ``sleep`` uses :func:`asyncio.sleep` again (no global leakage).
    """
    global _SLEEPER
    _SLEEPER = _interactive_sleep


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

    The await goes through the mode-aware :data:`_SLEEPER` seam: the in-process
    :func:`asyncio.sleep` interactively, or the durable :func:`_durable_sleep` under the Headless
    Runtime (ADR-0008 §4). The clamp and the negative/``nan`` rejection run *before* the seam, so a
    bad request never reaches the durable ``kitaru.wait`` and the cap is never defeated in either mode.
    """
    # ``not (seconds >= 0)`` rejects negatives AND nan; inf falls through to the clamp below.
    if not (seconds >= 0):
        logger.debug("sleep rejected seconds=%r", seconds)
        raise ModelRetry("seconds must be a non-negative number")
    capped = min(seconds, settings.sleep_max_s)
    logger.debug("sleep awaiting %s s (requested %s)", capped, seconds)
    await _SLEEPER(capped)
    return f"Slept {capped} s."
