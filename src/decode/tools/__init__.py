"""Tools the model calls (flat registry; ADR-0002 §7).

The tool catalogue lives in :mod:`decode.tools.registry` as a flat list of specs — one per
tool — with no plugin machinery (MCP is M12). M1's production tools are ``read`` / ``glob`` /
``grep`` / ``write`` / ``edit`` / ``bash`` / ``todo_write`` / ``web_fetch`` / ``ask_user``:

* :mod:`decode.tools.files` — the file tools ``read`` / ``glob`` / ``grep`` (read-only) plus
  the gated ``write`` / ``edit`` (task 006-007), backed by the shared
  :mod:`decode.tools.truncate` output cap.

:mod:`decode.tools.noop` (task 005's gated echo) is **not** in the production catalogue — it
was scaffolding for the permission path before any real tool existed and is kept only as a
TEST-ONLY helper (``register_noop``); the registry never registers it.

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
