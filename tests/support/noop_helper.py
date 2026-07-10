"""The trivial gated ``noop`` tool — a **TEST-ONLY** helper (ADR-0002 §3,7).

Echoes its ``text`` argument but raises :class:`pydantic_ai.ApprovalRequired` until the call is
approved, so a leg resolves to ``DeferredToolRequests`` and routes through the gate — a stand-in
for a mutating tool. Not part of the shipped package (the registry never registers it); the
permission / loop / e2e tests build a minimal one-gated-tool agent via :func:`register_noop`.
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
    """Register :func:`noop` on ``agent`` as the sole tool (minimal one-tool agent for tests).

    Production agents go through :mod:`decode.tools.registry`, which does **not** register
    ``noop``; this helper exists so a test can build an agent that has *only* the gated
    ``noop`` and nothing else.
    """
    agent.tool(noop)
