"""The model-callable ``agent`` tool + the in-process Explore-subagent runner.

``agent(prompt)`` spawns a read-only **Explore Subagent**: the *same* Pydantic-AI Agent re-entered
via a nested ``agent.run()`` with fresh, narrowed deps (``active_agent=explore``, gate in BYPASS,
no-op event sink, deny resolvers), so ``prepare=`` collapses the child's toolset to
``read/glob/grep/lsp`` and recursion is structurally impossible. Three seams: the set-once
main-agent seam (mirrors bash's ``_EXECUTOR``), a per-running-loop fan-out semaphore, and the
read-only child deps. The child's truncated final text is the tool result; its transcript is
ephemeral, and its usage is not threaded into the parent's. See ADR-0013 §1,5-10.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import TYPE_CHECKING

from pydantic_ai import DeferredToolRequests, RunContext, UsageLimits

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.types import PermissionMode

if TYPE_CHECKING:
    # Typing only — neither is needed at runtime.
    from pydantic_ai import Agent

    from decode.entities import events

logger = logging.getLogger(__name__)

AGENT_TOOL_NAME = "agent"

# The ONE subagent persona that ships (ADR-0013 §3): a read-only Explore child.
_SUBAGENT_PERSONA = "explore"

# Set-once module seam holding the running Agent (mirrors bash's ``_EXECUTOR``); installed by
# ``build_agent`` via :func:`set_main_agent`, so children reuse the parent's model + HTTP client.
_MAIN_AGENT: Agent[AgentDeps, str | DeferredToolRequests] | None = None

# One semaphore per running event loop, sized to ``subagent_max_parallel``. Keyed by the loop
# (weakly) because an asyncio primitive binds to the loop it is first awaited on — a single global
# semaphore would bind to the wrong loop under Kitaru's per-call loops.
_SEMAPHORES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def set_main_agent(agent: Agent[AgentDeps, str | DeferredToolRequests]) -> None:
    """Install the running Agent as the subagent-spawn seam (ADR-0013 §6).

    Called once by :func:`decode.agent.factory.build_agent` after ``register_tools`` +
    ``_register_instructions``, so the ``agent`` tool re-enters *this* Agent for every child. It
    simply overwrites the module reference — a later ``build_agent`` (a fresh REPL / a headless
    flow) replaces it with its own Agent.
    """
    global _MAIN_AGENT
    _MAIN_AGENT = agent


def _require_main_agent() -> Agent[AgentDeps, str | DeferredToolRequests]:
    """Return the installed main Agent, or raise a clear misconfiguration error (ADR-0013 §6)."""
    if _MAIN_AGENT is None:
        raise RuntimeError(
            "the agent tool has no main Agent to spawn a subagent from — build_agent() must call "
            "set_main_agent(agent) before a run (mirrors bash's executor seam)."
        )
    return _MAIN_AGENT


def reset_main_agent() -> None:
    """Clear the main-agent seam — test hermeticity, mirroring ``bash.reset_executor`` (ADR-0013 §6)."""
    global _MAIN_AGENT
    _MAIN_AGENT = None


def _semaphore() -> asyncio.Semaphore:
    """Return this running loop's child-fan-out semaphore, building it once per loop (ADR-0013 §7)."""
    loop = asyncio.get_running_loop()
    existing = _SEMAPHORES.get(loop)
    if existing is not None:
        return existing
    created = asyncio.Semaphore(settings.subagent_max_parallel)
    _SEMAPHORES[loop] = created
    return created


def _reset_semaphores() -> None:
    """Drop every cached per-loop semaphore — test hermeticity (so a re-sized cap takes effect)."""
    _SEMAPHORES.clear()


def _silent_emit(event: events.Event) -> None:
    """The child's event sink: children are silent in the TUI, so their events only log (ADR-0013 §8)."""
    logger.debug("subagent event (silent): %s", type(event).__name__)


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """Deny safety-net for a child's ``resolve_permission`` — never reached, a read-only child
    never resolves to an ASK; denying is the safe default for an unattended child (ADR-0013 §5)."""
    logger.debug("subagent permission resolver denying tool=%s", request.tool_name)
    return PermissionDecision.deny(reason="A subagent runs read-only with no interactive approver.")


async def agent(ctx: RunContext[AgentDeps], prompt: str) -> str:
    """Spawn a read-only Explore subagent to investigate ``prompt`` and return its report (ADR-0013).

    Builds FRESH, narrowed deps — the parent's ``cwd`` + ``harness_home`` (the child's read scope), a
    no-op event sink (silent in the TUI), a fresh :class:`~decode.permissions.gate.PermissionGate`, a
    fresh empty ``task_store``, the headless deny resolvers, and ``active_agent=explore`` — then, under
    the per-loop concurrency semaphore, re-enters the installed main Agent via a nested ``agent.run()``.
    The child is bounded by ``UsageLimits(request_limit=settings.subagent_max_requests)`` and does
    **not** thread ``usage=ctx.usage`` (so the parent's context gauge stays parent-only, ADR-0013 §7,10).

    The child's final text — truncated to ``settings.subagent_result_max_bytes`` — is returned as the
    tool result. Defensive: a :class:`pydantic_ai.DeferredToolRequests` output is theoretically
    impossible for a read-only child (its tools never raise :class:`pydantic_ai.ApprovalRequired`), so
    that case returns a short note rather than the raw object.
    """
    # Lazy imports: ``load_agent`` would form a tools -> agents -> tools cycle at module load.
    from decode.agents.loader import load_agent
    from decode.permissions.gate import PermissionGate
    from decode.tools.askuser import deny_user_question_resolver
    from decode.tools.truncate import truncate

    explore = load_agent(_SUBAGENT_PERSONA)
    # You may only spawn a *subagent*, never a primary (ADR-0013 §3).
    assert explore.subagent, f"the {_SUBAGENT_PERSONA!r} persona must declare subagent: true"

    child_deps = AgentDeps(
        cwd=ctx.deps.cwd,
        harness_home=ctx.deps.harness_home,
        emit=_silent_emit,
        # A FRESH gate in BYPASS: no harness loop resolves a child's deferred approval, so its
        # read-only tools must run inline (never raise ApprovalRequired) — ADR-0013 §2,5.
        gate=PermissionGate(mode=PermissionMode.BYPASS),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=deny_user_question_resolver,
        active_agent=explore,
        # ``task_store`` omitted: the default_factory gives each child a fresh empty list.
    )

    logger.debug("spawning explore subagent (prompt=%r)", prompt)
    async with _semaphore():
        result = await _require_main_agent().run(
            prompt,
            deps=child_deps,
            usage_limits=UsageLimits(request_limit=settings.subagent_max_requests),
        )

    output = result.output
    if isinstance(output, DeferredToolRequests):
        # Impossible in practice (a read-only child never defers a tool), but never leak the object.
        logger.warning("subagent run resolved to DeferredToolRequests; returning a fallback note")
        return "The subagent could not complete its investigation."

    return truncate(
        str(output),
        max_lines=settings.max_output_lines,
        max_bytes=settings.subagent_result_max_bytes,
    ).text
