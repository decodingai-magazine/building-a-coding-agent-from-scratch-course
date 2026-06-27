"""LSP Service — decode's hand-rolled JSON-RPC-over-stdio Code Intelligence client (ADR-0007).

The first concrete entry behind the Services Interface (:mod:`decode.services`). It spawns one stdio
Language Server (``ty server`` by default, swappable via ``lsp_server_command`` / ``lsp_server_args``)
per project root — lazily, cached, best-effort — and exposes four Code Intelligence ops plus the
enricher's sync bridge and the app-exit shutdown:

* :func:`~decode.services.lsp.service.definition` / :func:`~decode.services.lsp.service.references` /
  :func:`~decode.services.lsp.service.hover` / :func:`~decode.services.lsp.service.diagnostics` — async
  ops returning a decode-native :class:`~decode.services.lsp.types.Location` /
  :class:`~decode.services.lsp.types.Diagnostic` (1-based), ``None`` for "found nothing", or
  :data:`~decode.services.lsp.types.UNAVAILABLE` when the server can't answer.
* :func:`~decode.services.lsp.service.diagnostics_on_edit` — the **sync** best-effort helper the
  Diagnostics Enricher (task 053) calls from the worker thread.
* :func:`~decode.services.lsp.service.shutdown_all` — the async app-exit entry (task 054).
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
