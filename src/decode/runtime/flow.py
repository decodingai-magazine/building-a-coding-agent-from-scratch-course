"""The Headless Runtime: a Kitaru Durable Flow that runs ``build_agent()`` autonomously (ADR-0008).

This is decode's **second entry path** (ADR-0008 §1). The interactive TUI drives ``agent.iter()``
through the harness and streams to the terminal; this module instead runs the **same**
:func:`decode.agent.factory.build_agent` headlessly inside a Kitaru ``@flow`` so an unattended run
is durable — each turn is checkpointed and a crash replays finished turns from cache instead of
re-paying for them. Launched by ``decode run "<task>"`` (see :mod:`decode.cli`).

**Durability via the PydanticAI adapter (ADR-0008 §2).** :func:`_build_runtime_agent` wraps the
factory's :class:`~pydantic_ai.Agent` in :class:`kitaru.adapters.pydantic_ai.KitaruAgent`. The flow
calls ``KitaruAgent.run_sync(task)`` — Kitaru's loop, **not** decode's interactive loop. The flow
body is **sync**; the adapter bridges the async pydantic-ai agent internally, so there is no manual
asyncio here.

**Why bypass, and why tools run inline (ADR-0008 §2).** ``run_sync`` does not use decode's loop, so
the loop's deferred-approval round-trip (which resolves every ``ApprovalRequired`` through the
permission gate) is not in play. The Kitaru adapter converts *any* ``ApprovalRequired`` into a
flow-scope ``kitaru.wait()`` (a human-in-the-loop pause) — and 058 has no human to resolve it. So
the headless deps put the :class:`~decode.permissions.gate.PermissionGate` in **BYPASS**, which
makes every gated tool run **inline** instead of deferring (see
:func:`decode.tools.approval.needs_approval`). Nothing raises ``ApprovalRequired``, nothing waits,
and ``run_sync`` returns a clean text result. Durable approvals / ``ask_user`` as a real
``kitaru.wait()`` are task 059.

**Headless safety.** ``ask_user`` / ``exit_plan_mode`` route through
:func:`decode.tools.askuser.deny_user_question_resolver`, which raises so the tool maps it to a
``ModelRetry`` ("no human attached") and the agent proceeds without an answer rather than hanging.
``resolve_permission`` is a deny safety-net that is never reached under BYPASS (the gate never asks).

**Kitaru imports stay inside this package** so importing :mod:`decode.cli` (the REPL path) never
imports kitaru — the ``run`` subcommand imports :mod:`decode.runtime` lazily.
"""

from __future__ import annotations

import logging
from pathlib import Path

from kitaru import flow
from kitaru.adapters.pydantic_ai import KitaruAgent
from pydantic_ai import DeferredToolRequests

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.config.settings import settings
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.tools.askuser import deny_user_question_resolver

logger = logging.getLogger(__name__)

# The stable Agent name Kitaru needs for checkpoint identity (the factory's Agent has none).
# It names the per-turn checkpoint, so it must be stable across runs for replay to hit cache.
RUNTIME_AGENT_NAME = "decode-runtime"


def _headless_emit(event: events.Event) -> None:
    """The headless event sink: there is no TUI, so events are only logged (ADR-0008 §1).

    The interactive ``emit`` streams events to the terminal; a headless run has no surface to
    render them, so we drop them at debug level. Kitaru's own checkpoint metadata is the durable
    record of what happened.
    """
    logger.debug("runtime event: %s", type(event).__name__)


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """Deny safety-net for ``resolve_permission`` — never reached under BYPASS (ADR-0008 §2).

    Under the headless BYPASS gate no tool call ever resolves to an ``ASK`` (BYPASS auto-allows,
    and gated tools run inline), so this resolver is never invoked. It exists only so the
    :class:`~decode.agent.deps.AgentDeps` contract is satisfied; denying is the safe default for an
    unattended run if the posture ever changes.
    """
    logger.debug("headless permission resolver denying tool=%s", request.tool_name)
    return PermissionDecision.deny(reason="No interactive approver in the headless runtime.")


def _build_runtime_agent() -> KitaruAgent[AgentDeps, str | DeferredToolRequests]:
    """The patchable runtime seam: wrap ``build_agent()`` in ``KitaruAgent`` (ADR-0008 §2).

    Mirrors the bash ``_EXECUTOR`` / lsp ``_spawn_process`` seams: the one place a real
    ``KitaruAgent`` is constructed, so a test can patch it to inject a scripted-model agent and
    exercise the real ``@flow`` + adapter offline. ``checkpoint_strategy`` comes from settings
    (``"turn"`` — one checkpoint per turn — is the MVP default; ``"calls"`` is per model/tool call).
    """
    agent = build_agent()
    return KitaruAgent(
        agent,
        name=RUNTIME_AGENT_NAME,
        checkpoint_strategy=settings.runtime_checkpoint_strategy,
    )


def _build_headless_deps() -> AgentDeps:
    """Construct the headless :class:`~decode.agent.deps.AgentDeps` (ADR-0008 §2).

    ``cwd`` is the launch directory; ``emit`` only logs (no TUI); the gate is in **BYPASS** so
    every gated tool runs inline (no ``ApprovalRequired`` → no Kitaru wait); and both decision
    resolvers are the headless deny defaults so ``ask_user`` / ``exit_plan_mode`` map to a
    ``ModelRetry`` instead of hanging. ``active_agent`` defaults (via the dataclass factory) to the
    full-tool ``build`` persona — the same persona the interactive default uses.
    """
    return AgentDeps(
        cwd=Path.cwd(),
        emit=_headless_emit,
        gate=PermissionGate(mode=PermissionMode.BYPASS),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=deny_user_question_resolver,
    )


@flow
def run_agent_task(task: str) -> str:
    """Run ``task`` to completion through the durable agent and return its final text (ADR-0008 §1-2).

    Sync ``@flow``: build the durable agent (the patchable seam), construct the headless BYPASS
    deps, and call ``run_sync(task)`` — one or more checkpointed turns, every tool inline, no human
    wait. Returns the agent's final text. A crash mid-run replays the finished turns from the Kitaru
    cache on a re-run rather than re-executing them.

    Launched via ``run_agent_task.run(task=…)`` → a ``FlowHandle``; ``.wait()`` blocks for the
    terminal checkpoint (see :func:`decode.cli` for the text extraction).
    """
    durable_agent = _build_runtime_agent()
    deps = _build_headless_deps()
    result = durable_agent.run_sync(task, deps=deps)
    output = result.output
    if not isinstance(output, str):
        # Defensive: under BYPASS every tool runs inline, so a run never resolves to a deferred
        # request. Reaching here means a gated tool ignored bypass — a bug, not a user-facing path.
        raise RuntimeError(
            "headless runtime expected text output but the agent deferred a tool call; "
            "BYPASS mode must run every tool inline (ADR-0008 §2)."
        )
    return output
