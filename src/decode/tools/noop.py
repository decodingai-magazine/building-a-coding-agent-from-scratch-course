"""The trivial gated ``noop`` tool — a **TEST-ONLY** helper (ADR-0002 §3,7).

``noop`` was the single tool task 005 shipped to make the permission-gate-via-deferred-tools
path real end to end *before any real tool existed* (the real file/bash/web tools are 006-011,
which fully superseded it). It does nothing but echo its ``text`` argument — its job is to be
*gated*: it raises :class:`pydantic_ai.ApprovalRequired` whenever the run has not been approved
(``not ctx.tool_call_approved``). That is what makes a leg resolve to
:class:`~pydantic_ai.DeferredToolRequests`, which the loop then routes through the gate and
the human resolver; on the resume leg the same tool runs with ``tool_call_approved=True`` and
returns its echo (or the framework returns the denial message to the model instead).

``noop`` is **not** in the production tool set — the flat :mod:`decode.tools.registry` does
**not** register it, so the live Gemini agent never exposes it (AGENTS.md: remove scaffolding
once the real thing lands). It survives only because :func:`register_noop` builds a *minimal
one-gated-tool* agent that the permission / loop / e2e tests drive in isolation, without the
read-only file tools — those tests register it explicitly on their own test agent.
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
