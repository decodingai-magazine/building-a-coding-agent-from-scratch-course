"""LSP Service — decode's hand-rolled JSON-RPC-over-stdio Code Intelligence client (ADR-0007).

Spawns one stdio Language Server (``ty server`` by default) per project root — lazily, cached,
best-effort — and exposes the four Code Intelligence ops (definition / references / hover /
diagnostics), the enricher's sync bridge (``diagnostics_on_edit``), and ``shutdown_all``.
"""

from __future__ import annotations

from decode.services.lsp.service import (
    definition,
    diagnostics,
    diagnostics_on_edit,
    hover,
    references,
    shutdown_all,
)
from decode.services.lsp.types import UNAVAILABLE, Diagnostic, Location, Unavailable

__all__ = [
    "UNAVAILABLE",
    "Diagnostic",
    "Location",
    "Unavailable",
    "definition",
    "diagnostics",
    "diagnostics_on_edit",
    "hover",
    "references",
    "shutdown_all",
]
