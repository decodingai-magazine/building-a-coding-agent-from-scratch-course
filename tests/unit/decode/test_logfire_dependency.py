"""Smoke test that the ``logfire`` runtime dependency resolves and brings the OTLP exporter (ADR-0014).

Task 091 added ``logfire`` as the ONLY new top-level dependency for Opik observability; it transitively
brings ``opentelemetry-exporter-otlp-proto-http`` + ``opentelemetry-sdk`` — the ``OTLPSpanExporter`` +
``BatchSpanProcessor`` that ``observability.init_tracing`` wires to Opik. If a future resolver change
drops one of these (or the logfire integration surface moves), this fails loudly here instead of deep
inside the tracing seam.
"""

import importlib


def test_logfire_is_importable():
    module = importlib.import_module("logfire")

    assert module is not None
    assert hasattr(module, "configure")
    assert hasattr(module, "instrument_pydantic_ai")


def test_logfire_brings_the_otlp_exporter_and_batch_processor():
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    assert callable(OTLPSpanExporter)
    assert callable(BatchSpanProcessor)
