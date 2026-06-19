"""The trivial gated ``noop`` tool (ADR-0002 §3,7).

``noop`` is the single tool task 005 ships to make the permission-gate-via-deferred-tools
path real end to end (the real file/bash/web tools are 006-011). It does nothing but echo
its ``text`` argument — its job is to be *gated*: it raises
:class:`pydantic_ai.ApprovalRequired` whenever the run has not been approved
(``not ctx.tool_call_approved``). That is what makes a leg resolve to
:class:`~pydantic_ai.DeferredToolRequests`, which the loop then routes through the gate and
the human resolver; on the resume leg the same tool runs with ``tool_call_approved=True`` and
returns its echo (or the framework returns the denial message to the model instead).

Registered on the agent by :func:`register_noop` (called from the factory).
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent, ApprovalRequired, RunContext

from decode.agent.deps import AgentDeps

logger = logging.getLogger(__name__)

NOOP_TOOL_NAME = "noop"
# noop stands in for a *mutating* tool (write/edit/bash), so it is gated and always asked.
NOOP_READ_ONLY = False


def noop(ctx: RunContext[AgentDeps], text: str) -> str:
    """Echo ``text`` — but only after the call has been approved (ADR-0002 §3).

    Raises :class:`pydantic_ai.ApprovalRequired` until the run context is approved, so the
    first leg defers to the gate; on the approved resume leg it returns ``"noop: <text>"``.
    """
    if not ctx.tool_call_approved:
        logger.debug("noop requires approval (text=%r)", text)
        raise ApprovalRequired
    logger.debug("noop approved, echoing (text=%r)", text)
    return f"noop: {text}"


def register_noop(agent: Agent[AgentDeps, object]) -> None:
    """Register :func:`noop` on ``agent`` as the one gated tool for task 005."""
    agent.tool(noop)
