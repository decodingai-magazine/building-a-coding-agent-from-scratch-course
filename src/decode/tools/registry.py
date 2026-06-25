"""The flat tool registry — the one place tools are declared and wired (ADR-0002 §7, ADR-0003 §2).

M1 keeps tooling deliberately flat: **no plugin machinery, no discovery, no MCP** (that is
M12). The registry is a plain list of :class:`ToolSpec` — one per tool — and does exactly two
jobs:

* :func:`register_tools` registers every spec's function on the :class:`~pydantic_ai.Agent`
  (the factory calls this instead of hand-registering each tool);
* :data:`TOOL_KIND` is *derived from the same list*, so a tool's
  :class:`~decode.permissions.types.ToolKind` is declared in exactly one place. The loop reads it
  through :func:`decode.tools.tool_kind` when it builds a
  :class:`~decode.entities.permissions.PermissionRequest`.

Tools land here as they are built (006-011). Every *gated* tool raises
:class:`pydantic_ai.ApprovalRequired` when ``not ctx.tool_call_approved`` so the run resolves to
``DeferredToolRequests`` and the loop can route the call through the gate; the gate then decides
allow/ask/deny by mode x the tool's ``kind`` (ADR-0003 §1) — read-only tools auto-allow.

The **ungated** tools never raise ``ApprovalRequired`` and so never reach the permission gate (the
gate path is only reached by a tool that actually raised it; their ``kind`` is ``OTHER`` but is
never consulted): ``ask_user`` (task 011), which IS the human-interaction tool — gating it ("may I
ask you a question?") would double-prompt — the orchestration controls ``enter_plan_mode`` /
``exit_plan_mode`` / ``sleep`` (task 021 / ADR-0003 §8), which touch no filesystem and only steer
the session (mode flips, a bounded ``sleep``), and the ``skill`` dispatcher (task 026 / ADR-0004 §7),
which only returns a skill's instruction body — the gated ``bash`` / ``write`` / ``edit`` calls that
body *induces* are what the gate still governs. ``exit_plan_mode`` rides the same single decision
channel ``ask_user`` uses for its plan-approval HITL.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.tools import ToolDefinition

from decode.agent.deps import AgentDeps
from decode.permissions.types import ToolKind
from decode.tools import askuser as askuser_module
from decode.tools import bash as bash_module
from decode.tools import files
from decode.tools import orchestration as orchestration_module
from decode.tools import skills as skills_module
from decode.tools import sleep as sleep_module
from decode.tools import tasks as tasks_module
from decode.tools import web as web_module

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One registered tool: ``name``, ``func``, and its :class:`~decode.permissions.types.ToolKind`.

    ``kind`` is the tool's permission classification (ADR-0003 §2): the gate evaluates it against
    the active mode to decide allow/ask/deny. ``func`` is the bare tool function (it takes ``ctx``
    as its first parameter) — Pydantic AI builds the model-facing schema from its signature and
    docstring.
    """

    name: str
    func: Callable[..., object]
    kind: ToolKind


