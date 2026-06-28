"""The single gated-tool approval predicate (ADR-0003 §1,3; ADR-0008 §2).

Every gated tool (``read`` / ``glob`` / ``grep`` / ``write`` / ``edit`` / ``bash`` / ``web_fetch``
/ ``todo_write`` / ``lsp``) opens its body with the *same* guard: raise
:class:`pydantic_ai.ApprovalRequired` until the call is approved, so the Pydantic AI run resolves
to :class:`~pydantic_ai.DeferredToolRequests` and decode's loop (:mod:`decode.agent.loop`) routes
the call through the :class:`~decode.permissions.gate.PermissionGate`. :func:`needs_approval` is
that guard, factored into one place so the rule is stated once.

**Why the gate mode is read here (ADR-0008 §2).** The deferred-approval round-trip is resolved by
decode's *loop*. The Headless Runtime (:mod:`decode.runtime`) drives the agent through
``KitaruAgent.run_sync`` instead — Kitaru's own loop, **not** decode's — and the Kitaru PydanticAI
adapter converts *any* ``ApprovalRequired`` into a flow-scope ``kitaru.wait()`` (a human-in-the-loop
pause). In the headless ``bypass`` posture there is no human to resolve that wait, so a gated tool
must **run inline** rather than defer. Reading ``ctx.deps.gate.mode`` here lets a gated tool skip
the deferral *only* under :class:`~decode.permissions.types.PermissionMode.BYPASS`:

* **default / plan / edit** (interactive, the common case): mode is not ``BYPASS`` →
  :func:`needs_approval` returns ``True`` on an unapproved call → the tool defers exactly as before
  → decode's loop asks the gate (rules + mode x kind) and resolves allow/ask/deny. **Unchanged.**
* **bypass**: the gate would auto-allow every call anyway (ADR-0003 §4), so the tool runs inline
  with the same public outcome (no prompt, the body runs) — but without a deferred round-trip,
  which is what lets ``KitaruAgent.run_sync`` execute it headlessly instead of pausing on a wait.

The tool's own argument validation always runs **after** this guard, so it fires identically on
both paths (a bypass/approved call is still validated before it acts).

**The headless HITL path (ADR-0008 §3, task 059).** A third posture runs under
``KitaruAgent.run_sync`` like bypass, but with the gate in a *gating* mode so mutating tools pause
on a durable :func:`kitaru.wait` resolved out-of-band. Here too there is no decode loop to run the
gate, so the predicate decides *itself*: under ``ctx.deps.headless_durable_waits`` a **read-only**
tool runs inline (the gate would auto-allow it anyway, ADR-0003 §2) while a **mutating** tool defers
— the adapter converts that ``ApprovalRequired`` into the durable approval wait. This applies the
gate's read-only-allow floor at the tool, since no loop will. Interactive runs leave the flag
``False`` and keep the mode-binary behaviour byte-for-byte.
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
        # Lazy import: ``decode.tools`` pulls in the registry, which imports this module — a
        # module-level import would cycle. By call time the package is fully loaded.
        from decode.tools import tool_kind

        return tool_kind(ctx.tool_name or "") is not ToolKind.READ_ONLY
    return True
