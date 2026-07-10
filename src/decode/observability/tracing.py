"""Opik observability — the presence-based tracing init seam (ADR-0014).

decode's whole tracing surface: a single :func:`init_tracing` that, when ``settings.opik_api_key`` is
present, wires the official logfire integration + a settings-driven OTLP exporter to Opik — covering
every pydantic-ai Agent (the main loop, memory write-back, compaction, and subagents) in ONE global
:func:`logfire.instrument_pydantic_ai` call, with zero per-call-site code. When the key is absent it is
a **silent no-op** and decode is byte-identical (ADR-0014 §1, the presence-based enablement every prior
optional surface follows).

The public surface (ADR-0014 §5):

* :func:`init_tracing` — presence-based + idempotent; the seam 092/093 call from ``run_app`` and the
  headless ``@flow`` bodies. No call site is wired yet (this task is settings + module only).
* :func:`is_tracing_active` — cheap read of the module flag.
* :func:`root_span` — the ``logfire.span`` wrapper 092/093 open per turn / per run (a ``nullcontext``
  when tracing is off), carrying the Opik Thread id + the turn/run ``input``.
* :func:`record_output` — sets the paired ``output`` attribute on that root span at turn/run end, so
  Opik populates the trace's OUTPUT and the Thread view has messages to render.
* :func:`reset_tracing` — clears the module flag for test hermeticity (mirrors ``bash.reset_executor``
  / ``agent.reset_main_agent``).

Export is configured **programmatically from settings, never via global ``OTEL_*`` env vars**
(ADR-0014 §2): kitaru→zenml ships its own OpenTelemetry SDK, so polluting the global OTEL env could
redirect *its* telemetry too. The exporter is attached only to logfire's own tracer provider.
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

# The Comet-cloud OTLP base used when ``opik_url_override`` is unset (ADR-0014 §2). The exporter
# appends ``/v1/traces``; a self-hosted Opik overrides the whole base (e.g.
# ``http://localhost:5173/api/v1/private/otel``).
_CLOUD_OTLP_BASE = "https://www.comet.com/opik/api/v1/private/otel"

# Idempotency flag: ``logfire.configure`` installs a PROCESS-GLOBAL TracerProvider, so a second
# configure would stack a second exporter. Set True once :func:`init_tracing` configures; read by
# :func:`is_tracing_active` / :func:`root_span`; cleared by :func:`reset_tracing` (test hermeticity).
_active = False


def init_tracing() -> bool:
    """Configure Opik tracing when ``OPIK_API_KEY`` is present; a silent no-op otherwise (ADR-0014 §1-3).

    Presence-based + idempotent. Returns ``True`` when tracing is (already or newly) active, ``False``
    when the key is absent. With **no key** it builds nothing, configures nothing, emits no span, and
    mutates no ``os.environ`` — decode is byte-identical. With a key it builds an
    :class:`OTLPSpanExporter` (endpoint ``<base>/v1/traces`` + the three Opik headers from settings),
    attaches it via ``logfire.configure(send_to_logfire=False, additional_span_processors=[...])``, and
    turns on GLOBAL pydantic-ai instrumentation — one call covers the main loop, memory write-back,
    compaction, and subagents. No :class:`InstrumentationSettings` is passed: the pydantic-ai 1.95
    defaults (full content, format v5, aggregated-usage attribute names) are exactly right, and an older
    format version would emit a ``PydanticAIDeprecationWarning`` that ``filterwarnings=["error"]`` turns
    into a failure. Logs one INFO line (naming the project + cloud/self-hosted target) when it activates.
    """
    global _active
    if _active:
        # Idempotent: ``logfire.configure`` is process-global, so a repeat call must not re-configure.
        return True
    key = settings.opik_api_key.get_secret_value()
    if not key:
        return False
    # ``rstrip("/")`` the base so a trailing-slash ``opik_url_override`` (e.g. ".../otel/") can't
    # produce a ``//v1/traces`` double slash (pre-approved from the 091 review).
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
    """Open a root span named ``name`` when tracing is active, else a ``nullcontext`` (ADR-0014 §4,5).

    The thin wrapper 092/093 open around a REPL turn (``AgentTurnHandler.__call__``) or a headless run
    (the ``@flow`` body): when active it is ``logfire.span(name, thread_id=thread_id, input=input)`` —
    ``thread_id`` rides as a span attribute that Opik maps to a conversation Thread (the session id for
    the REPL, the Kitaru exec_id for a run). When tracing is off it is a no-op
    :func:`contextlib.nullcontext`, so the call sites open it unconditionally with no ``if`` at the caller.

    ``input`` (the turn prompt / the run task) is set as the span's ``input`` attribute so Opik's OTLP
    ingest buckets it into the **trace's** ``input`` field. This matters twice: the trace summary shows
    the user's message, and the Thread view — which Opik builds from **trace-level** input/output — has
    a message to render (without it a whole conversation renders as blank ``-`` rows). Opik's mapping is
    a prefix match on the attribute key (``input`` → INPUT, ``output`` → OUTPUT); the paired
    :func:`record_output` sets the ``output`` half at the end of the turn/run.
    """
    if not _active:
        return nullcontext()
    attributes: dict[str, Any] = {"thread_id": thread_id}
    if isinstance(input, str) and input:
        attributes["input"] = input
    return logfire.span(name, **attributes)


def record_output(span: Any, output: object) -> None:
    """Set the ``output`` attribute on a root ``span`` so Opik populates the **trace's** OUTPUT (ADR-0014 §4).

    The mirror of ``root_span``'s ``input``: Opik buckets any ``output``-prefixed span attribute into the
    trace OUTPUT field the Thread view reads, so the turn's/run's final assistant text shows on both the
    trace and its Thread message. A no-op when ``span`` is ``None`` (tracing off — ``root_span`` returned
    a ``nullcontext``) or ``output`` is not non-empty text, so call sites invoke it unconditionally with
    whatever they already hold. pydantic-ai's own spans (``agent run`` / ``chat …`` / ``running tool``)
    get their I/O for free from the global instrumentation; only these manually-opened roots need it set.
    """
    if span is None or not isinstance(output, str) or not output:
        return
    span.set_attribute("output", output)


def reset_tracing() -> None:
    """Clear the module flag so a later :func:`init_tracing` re-drives — test hermeticity (ADR-0014 §7).

    Mirrors ``bash.reset_executor`` / ``agent.reset_main_agent``: it only drops decode's ``_active``
    flag; it does NOT tear down the process-global logfire TracerProvider a prior configure installed.
    Span-asserting tests own real provider isolation via ``logfire.testing``'s in-memory exporter; the
    autouse ``_no_opik_tracing`` fixture blanks the key so ordinary tests never configure real export.
    """
    global _active
    _active = False
