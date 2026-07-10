"""Tools the model calls (flat registry; ADR-0002 §7, ADR-0003 §2).

The catalogue lives in :mod:`decode.tools.registry` as a flat list of specs, one per tool.
Gated tools raise :class:`pydantic_ai.ApprovalRequired` until approved so the loop can route
the call through the gate; the loop looks up each call's kind via :func:`tool_kind` (default
``OTHER`` — mutating) and the gate decides allow/ask/deny by mode x kind (ADR-0003 §1).
"""

from __future__ import annotations

from decode.permissions.types import ToolKind
from decode.tools.orchestration import ORCHESTRATION_TOOL_NAMES
from decode.tools.registry import TOOL_KIND

# Every tool name an agent's catalog allowlist may reference (ADR-0003 §5). The union with
# ORCHESTRATION_TOOL_NAMES is belt-and-suspenders: it keeps allowlist validation honest even if
# a name is ever declared before its registry spec lands.
KNOWN_TOOL_NAMES: frozenset[str] = frozenset(TOOL_KIND) | ORCHESTRATION_TOOL_NAMES

__all__ = ["KNOWN_TOOL_NAMES", "TOOL_KIND", "tool_kind"]


def tool_kind(tool_name: str) -> ToolKind:
    """The registered :class:`~decode.permissions.types.ToolKind` of ``tool_name``.

    Unknown tools default to ``OTHER`` (treat as mutating → gated/asked).
    """
    return TOOL_KIND.get(tool_name, ToolKind.OTHER)
