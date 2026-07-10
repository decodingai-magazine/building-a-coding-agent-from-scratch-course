"""The single gated-tool approval predicate.

Every gated tool opens its body with the same guard: raise :class:`pydantic_ai.ApprovalRequired`
until approved, so the run resolves to ``DeferredToolRequests`` and decode's loop routes the call
through the gate. :func:`needs_approval` states that rule once. It reads the gate mode so the
headless ``BYPASS`` posture runs tools inline (no loop resolves a deferred approval under
``KitaruAgent.run_sync``), and under the headless HITL flag it applies the read-only-allow floor
itself: read-only tools run inline, mutating tools defer into a durable ``kitaru.wait``.
See ADR-0003 §1,3 and ADR-0008 §2,3.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from decode.agent.deps import AgentDeps
from decode.permissions.types import PermissionMode, ToolKind


def needs_approval(ctx: RunContext[AgentDeps]) -> bool:
    """Whether a gated tool must defer for approval on this call (ADR-0003 §3; ADR-0008 §2,3).

    ``True`` — raise :class:`pydantic_ai.ApprovalRequired` — when the call is **not yet approved**
    *and* the gate is **not** in :class:`~decode.permissions.types.PermissionMode.BYPASS`. Under
    ``BYPASS`` (and on an already-approved resume leg) the tool runs inline. This keeps every
    interactive mode's behaviour byte-for-byte (they defer and decode's loop resolves via the
    gate) while letting the headless ``bypass`` run execute tools directly under
    ``KitaruAgent.run_sync`` instead of stalling on a Kitaru wait.

    In the **headless HITL** posture (``ctx.deps.headless_durable_waits`` — task 059) the gate is in
    a gating mode but no loop runs it, so the predicate applies the read-only-allow floor itself:
    a :class:`~decode.permissions.types.ToolKind.READ_ONLY` tool runs inline, every mutating tool
    defers so the Kitaru adapter turns its ``ApprovalRequired`` into a durable approval wait.
    """
    if ctx.tool_call_approved or ctx.deps.gate.mode is PermissionMode.BYPASS:
        return False
    if ctx.deps.headless_durable_waits:
        # Lazy import: a module-level import would cycle (tools -> registry -> this module).
        from decode.tools import tool_kind

        return tool_kind(ctx.tool_name or "") is not ToolKind.READ_ONLY
    return True
