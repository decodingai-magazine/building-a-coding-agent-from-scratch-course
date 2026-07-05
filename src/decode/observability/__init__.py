"""Observability — decode's Opik tracing surface (ADR-0014, step 10 / M10).

Monitoring, not evaluation: one small module (:mod:`decode.observability.tracing`) wires the official
logfire integration to Opik over OTLP so every turn (REPL) and run (``decode run``) becomes a Trace —
each LLM + tool call with inputs/outputs, latency, tokens, and (for priced models) cost. Presence-based
via ``OPIK_API_KEY``: a silent no-op when unset, so decode is byte-identical without a key. Evals /
experiments are M13, built on top of these traces.

Re-exports the four-function public surface so callers write ``observability.init_tracing()``
(ADR-0014 §5). Importing this package pulls in logfire + the OpenTelemetry OTLP exporter — cheap and
side-effect-free (nothing is configured until :func:`init_tracing` is called with a key).
"""

from __future__ import annotations

from decode.observability.tracing import (
    init_tracing,
    is_tracing_active,
    reset_tracing,
    root_span,
)

__all__ = ["init_tracing", "is_tracing_active", "reset_tracing", "root_span"]
