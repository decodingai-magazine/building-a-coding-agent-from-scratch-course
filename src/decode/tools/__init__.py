"""Tools the model calls (flat registry; ADR-0002 §7, ADR-0003 §2).

The tool catalogue lives in :mod:`decode.tools.registry` as a flat list of specs — one per
tool — with no plugin machinery (MCP is M12). M1's production tools are ``read`` / ``glob`` /
``grep`` / ``write`` / ``edit`` / ``bash`` / ``todo_write`` / ``web_fetch`` / ``ask_user``:

* :mod:`decode.tools.files` — the file tools ``read`` / ``glob`` / ``grep`` (read-only) plus
  the gated ``write`` / ``edit`` (task 006-007), backed by the shared
  :mod:`decode.tools.truncate` output cap.

The task-005 gated ``noop`` echo is **not** part of this package — it was scaffolding for the
permission path before any real tool existed and now lives only as a TEST-ONLY helper under
``tests/support`` (``support.noop_helper.register_noop``); production never registers it.

Every gated tool raises :class:`pydantic_ai.ApprovalRequired` when its run context has not been
approved, which makes the Pydantic AI run resolve to ``DeferredToolRequests`` so the loop can
route the call through the gate. The loop looks up each gated call's
:class:`~decode.permissions.types.ToolKind` via :func:`tool_kind` (default ``OTHER`` — mutating)
when building the :class:`~decode.entities.permissions.PermissionRequest`; the gate then decides
allow/ask/deny by mode x kind (ADR-0003 §1) — read-only tools auto-allow.
"""

from __future__ import annotations

from decode.permissions.types import ToolKind
from decode.tools.orchestration import ORCHESTRATION_TOOL_NAMES
from decode.tools.registry import TOOL_KIND

# Every tool name an agent's catalog allowlist may reference (ADR-0003 §5): the registered tools
# (``TOOL_KIND`` keys, which since task 021 include the ungated ``enter_plan_mode`` /
# ``exit_plan_mode`` / ``sleep``) unioned with ``ORCHESTRATION_TOOL_NAMES``. The union is now
# belt-and-suspenders — the orchestration names are registered specs — but it keeps validation
# honest even if a name is ever declared before its spec lands. The agents-catalog loader validates
# each ``tools`` entry against this set, so an unknown tool fails loudly regardless of task ordering.
KNOWN_TOOL_NAMES: frozenset[str] = frozenset(TOOL_KIND) | ORCHESTRATION_TOOL_NAMES

__all__ = ["KNOWN_TOOL_NAMES", "TOOL_KIND", "tool_kind"]


def tool_kind(tool_name: str) -> ToolKind:
    """The registered :class:`~decode.permissions.types.ToolKind` of ``tool_name``.

    Unknown tools default to ``OTHER`` (treat as mutating → gated/asked).
    """
    return TOOL_KIND.get(tool_name, ToolKind.OTHER)
