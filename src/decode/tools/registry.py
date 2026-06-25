"""The flat tool registry — the one place tools are declared and wired (ADR-0002 §7, ADR-0003 §2).

M1 keeps tooling deliberately flat: **no plugin machinery, no discovery, no MCP** (that is
M12). The registry is a plain list of :class:`ToolSpec` — one per tool — and does exactly two
jobs:

* :func:`register_tools` registers every spec's function on the :class:`~pydantic_ai.Agent`
  (the factory calls this instead of hand-registering each tool);
* :data:`TOOL_KIND` is *derived from the same list*, so a tool's
  :class:`~decode.permissions.types.ToolKind` is declared in exactly one place. The loop reads it
  through :func:`decode.tools.tool_kind` when it builds a
  :class:`~decode.entities.permissions.PermissionRequest`. :data:`TOOL_READ_ONLY` is *also*
  derived (``kind is READ_ONLY``) so existing :func:`decode.tools.is_read_only` callers work.

Tools land here as they are built (006-011). Every *gated* tool raises
:class:`pydantic_ai.ApprovalRequired` when ``not ctx.tool_call_approved`` so the run resolves to
``DeferredToolRequests`` and the loop can route the call through the gate; the gate then decides
allow/ask/deny by mode x the tool's ``kind`` (ADR-0003 §1) — read-only tools auto-allow.

``ask_user`` (task 011) is the lone exception: it IS the human-interaction tool, so gating it
("may I ask you a question?") would double-prompt. It never raises ``ApprovalRequired`` and so
never reaches the permission gate; instead it blocks the turn on the human via the same single
decision channel the permission resolver uses (its ``kind`` is ``OTHER`` but is never consulted —
the gate path is only reached by a tool that actually raised ``ApprovalRequired``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic_ai import Agent, DeferredToolRequests

from decode.agent.deps import AgentDeps
from decode.permissions.types import ToolKind
from decode.tools import askuser as askuser_module
from decode.tools import bash as bash_module
from decode.tools import files
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
]

# Each tool's kind, derived from TOOL_SPECS (single source of truth). Consulted by the loop via
# decode.tools.tool_kind; unknown tools default to OTHER (mutating → gated/asked).
TOOL_KIND: dict[str, ToolKind] = {spec.name: spec.kind for spec in TOOL_SPECS}

# Read-only map, derived from the kinds (``kind is READ_ONLY``). Kept so existing
# decode.tools.is_read_only callers work unchanged.
TOOL_READ_ONLY: dict[str, bool] = {
    name: kind is ToolKind.READ_ONLY for name, kind in TOOL_KIND.items()
}


def register_tools(agent: Agent[AgentDeps, str | DeferredToolRequests]) -> None:
    """Register every :data:`TOOL_SPECS` function on ``agent`` (ADR-0002 §7).

    Called once by the factory. Each tool takes ``ctx: RunContext[AgentDeps]`` first, so it is
    registered with ``agent.tool`` (context-aware), not ``agent.tool_plain``.
    """
    for spec in TOOL_SPECS:
        agent.tool(spec.func)
    logger.debug("registered %d tools: %s", len(TOOL_SPECS), [s.name for s in TOOL_SPECS])
