"""Tools the model calls (flat registry; ADR-0002 §7).

Task 005 ships exactly **one** tool — :mod:`decode.tools.noop` — a trivial, gated echo tool
whose only purpose is to make the permission-gate-via-deferred-tools path real and testable.
The real tools (file I/O, bash, web, tasks, AskUser) land in tasks 006-011; this package is
intentionally near-empty until then.

A tool that should be gated raises :class:`pydantic_ai.ApprovalRequired` when its run context
has not been approved, which makes the Pydantic AI run resolve to ``DeferredToolRequests`` so
the loop can route the call through the gate. The loop looks up each gated call's
``read_only`` flag in :data:`decode.tools.TOOL_READ_ONLY` (default ``False`` — mutating) when
building the :class:`~decode.entities.permissions.PermissionRequest`.
"""

from __future__ import annotations

from decode.tools.noop import NOOP_READ_ONLY, NOOP_TOOL_NAME

# Registry of each tool's read-only flag, consulted by the loop when it builds a
# PermissionRequest for a deferred approval. Unknown tools default to mutating (gated/asked).
# Real tools register their flags here as they land (006+).
TOOL_READ_ONLY: dict[str, bool] = {
    NOOP_TOOL_NAME: NOOP_READ_ONLY,
}


def is_read_only(tool_name: str) -> bool:
    """Whether ``tool_name`` is registered read-only (default ``False`` — treat as mutating)."""
    return TOOL_READ_ONLY.get(tool_name, False)
