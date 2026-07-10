"""Opik observability — the presence-based tracing init seam (ADR-0014).

One :func:`init_tracing` wires the logfire integration + a settings-driven OTLP exporter to Opik,
covering every pydantic-ai Agent in ONE global ``instrument_pydantic_ai`` call. With no
``OPIK_API_KEY`` it is a silent no-op and decode is byte-identical. Export is configured
programmatically from settings, never via global ``OTEL_*`` env vars — kitaru→zenml ships its own
OpenTelemetry SDK, so polluting the global env could redirect its telemetry too. See ADR-0014 §5.
"""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager, nullcontext
from typing import Any

import logfire
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from decode.config.settings import settings

logger = logging.getLogger(__name__)

# The Comet-cloud OTLP base used when ``opik_url_override`` is unset; the exporter appends
# ``/v1/traces``, a self-hosted Opik overrides the whole base (ADR-0014 §2).
_CLOUD_OTLP_BASE = "https://www.comet.com/opik/api/v1/private/otel"

# Idempotency flag: ``logfire.configure`` installs a PROCESS-GLOBAL TracerProvider, so a second
# configure would stack a second exporter. Cleared by :func:`reset_tracing` (test hermeticity).
_active = False


def init_tracing() -> bool:
    """Configure Opik tracing when ``OPIK_API_KEY`` is present; a silent no-op otherwise (ADR-0014 §1-3).

    Presence-based + idempotent; returns whether tracing is active. With no key it builds nothing,
    emits no span, and mutates no ``os.environ`` — decode is byte-identical. No
    :class:`InstrumentationSettings` is passed: the pydantic-ai defaults are exactly right, and an
    older format version would emit a deprecation warning that ``filterwarnings=["error"]`` turns
    into a failure. Logs one INFO line when it activates.
    """
    global _active
    if _active:
        # ``logfire.configure`` is process-global, so a repeat call must not re-configure.
        return True
    key = settings.opik_api_key.get_secret_value()
    if not key:
        return False
    # ``rstrip("/")`` so a trailing-slash ``opik_url_override`` can't produce a ``//v1/traces``.
    base = (settings.opik_url_override or _CLOUD_OTLP_BASE).rstrip("/")
    exporter = OTLPSpanExporter(
        endpoint=f"{base}/v1/traces",
        headers={
            "Authorization": key,
            "Comet-Workspace": settings.opik_workspace,
            "projectName": settings.opik_project_name,
        },
    )
    logfire.configure(
        send_to_logfire=False,
        additional_span_processors=[BatchSpanProcessor(exporter)],
    )
    logfire.instrument_pydantic_ai()
    _active = True
    target = "self-hosted" if settings.opik_url_override else "cloud"
    logger.info("Opik tracing active — project=%s target=%s", settings.opik_project_name, target)
    return True


def is_tracing_active() -> bool:
    """Whether :func:`init_tracing` has configured tracing this process (ADR-0014 §5)."""
    return _active


def root_span(
    name: str, *, thread_id: str | None = None, input: str | None = None
) -> AbstractContextManager[Any]:
    """Open a root span named ``name`` when tracing is active, else a ``nullcontext`` (ADR-0014 §4-5).

    ``thread_id`` rides as a span attribute Opik maps to a conversation Thread (session id for the
    REPL, Kitaru exec_id for a run). ``input`` is set so Opik buckets it into the **trace's** INPUT
    (a prefix match on the attribute key) — without it the Thread view renders blank rows. The
    paired :func:`record_output` sets the ``output`` half. Call sites open this unconditionally.
    """
    if not _active:
        return nullcontext()
    attributes: dict[str, Any] = {"thread_id": thread_id}
    if isinstance(input, str) and input:
        attributes["input"] = input
    return logfire.span(name, **attributes)


def record_output(span: Any, output: object) -> None:
    """Set the ``output`` attribute on a root ``span`` so Opik populates the **trace's** OUTPUT (ADR-0014 §4).

    The mirror of ``root_span``'s ``input``. A no-op when ``span`` is ``None`` (tracing off) or
    ``output`` is not non-empty text, so call sites invoke it unconditionally.
    """
    if span is None or not isinstance(output, str) or not output:
        return
    span.set_attribute("output", output)


def reset_tracing() -> None:
    """Clear the module flag so a later :func:`init_tracing` re-drives — test hermeticity (ADR-0014 §7).

    Only drops the ``_active`` flag; it does NOT tear down the process-global logfire TracerProvider
    a prior configure installed.
    """
    global _active
    _active = False
