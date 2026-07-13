"""The flat tool registry — the one place tools are declared and wired.

A plain list of :class:`ToolSpec` (no plugin machinery): :func:`register_tools` registers each
spec's function on the Agent, and :data:`TOOL_KIND` is derived from the same list so a tool's
kind is declared exactly once. Gated tools raise :class:`pydantic_ai.ApprovalRequired` so the
loop routes them through the gate; the ungated tools (``ask_user``, the orchestration controls,
``skill``) never raise it and never reach the gate. See ADR-0002 §7, ADR-0003 §2,§8, ADR-0004 §7.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.tools import ToolDefinition

from decode.agent.deps import AgentDeps
from decode.permissions.types import ToolKind
from decode.tools import agent as agent_module
from decode.tools import askuser as askuser_module
from decode.tools import bash as bash_module
from decode.tools import files
from decode.tools import lsp as lsp_module
from decode.tools import orchestration as orchestration_module
from decode.tools import skills as skills_module
from decode.tools import sleep as sleep_module
from decode.tools import tasks as tasks_module
from decode.tools import web as web_module

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One registered tool: ``name``, ``func``, its :class:`~decode.permissions.types.ToolKind`, ``retries``.

    ``kind`` is the tool's permission classification (ADR-0003 §2): the gate evaluates it against
    the active mode to decide allow/ask/deny. ``func`` is the bare tool function (it takes ``ctx``
    as its first parameter) — Pydantic AI builds the model-facing schema from its signature and
    docstring. ``retries`` is the per-tool ``ModelRetry`` budget: ``None`` = the Agent default (1),
    raised only by a tool that *coaches* the model with ``ModelRetry`` (``agent``, ADR-0017 §3).
    """

    name: str
    func: Callable[..., object]
    kind: ToolKind
    retries: int | None = None


# The flat catalogue. Source of truth for registration and the tool-kind map.
TOOL_SPECS: list[ToolSpec] = [
    # Read-only file tools: no disk/exec side effect → auto-allowed by the gate.
    ToolSpec(name=files.READ_TOOL_NAME, func=files.read, kind=ToolKind.READ_ONLY),
    ToolSpec(name=files.GLOB_TOOL_NAME, func=files.glob, kind=ToolKind.READ_ONLY),
    ToolSpec(name=files.GREP_TOOL_NAME, func=files.grep, kind=ToolKind.READ_ONLY),
    # Mutating file tools: FILE_EDIT — edit mode auto-allows them, default asks.
    ToolSpec(name=files.WRITE_TOOL_NAME, func=files.write, kind=ToolKind.FILE_EDIT),
    ToolSpec(name=files.EDIT_TOOL_NAME, func=files.edit, kind=ToolKind.FILE_EDIT),
    # Bash: shell execution behind the executor seam → OTHER (edit mode still asks).
    ToolSpec(name=bash_module.BASH_TOOL_NAME, func=bash_module.bash, kind=ToolKind.OTHER),
    # In-memory TodoWrite checklist: no disk/exec side effect → READ_ONLY (works in plan mode).
    ToolSpec(
        name=tasks_module.TODO_WRITE_TOOL_NAME,
        func=tasks_module.todo_write,
        kind=ToolKind.READ_ONLY,
    ),
    # Web: httpx GET → Markdown; network egress only, no local side effect → READ_ONLY.
    ToolSpec(
        name=web_module.WEB_FETCH_TOOL_NAME,
        func=web_module.web_fetch,
        kind=ToolKind.READ_ONLY,
    ),
    # LSP (ADR-0007): reads code intelligence only → READ_ONLY, auto-allowed in every mode.
    ToolSpec(
        name=lsp_module.LSP_TOOL_NAME,
        func=lsp_module.lsp,
        kind=ToolKind.READ_ONLY,
    ),
    # AskUser: the one blocking tool. NOT gated — gating the human-interaction tool would
    # double-prompt; it never raises ApprovalRequired (kind OTHER, never consulted).
    ToolSpec(
        name=askuser_module.ASK_USER_TOOL_NAME,
        func=askuser_module.ask_user,
        kind=ToolKind.OTHER,
    ),
    # Orchestration + sleep (ADR-0003 §8): ungated control signals; kind OTHER, never consulted.
    ToolSpec(
        name=orchestration_module.ENTER_PLAN_MODE_TOOL_NAME,
        func=orchestration_module.enter_plan_mode,
        kind=ToolKind.OTHER,
    ),
    ToolSpec(
        name=orchestration_module.EXIT_PLAN_MODE_TOOL_NAME,
        func=orchestration_module.exit_plan_mode,
        kind=ToolKind.OTHER,
    ),
    ToolSpec(
        name=sleep_module.SLEEP_TOOL_NAME,
        func=sleep_module.sleep,
        kind=ToolKind.OTHER,
    ),
    # Skill dispatcher (ADR-0004 §7): ungated — it only returns instructions; the gated
    # bash/write/edit calls the body induces are what the gate still governs.
    ToolSpec(
        name=skills_module.SKILL_TOOL_NAME,
        func=skills_module.skill,
        kind=ToolKind.OTHER,
    ),
    # Agent tool (ADR-0013, ADR-0017): ONE call fans out N read-only Explore subagents in-process.
    # READ_ONLY → runs inline, auto-allowed; explore itself omits it (recursion default-deny). Its
    # structural guards nag via ``ModelRetry``, so it needs a budget above the default 1 — otherwise
    # two consecutive nags abort the run with ``UnexpectedModelBehavior`` (ADR-0017 §3).
    ToolSpec(
        name=agent_module.AGENT_TOOL_NAME,
        func=agent_module.agent,
        kind=ToolKind.READ_ONLY,
        retries=agent_module.AGENT_TOOL_RETRIES,
    ),
]

