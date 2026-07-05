"""Apply a selected Agent persona to the running session (ADR-0003 §7).

Selecting an Agent is the one place the three pieces of active state move together, so they never
drift apart: the persona on :class:`~decode.agent.deps.AgentDeps` (which the factory's instructions
hook + per-tool ``prepare=`` read each turn), the gate's **mode** (reset to the agent's default),
and the gate's **active-agent rule set** (the agent's catalog ``allow`` / ``deny`` rules, so e.g.
code-reviewer's ``bash(git *)`` auto-allows ``git diff``).

:func:`select_agent` is called from two surfaces: the startup ``--agent`` flow in
:func:`decode.tui.app.run_app` and (task 022) the mid-session ``/agent`` slash command. It loads the
agent by name from the catalog (raising :class:`ValueError` listing the available agents on an
unknown name — the CLI guard / slash command turns that into a friendly message), then mutates the
shared ``deps`` + ``gate`` in place. No agent rebuild: the persona rides ``deps`` per turn (ADR-0003
§7), so a switch takes effect on the next model request.
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

    Sets ``deps.active_agent`` (the prompt + tool allowlist the factory reads per turn), resets the
    gate's mode to the agent's default (``plan`` → ``PLAN``, ``build`` → ``DEFAULT``), and loads the
    agent's catalog ``allow`` / ``deny`` rules as the gate's active-agent rule source (merged as a
    union with the user rules — a deny from either source wins). Replacing the agent rule set means
    the prior agent's rules never linger across a switch. Returns the resolved
    :class:`~decode.entities.agent_def.AgentDef`. Raises :class:`ValueError` (listing the **primary**
    agents) when ``name`` is not a built-in *or* names a subagent (explore) — a subagent is spawnable
    only via the Agent tool, never selected as the main agent (ADR-0013 §3). The caller's ``deps`` /
    ``gate`` are left untouched on failure because the load happens before any mutation.
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
