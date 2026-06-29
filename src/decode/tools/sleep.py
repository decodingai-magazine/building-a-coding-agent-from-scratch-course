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

**Mode-aware via the ``_SLEEPER`` seam (ADR-0008 §4).** The *await* differs by entry path while the
guardrails do not. The module-level :data:`_SLEEPER` callable — mirroring bash ``_EXECUTOR`` / web
``_TRANSPORT`` / lsp ``_spawn_process`` — is the in-process :func:`asyncio.sleep` by default
(interactive TUI), so interactive behaviour is byte-unchanged. The Headless Runtime
(:mod:`decode.runtime.flow`) swaps in :func:`_durable_sleep` (a flow-scope ``kitaru.wait``) around a
durable run and resets it on flow exit, so a durable run's ``sleep`` can pause the execution and the
process exit, then resume — and no in-process REPL ever inherits the durable sleeper. The clamp and
the negative/``nan`` rejection run *before* the seam in both modes, so a bad request never reaches
``kitaru.wait``.
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

# The single argument every sleeper takes: the **already-capped** duration in seconds (so the cap and
# the negative/``nan`` rejection are applied once, in ``sleep``, before either implementation runs).
Sleeper = Callable[[float], Awaitable[None]]


async def _interactive_sleep(capped: float) -> None:
    """The default seam: the interactive in-process ``await asyncio.sleep(capped)`` (pre-ADR-0008).

    A thin wrapper rather than a bare ``asyncio.sleep`` reference so ``asyncio.sleep`` is resolved by
    module-global lookup at call time — this keeps the in-process behaviour byte-identical and lets a
    test patch ``decode.tools.sleep.asyncio.sleep`` exactly as before the seam existed.
    """
    await asyncio.sleep(capped)


# The mode-aware seam (ADR-0008 §4). Defaults to the interactive in-process sleeper; patched to
# :func:`_durable_sleep` by the Headless Runtime for the duration of a durable run and reset
# afterwards via :func:`reset_sleeper`. The interactive TUI never touches it.
_SLEEPER: Sleeper = _interactive_sleep


async def _durable_sleep(capped: float) -> None:
    """The headless durable sleeper: pause on a flow-scope ``kitaru.wait`` instead of sleeping inline.

    Installed by :mod:`decode.runtime.flow` for a durable run (ADR-0008 §4). ``capped`` is the
    duration ``sleep`` already clamped to ``settings.sleep_max_s``, so this never re-derives it. It
    calls the **sync** :func:`kitaru.wait` directly from this ``async`` body — the same async→sync
    bridge :func:`decode.runtime.flow.flow_resolve_user_question` uses (task 059): under
    ``KitaruAgent.run_sync`` with ``allow_sync_tool_body_waits=True`` the agent's event loop runs on
    Kitaru's workflow thread, which is exactly where a flow-scope wait must be created, so the
    blocking call is correct (offloading it to a worker thread would trip Kitaru's "waits must be at
    flow scope" guard). ``kitaru.wait``'s ``timeout`` is typed ``int`` (it counts whole seconds before
    the runner pauses + exits the execution), so the capped float is coerced to ``int`` — the same
    ``int(...)`` coercion task 059 applies to ``runtime_wait_timeout_s``. ``name="sleep"`` names the
    wait point; no ``schema`` is passed (a pure timer gate, not a request for human input).
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
    # ``not (seconds >= 0)`` rejects negatives AND nan (``nan >= 0`` is False); inf is >= 0 so it
    # falls through to be clamped by ``min`` below. A bare ``seconds < 0`` would let nan slip past.
    if not (seconds >= 0):
        logger.debug("sleep rejected seconds=%r", seconds)
        raise ModelRetry("seconds must be a non-negative number")
    capped = min(seconds, settings.sleep_max_s)
    logger.debug("sleep awaiting %s s (requested %s)", capped, seconds)
    await _SLEEPER(capped)
    return f"Slept {capped} s."