# Each tool's kind, derived from TOOL_SPECS; unknown tools default to OTHER (mutating → gated).
TOOL_KIND: dict[str, ToolKind] = {spec.name: spec.kind for spec in TOOL_SPECS}


def register_tools(agent: Agent[AgentDeps, str | DeferredToolRequests]) -> None:
    """Register every :data:`TOOL_SPECS` function on ``agent`` with per-agent restriction (ADR-0003 §6).

    Called once by the factory. Each tool takes ``ctx: RunContext[AgentDeps]`` first, so it is
    registered with ``agent.tool`` (context-aware), not ``agent.tool_plain``. Each is registered
    with a per-tool ``prepare=`` callback (:func:`_restrict_to_active_agent`) that hides the tool —
    returns ``None`` so it is absent from the model's schema **for that run** — when it is not in
    ``ctx.deps.active_agent.tools``. One Agent, no rebuild: switching the active agent changes the
    visible tool set on the next turn (spike-confirmed against pydantic-ai 1.107). ``retries=None``
    (every tool but ``agent``) leaves the Agent's default budget in place.
    """
    for spec in TOOL_SPECS:
        agent.tool(spec.func, prepare=_prepare_for(spec.name), retries=spec.retries)
    logger.debug("registered %d tools: %s", len(TOOL_SPECS), [s.name for s in TOOL_SPECS])


def _prepare_for(
    tool_name: str,
) -> Callable[[RunContext[AgentDeps], ToolDefinition], Awaitable[ToolDefinition | None]]:
    """Build the ``prepare=`` callback for ``tool_name`` — active-agent restriction, plus bash's description.

    Every tool gets :func:`_restrict_to_active_agent`; ``bash`` additionally gets its mode-specific
    description via :func:`decode.tools.bash.bash_description` (ADR-0011 §4). ``none`` mode returns the
    definition untouched; ``docker`` / ``modal`` return a :func:`dataclasses.replace` copy with the
    sandbox paragraph appended (``tool_def`` is rebuilt fresh per run, so composing from it is safe).
    """
    restrict = _restrict_to_active_agent(tool_name)
    if tool_name != bash_module.BASH_TOOL_NAME:
        return restrict

    async def prepare(
        ctx: RunContext[AgentDeps], tool_def: ToolDefinition
    ) -> ToolDefinition | None:
        prepared = await restrict(ctx, tool_def)
        if prepared is None:
            return None
        base = prepared.description or ""
        described = bash_module.bash_description(base)
        if described == base:
            return prepared  # none mode: no change → byte-identical (untouched definition)
        return dataclasses.replace(prepared, description=described)

    return prepare


def _restrict_to_active_agent(
    tool_name: str,
) -> Callable[[RunContext[AgentDeps], ToolDefinition], Awaitable[ToolDefinition | None]]:
    """Build the per-tool ``prepare=`` callback hiding ``tool_name`` when the agent disallows it.

    ``prepare`` runs at schema-build time per run; returning ``None`` drops the tool from the
    model-facing schema. Reading ``ctx.deps.active_agent.tools`` makes the active agent's
    allowlist take effect per turn with no rebuild (ADR-0003 §6).
    """

    async def prepare(
        ctx: RunContext[AgentDeps], tool_def: ToolDefinition
    ) -> ToolDefinition | None:
        if tool_name in ctx.deps.active_agent.tools:
            return tool_def
        return None

    return prepare
