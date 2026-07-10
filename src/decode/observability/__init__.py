"""Observability — decode's Opik tracing surface (ADR-0014).

Presence-based via ``OPIK_API_KEY``: a silent no-op when unset, so decode is byte-identical without
a key. Re-exports the public surface so callers write ``observability.init_tracing()``. Importing
this package is cheap and side-effect-free — nothing is configured until :func:`init_tracing` runs.
"""

from __future__ import annotations

from decode.observability.tracing import (
    init_tracing,
    is_tracing_active,
    record_output,
    reset_tracing,
    root_span,
)

__all__ = ["init_tracing", "is_tracing_active", "record_output", "reset_tracing", "root_span"]