# The flat catalogue. Source of truth for registration and the tool-kind map.
#
# The scaffolding ``noop`` tool (task 005) is deliberately ABSENT: it was the stand-in that made
# the permission-gate-via-deferred-tools path real *before* the real tools existed, and 006-011
# fully superseded it. It is no longer in this package — it survives only as a TEST-ONLY helper
# (``support.noop_helper.register_noop``) for the permission/loop tests that want a minimal
# one-gated-tool agent; the production agent never exposes it (AGENTS.md: remove scaffolding once
# the real thing lands; no abstraction without a second concrete caller).
TOOL_SPECS: list[ToolSpec] = [
    # The read-only file tools (task 006): no disk/exec side effect → auto-allowed by the gate.
    ToolSpec(name=files.READ_TOOL_NAME, func=files.read, kind=ToolKind.READ_ONLY),
    ToolSpec(name=files.GLOB_TOOL_NAME, func=files.glob, kind=ToolKind.READ_ONLY),
    ToolSpec(name=files.GREP_TOOL_NAME, func=files.grep, kind=ToolKind.READ_ONLY),
    # The mutating file tools (task 007): FILE_EDIT — edit mode auto-allows them, default asks.
    ToolSpec(name=files.WRITE_TOOL_NAME, func=files.write, kind=ToolKind.FILE_EDIT),
    ToolSpec(name=files.EDIT_TOOL_NAME, func=files.edit, kind=ToolKind.FILE_EDIT),
    # Bash (task 008): shell execution behind the executor seam → OTHER (edit mode still asks).
    ToolSpec(name=bash_module.BASH_TOOL_NAME, func=bash_module.bash, kind=ToolKind.OTHER),
    # Tasks (task 009): in-memory TodoWrite checklist, no disk/exec side effect → READ_ONLY
    # (ADR-0003 §2), so it works in plan mode and never prompts.
    ToolSpec(
        name=tasks_module.TODO_WRITE_TOOL_NAME,
        func=tasks_module.todo_write,
        kind=ToolKind.READ_ONLY,
    ),
    # Web (task 010): httpx GET → HTML-to-Markdown. No local side effect (network egress only) →
    # READ_ONLY, so the gate auto-allows it.
    ToolSpec(
        name=web_module.WEB_FETCH_TOOL_NAME,
        func=web_module.web_fetch,
        kind=ToolKind.READ_ONLY,
    ),
    # AskUser (task 011): the one blocking tool. NOT gated — it IS the human-interaction tool, so
    # routing it through the permission gate would double-prompt. It never raises ApprovalRequired
    # and so never reaches the gate; its kind is OTHER but never consulted.
    ToolSpec(
        name=askuser_module.ASK_USER_TOOL_NAME,
        func=askuser_module.ask_user,
        kind=ToolKind.OTHER,
    ),
    # Orchestration + sleep (task 021 / ADR-0003 §8): control signals that touch no filesystem.
    # UNGATED like ask_user — they never raise ApprovalRequired and so never reach the gate (gating
    # a control signal would block it in plan mode or double-prompt the exit_plan_mode HITL). Their
    # kind is OTHER but, like ask_user's, is never consulted. enter/exit_plan_mode flip the gate
    # mode; exit_plan_mode rides the same Decision Channel as ask_user for its approval.
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
    # The skill dispatcher (task 026 / ADR-0004 §7): returns a skill's instruction body on demand.
    # UNGATED like ask_user / the orchestration controls — it never raises ApprovalRequired and so
    # never reaches the gate (loading instructions is harmless; its kind is OTHER but never
    # consulted). The gated bash/write/edit calls the returned body INDUCES are what the gate still
    # governs — e.g. the commit skill's git commit rides the gated bash tool (default asks, plan denies).
    ToolSpec(
        name=skills_module.SKILL_TOOL_NAME,
        func=skills_module.skill,
        kind=ToolKind.OTHER,
    ),
]

# Each tool's kind, derived from TOOL_SPECS (single source of truth). Consulted by the loop via
# decode.tools.tool_kind; unknown tools default to OTHER (mutating → gated/asked).
TOOL_KIND: dict[str, ToolKind] = {spec.name: spec.kind for spec in TOOL_SPECS}


def register_tools(agent: Agent[AgentDeps, str | DeferredToolRequests]) -> None:
    """Register every :data:`TOOL_SPECS` function on ``agent`` with per-agent restriction (ADR-0003 §6).

    Called once by the factory. Each tool takes ``ctx: RunContext[AgentDeps]`` first, so it is
    registered with ``agent.tool`` (context-aware), not ``agent.tool_plain``. Each is registered
    with a per-tool ``prepare=`` callback (:func:`_restrict_to_active_agent`) that hides the tool —
    returns ``None`` so it is absent from the model's schema **for that run** — when it is not in
    ``ctx.deps.active_agent.tools``. One Agent, no rebuild: switching the active agent changes the
    visible tool set on the next turn (spike-confirmed against pydantic-ai 1.107).
    """
    for spec in TOOL_SPECS:
        agent.tool(spec.func, prepare=_restrict_to_active_agent(spec.name))
    logger.debug("registered %d tools: %s", len(TOOL_SPECS), [s.name for s in TOOL_SPECS])


def _restrict_to_active_agent(
    tool_name: str,
) -> Callable[[RunContext[AgentDeps], ToolDefinition], object]:
    """Build the per-tool ``prepare=`` callback hiding ``tool_name`` when the agent disallows it.

    Pydantic AI 1.107's per-tool ``prepare`` is
    ``Callable[[RunContext[Deps], ToolDefinition], Awaitable[ToolDefinition | None]]`` (verified
    against the installed SDK): it runs at schema-build time per run, receives ``ctx.deps``, and a
    ``None`` return drops the tool from the model-facing schema for that run. Returning the
    unchanged ``tool_def`` keeps the tool visible. Reading ``ctx.deps.active_agent.tools`` here is
    what makes the active agent's allowlist take effect per turn with no rebuild (ADR-0003 §6).
    """

    async def prepare(
        ctx: RunContext[AgentDeps], tool_def: ToolDefinition
    ) -> ToolDefinition | None:
        if tool_name in ctx.deps.active_agent.tools:
            return tool_def
        return None

    return prepare
