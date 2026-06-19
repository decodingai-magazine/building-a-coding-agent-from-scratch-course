"""Tools the model calls (flat registry; ADR-0002 §7).

The tool catalogue lives in :mod:`decode.tools.registry` as a flat list of specs — one per
tool — with no plugin machinery (MCP is M12). M1's tools:

* :mod:`decode.tools.noop` — the trivial gated echo tool (task 005) that makes the
  permission-gate-via-deferred-tools path real;
* :mod:`decode.tools.files` — the read-only file tools ``read`` / ``glob`` / ``grep`` (task
  006), backed by the shared :mod:`decode.tools.truncate` output cap.

Every tool gates itself by raising :class:`pydantic_ai.ApprovalRequired` when its run context
has not been approved, which makes the Pydantic AI run resolve to ``DeferredToolRequests`` so
the loop can route the call through the gate. The loop looks up each gated call's
``read_only`` flag via :func:`is_read_only` (default ``False`` — mutating) when building the
:class:`~decode.entities.permissions.PermissionRequest`. Read-only tools are *tagged* but, in
v1, still asked (M3 adds read-only auto-allow).
"""

from __future__ import annotations

from decode.tools.registry import TOOL_READ_ONLY

__all__ = ["TOOL_READ_ONLY", "is_read_only"]


def is_read_only(tool_name: str) -> bool:
    """Whether ``tool_name`` is registered read-only (default ``False`` — treat as mutating)."""
    return TOOL_READ_ONLY.get(tool_name, False)
