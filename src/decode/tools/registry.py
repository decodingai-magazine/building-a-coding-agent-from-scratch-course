"""The flat tool registry — the one place tools are declared and wired (ADR-0002 §7).

M1 keeps tooling deliberately flat: **no plugin machinery, no discovery, no MCP** (that is
M12). The registry is a plain list of :class:`ToolSpec` — one per tool — and does exactly two
jobs:

* :func:`register_tools` registers every spec's function on the :class:`~pydantic_ai.Agent`
  (the factory calls this instead of hand-registering each tool);
* :data:`TOOL_READ_ONLY` is *derived from the same list*, so a tool's ``read_only`` flag is
  declared in exactly one place. The loop reads it through :func:`decode.tools.is_read_only`
  when it builds a :class:`~decode.entities.permissions.PermissionRequest`.

Tools land here as they are built (006-011). Every tool gates itself by raising
:class:`pydantic_ai.ApprovalRequired` when ``not ctx.tool_call_approved`` (v1 asks on every
call); the ``read_only`` flag is a *tag* for M3's future auto-allow, not a v1 behaviour change.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic_ai import Agent, DeferredToolRequests

from decode.agent.deps import AgentDeps
from decode.tools import bash as bash_module
from decode.tools import files, noop
from decode.tools import tasks as tasks_module
from decode.tools import web as web_module

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One registered tool: its model-facing ``name``, its ``func``, and its ``read_only`` tag.

    ``read_only`` is recorded for M3's read-only auto-allow; v1 asks on every call regardless.
    ``func`` is the bare tool function (it takes ``ctx`` as its first parameter) — Pydantic AI
    builds the model-facing schema from its signature and docstring.
    """

    name: str
    func: Callable[..., object]
    read_only: bool


# The flat catalogue. Source of truth for both registration and the read-only map.
TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(name=noop.NOOP_TOOL_NAME, func=noop.noop, read_only=noop.NOOP_READ_ONLY),
    ToolSpec(name=files.READ_TOOL_NAME, func=files.read, read_only=True),
    ToolSpec(name=files.GLOB_TOOL_NAME, func=files.glob, read_only=True),
    ToolSpec(name=files.GREP_TOOL_NAME, func=files.grep, read_only=True),
    # The mutating file tools (task 007): gated, NOT read-only, still asked on every call.
    ToolSpec(name=files.WRITE_TOOL_NAME, func=files.write, read_only=False),
    ToolSpec(name=files.EDIT_TOOL_NAME, func=files.edit, read_only=False),
    # Bash (task 008): gated shell execution behind the executor seam, NOT read-only.
    ToolSpec(name=bash_module.BASH_TOOL_NAME, func=bash_module.bash, read_only=False),
    # Tasks (task 009): in-memory TodoWrite; rewrites the per-run task store, NOT read-only.
    ToolSpec(
        name=tasks_module.TODO_WRITE_TOOL_NAME,
        func=tasks_module.todo_write,
        read_only=tasks_module.TODO_WRITE_READ_ONLY,
    ),
    # Web (task 010): httpx GET → HTML-to-Markdown. No local side effect (network egress), so
    # tagged read_only for M3's future auto-allow — but STILL asked on every call in v1.
    ToolSpec(
        name=web_module.WEB_FETCH_TOOL_NAME,
        func=web_module.web_fetch,
        read_only=web_module.WEB_FETCH_READ_ONLY,
    ),
]

# Each tool's read-only flag, derived from TOOL_SPECS (single source of truth). Consulted by
# the loop via decode.tools.is_read_only; unknown tools default to mutating (gated/asked).
TOOL_READ_ONLY: dict[str, bool] = {spec.name: spec.read_only for spec in TOOL_SPECS}


def register_tools(agent: Agent[AgentDeps, str | DeferredToolRequests]) -> None:
    """Register every :data:`TOOL_SPECS` function on ``agent`` (ADR-0002 §7).

    Called once by the factory. Each tool takes ``ctx: RunContext[AgentDeps]`` first, so it is
    registered with ``agent.tool`` (context-aware), not ``agent.tool_plain``.
    """
    for spec in TOOL_SPECS:
        agent.tool(spec.func)
    logger.debug("registered %d tools: %s", len(TOOL_SPECS), [s.name for s in TOOL_SPECS])
