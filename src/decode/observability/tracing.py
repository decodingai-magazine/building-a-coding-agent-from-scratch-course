"""Langfuse Cloud observability — the presence-based tracing init seam (ADR-0024).

One :func:`init_tracing` wires the logfire integration + a settings-driven OTLP exporter to Langfuse,
covering every pydantic-ai Agent in ONE global ``instrument_pydantic_ai`` call. With no Langfuse
credentials it is a silent no-op and decode is byte-identical. Export is configured programmatically
from settings, never via global ``OTEL_*`` env vars — kitaru→zenml ships its own OpenTelemetry SDK,
so polluting the global env could redirect its telemetry too.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any

import logfire
from langfuse import propagate_attributes
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from decode.config.settings import settings
from decode.observability.cost import GEN_AI_COST_ATTRIBUTE, span_cost_usd

logger = logging.getLogger(__name__)

_INGESTION_VERSION = "4"

# Idempotency flag: ``logfire.configure`` installs a PROCESS-GLOBAL TracerProvider, so a second
# configure would stack a second exporter. Cleared by :func:`reset_tracing` (test hermeticity).
_active = False


class CostAnnotatingExporter(SpanExporter):
    """Stamps ``gen_ai.usage.cost`` onto model spans on the way out (ADR-0014 §8).

    An EXPORTER wrapper, not a span processor, because a processor's ``on_end`` receives an immutable
    snapshot — the SDK builds a fresh :class:`ReadableSpan` at ``Span.end()``, so there is nothing
    left to mutate by then. Sitting in front of the OTLP exporter also means every model span is
    covered whatever produced it — streamed or not, main agent or subagent, REPL or headless flow —
    rather than only the call sites decode remembers to annotate.

    Rewriting is skipped unless there is a cost to add, so a non-model span (a tool call, the root
    span) is forwarded as the very same object.
    """

    def __init__(self, wrapped: SpanExporter) -> None:
        self._wrapped = wrapped

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._wrapped.export([_with_cost(span) for span in spans])

    def shutdown(self) -> None:
        self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._wrapped.force_flush(timeout_millis)


def _with_cost(span: ReadableSpan) -> ReadableSpan:
    """``span`` plus a cost attribute, or ``span`` itself when it cannot be priced.

    A span already carrying :data:`GEN_AI_COST_ATTRIBUTE` is left alone — whoever set it upstream is
    closer to the provider's own numbers than a rate table is.
    """
    attributes = span.attributes or {}
    if GEN_AI_COST_ATTRIBUTE in attributes:
        return span
    cost = span_cost_usd(attributes)
    if cost is None:
        return span
    return ReadableSpan(
        name=span.name,
        context=span.get_span_context(),
        parent=span.parent,
        resource=span.resource,
        attributes={**attributes, GEN_AI_COST_ATTRIBUTE: cost},
        events=span.events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def _preserve_langfuse_session_id(match: logfire.ScrubMatch) -> Any:
    if match.path in {
        ("attributes", "session.id"),
        ("attributes", "langfuse.session.id"),
    }:
        return match.value
    return None


def init_tracing() -> bool:
    """Configure Langfuse tracing when both keys are present; otherwise a silent no-op (ADR-0024).

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
    secret_key = settings.langfuse_secret_key.get_secret_value()
    public_key = settings.langfuse_public_key.get_secret_value()
    if not secret_key and not public_key:
        return False
    if not secret_key or not public_key:
        raise ValueError(
            "Langfuse tracing requires both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY."
        )
    base = settings.langfuse_base_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("LANGFUSE_BASE_URL must be an http(s) URL.")
    authorization = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    exporter = OTLPSpanExporter(
        endpoint=f"{base}/api/public/otel/v1/traces",
        headers={
            "Authorization": f"Basic {authorization}",
            "x-langfuse-ingestion-version": _INGESTION_VERSION,
        },
    )
    logfire.configure(
        send_to_logfire=False,
        # ``console=False`` is LOAD-BEARING, not tidiness: logfire's DEFAULT installs a
        # ``ShowParentsConsoleSpanExporter`` writing every span to STDOUT — the same stdout the TUI
        # pins its prompt over with ``patch_stdout()``. That flooded the REPL with a raw span trace
        # (timestamps, nesting, ``running tool: grep``), bypassing decode's event bus and renderer
        # entirely. ``send_to_logfire=False`` only disables CLOUD egress; it does not touch this.
        # Langfuse export rides ``additional_span_processors`` below.
        console=False,
        # The cost wrapper sits between the batcher and the OTLP exporter, so every span Langfuse
        # receives carries a cost whenever decode can price it (ADR-0014 §8).
        additional_span_processors=[BatchSpanProcessor(CostAnnotatingExporter(exporter))],
        # Langfuse uses ``session.id`` for trace grouping, but that key matches Logfire's default
        # ``session`` scrub pattern. Preserve only decode's opaque session identifier.
        scrubbing=logfire.ScrubbingOptions(callback=_preserve_langfuse_session_id),
    )
    logfire.instrument_pydantic_ai()
    _active = True
    logger.info("Langfuse tracing active — host=%s", base)
    return True


def is_tracing_active() -> bool:
    """Whether :func:`init_tracing` has configured tracing this process (ADR-0014 §5)."""
    return _active


def root_span(
    name: str,
    *,
    session_id: str | None = None,
    input: str | None = None,
    metadata: Mapping[str, str] | None = None,
) -> AbstractContextManager[Any]:
    """Open a root span named ``name`` when tracing is active, else a ``nullcontext`` (ADR-0024).

    The decode session id becomes a Langfuse session id. Langfuse's propagation helper applies
    session and metadata attributes to this span and its descendants. ``input`` and the paired
    :func:`record_output` use Langfuse's trace-level OTEL attributes.
    """
    if not _active:
        return nullcontext()
    attributes: dict[str, Any] = {}
    if isinstance(input, str) and input:
        attributes["langfuse.trace.input"] = input

    @contextmanager
    def span_context() -> Iterator[Any]:
        with (
            logfire.span(name, **attributes) as span,
            propagate_attributes(
                session_id=session_id,
                metadata=dict(metadata) if metadata else None,
                trace_name=name,
            ),
        ):
            if session_id:
                span.set_attribute("langfuse.session.id", session_id)
            yield span

    return span_context()


def record_output(span: Any, output: object) -> None:
    """Set the output attribute on a root span so Langfuse populates the trace output (ADR-0024).

    The mirror of ``root_span``'s ``input``. A no-op when ``span`` is ``None`` (tracing off) or
    ``output`` is not non-empty text, so call sites invoke it unconditionally.
    """
    if span is None or not isinstance(output, str) or not output:
        return
    span.set_attribute("langfuse.trace.output", output)


def reset_tracing() -> None:
    """Clear the module flag so a later :func:`init_tracing` re-drives — test hermeticity.

    Only drops the ``_active`` flag; it does NOT tear down the process-global logfire TracerProvider
    a prior configure installed.
    """
    global _active
    _active = False
