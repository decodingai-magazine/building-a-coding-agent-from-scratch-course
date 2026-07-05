"""The model-callable ``agent`` tool + the in-process Explore-subagent runner (ADR-0013 §1,5-9).

``agent(prompt)`` is how a primary persona (build / plan / code-reviewer) spawns an **Explore
Subagent** to read a scoped slice of the codebase in parallel and hand back one compressed report. A
child is **not** a new session or a subprocess — it is the *same* Pydantic-AI :class:`~pydantic_ai.Agent`
re-entered via a nested ``agent.run()`` with FRESH, narrowed, read-only deps (``active_agent=explore``),
so ADR-0003 §6-7's per-tool ``prepare=`` collapses the child's toolset to ``read/glob/grep/lsp`` and the
instructions hook swaps in the explore persona automatically. There is no new loop and no new machinery
— just deps construction plus this one thin tool (ADR-0013 §1).

Three seams make it work, each mirroring an established idiom:

* **The set-once main-agent seam** (:func:`set_main_agent` / :func:`_require_main_agent`), mirroring
  bash's ``_EXECUTOR``: ``build_agent`` installs the one built Agent here, so the tool reuses that
  Agent's model + HTTP client (no per-child rebuild) and one ``agent.override(model=…)`` in a test
  drives parent AND children (ADR-0013 §6). Reading it while unset is a clear misconfiguration error.
* **The per-running-loop semaphore** (:func:`_semaphore`): one :class:`asyncio.Semaphore` sized to
  ``settings.subagent_max_parallel``, cached *per running loop* (keyed by
  :func:`asyncio.get_running_loop`), so it is loop-safe under both the single REPL loop and Kitaru's
  per-call loops — the same per-call-loop hazard the flow-mode HTTP client dodges. Within one model
  response all N fan-out ``agent(...)`` calls share one loop, so the cap actually bites (ADR-0013 §7).
* **The read-only child deps**: a fresh :class:`~decode.permissions.gate.PermissionGate` in **BYPASS**
  (a child runs a plain ``agent.run()`` with no harness loop to resolve a deferred approval, so its
  read-only tools must run *inline* — BYPASS makes ``needs_approval`` return ``False`` so nothing ever
  raises :class:`pydantic_ai.ApprovalRequired`), a fresh empty ``task_store``, a no-op event sink
  (children are silent in the TUI — ADR-0013 §8), and the headless deny resolvers so a stray gated/ask
  call fails safe instead of hanging. The child inherits only the parent's ``cwd`` + ``harness_home``
  (its read scope).

**Read-only by construction → permissions come free** (ADR-0013 §5). ``agent`` is registered
``ToolKind.READ_ONLY``, so it runs inline and never raises :class:`pydantic_ai.ApprovalRequired` — the
gate auto-allows it in every mode. Its children's tools are all read-only too, so a child never reaches
the gate or the single Decision Channel (which is *why* ``ask_user`` is forbidden to children — it
would deadlock the fan-out). Recursion is structurally impossible: the child's ``active_agent=explore``
omits ``agent``, so ``prepare=`` hides it — no depth counter needed.

The child run is bounded by ``UsageLimits(request_limit=settings.subagent_max_requests)`` and
deliberately does **not** thread ``usage=ctx.usage``, so the parent's context gauge + compaction
trigger keep reflecting the parent's context only (ADR-0013 §7,10). The child's final text — truncated
to ``settings.subagent_result_max_bytes`` — *is* the tool result; the transcript is ephemeral.
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
    # Typing only — kept out of the runtime import graph. ``Agent`` would not cycle, but the seam only
    # ever *holds* one; ``events`` is referenced solely in the no-op sink's annotation (a string under
    # ``from __future__ import annotations``), so neither is needed at runtime.
    from pydantic_ai import Agent

    from decode.entities import events

logger = logging.getLogger(__name__)

AGENT_TOOL_NAME = "agent"

# The ONE subagent persona that ships (ADR-0013 §3): a read-only Explore child. A second subagent later
# is a new ``subagent: true`` file + a ``subagent_type`` param — not a rewrite.
_SUBAGENT_PERSONA = "explore"

# The set-once module seam holding the running Agent, mirroring bash's ``_EXECUTOR``. ``build_agent``
# installs it via :func:`set_main_agent` after registering tools; :func:`_require_main_agent` reads it
# (raising a clear error if unset). Reusing the one built Agent means a child inherits the parent's
# model + HTTP client + any Model Override for free, and one ``override`` covers parent + children.
_MAIN_AGENT: Agent[AgentDeps, str | DeferredToolRequests] | None = None

# One :class:`asyncio.Semaphore` per running event loop, sized to ``subagent_max_parallel``. Keyed by
# the loop (a ``WeakKeyDictionary`` so a closed loop's entry is reclaimed) because an ``asyncio``
# primitive binds to the loop it is first awaited on: a single global semaphore reused across Kitaru's
# per-call loops would be bound to the wrong loop. Built lazily on first use inside a running loop.
_SEMAPHORES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def set_main_agent(agent: Agent[AgentDeps, str | DeferredToolRequests]) -> None:
    """Install the running Agent as the subagent-spawn seam (ADR-0013 §6).

    Called once by :func:`decode.agent.factory.build_agent` after ``register_tools`` +
    ``_register_instructions``, so the ``agent`` tool re-enters *this* Agent for every child. Like
    bash's ``install_executor`` it simply overwrites the module reference — a later ``build_agent``
    (a fresh REPL / a headless flow) replaces it with its own Agent.
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
    """The child's event sink: children are silent in the TUI, so their events only log (ADR-0013 §8).

    The parent's ``emit`` streams events to the terminal; a subagent runs silent-until-done, so its
    internal events are dropped at debug level (the folded report is the only thing the user sees).
    """
    logger.debug("subagent event (silent): %s", type(event).__name__)


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """Deny safety-net for a child's ``resolve_permission`` — never reached (ADR-0013 §5).

    A child's tools are all read-only, so no call ever resolves to an ``ASK`` and this resolver is
    never invoked. It exists only so the child :class:`~decode.agent.deps.AgentDeps` contract is
    satisfied; denying is the safe default for an unattended child if the posture ever changes
    (mirrors the headless flow's deny resolver).
    """
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
    # Lazy imports (mirrors deps.py:76-85's ``_default_active_agent``): ``load_agent`` would form a
    # tools -> agents -> tools import cycle at module load; ``PermissionGate`` / the ask-user deny
    # resolver / ``truncate`` are pulled in here alongside it. (``AgentDeps`` is a top-level import — it
    # is cycle-free and pydantic-ai must resolve it to build this tool's schema.)
    from decode.agents.loader import load_agent
    from decode.permissions.gate import PermissionGate
    from decode.tools.askuser import deny_user_question_resolver
    from decode.tools.truncate import truncate

    explore = load_agent(_SUBAGENT_PERSONA)
    # Invariant on packaged catalog data: you may only spawn a *subagent*, never a primary (ADR-0013 §3).
    assert explore.subagent, f"the {_SUBAGENT_PERSONA!r} persona must declare subagent: true"

    child_deps = AgentDeps(
        cwd=ctx.deps.cwd,
        harness_home=ctx.deps.harness_home,
        emit=_silent_emit,
        # A FRESH gate (never the parent's mutable one), in BYPASS: a child runs a plain ``agent.run()``
        # with NO harness loop to resolve a deferred approval, so its read-only tools must run INLINE.
        # BYPASS makes ``needs_approval`` return ``False`` for every call (``tools/approval.py``), so the
        # child never raises ``ApprovalRequired`` and never touches the gate / Decision Channel — exactly
        # the ADR-0013 §2,5 contract and the headless ``_build_headless_deps`` posture (``runtime/flow.py``).
        gate=PermissionGate(mode=PermissionMode.BYPASS),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=deny_user_question_resolver,
        active_agent=explore,
        # ``task_store`` is omitted so the dataclass default_factory gives each child a fresh empty list.
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
