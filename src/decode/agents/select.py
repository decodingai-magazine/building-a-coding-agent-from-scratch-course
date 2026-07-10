"""Apply a selected Agent persona to the running session (ADR-0003 §7).

Selecting an Agent moves the three pieces of active state together so they never drift: the
persona on :class:`~decode.agent.deps.AgentDeps`, the gate's mode (reset to the agent's
default), and the gate's active-agent rule set. Called from the startup ``--agent`` flow and
the mid-session ``/agent`` slash command. No agent rebuild — the persona rides ``deps`` per
turn, so a switch takes effect on the next model request.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from decode.agents.loader import load_primary_agent
from decode.permissions.rules import RuleSet

if TYPE_CHECKING:
    from decode.agent.deps import AgentDeps
    from decode.entities.agent_def import AgentDef
    from decode.permissions.gate import PermissionGate

logger = logging.getLogger(__name__)


def select_agent(name: str, *, deps: AgentDeps, gate: PermissionGate) -> AgentDef:
    """Load the agent ``name`` and make it the active persona on ``deps`` + ``gate`` (ADR-0003 §7).

    Sets ``deps.active_agent``, resets the gate mode to the agent's default, and replaces the
    gate's active-agent rule set (merged as a union with the user rules — a deny from either
    source wins), so the prior agent's rules never linger. Raises :class:`ValueError` (listing
    the **primary** agents) when ``name`` is unknown or names a subagent (ADR-0013 §3); ``deps``
    / ``gate`` are left untouched on failure because the load precedes any mutation.
    """
    agent = load_primary_agent(name)
    deps.active_agent = agent
    gate.set_mode(agent.mode)
    gate.set_agent_rules(RuleSet(allow=list(agent.allow_rules), deny=list(agent.deny_rules)))
    logger.debug(
        "selected agent=%s mode=%s tools=%d allow=%d deny=%d",
        agent.name,
        agent.mode.value,
        len(agent.tools),
        len(agent.allow_rules),
        len(agent.deny_rules),
    )
    return agent
