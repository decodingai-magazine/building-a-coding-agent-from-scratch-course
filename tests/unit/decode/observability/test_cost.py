"""Unit tests for the Opik span cost attribute (ADR-0014 §8).

Hermetic and keyless: :func:`span_cost_usd` is pure over an attribute mapping, and the exporter
wrapper is driven with hand-built :class:`ReadableSpan` objects into a recording fake — no OTLP
exporter, no network. The last test closes the loop against REAL pydantic-ai spans (scripted model,
logfire's in-memory exporter), which is what pins the double-count guard to what pydantic-ai emits.
"""

from __future__ import annotations

import logfire
import pytest
from logfire.testing import (
    capfire,  # noqa: F401 — imported so pytest registers the in-memory fixture
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from decode.config.settings import settings
from decode.observability.cost import (
    OPIK_COST_ATTRIBUTE,
    PYDANTIC_AI_COST_ATTRIBUTE,
    span_cost_usd,
)
from decode.observability.tracing import CostAnnotatingExporter


@pytest.fixture
def rates(monkeypatch):
    """Configure $1/Mtok in and $2/Mtok out — round numbers keep the expected cost readable."""
    monkeypatch.setattr(settings, "llm_cost_input_usd_per_mtok", 1.0, raising=False)
    monkeypatch.setattr(settings, "llm_cost_output_usd_per_mtok", 2.0, raising=False)


def _model_span_attributes(**overrides) -> dict:
    """The attributes pydantic-ai puts on a model (``chat``) span, before any cost bridging."""
    return {
        # An OpenRouter slug the genai-prices catalog does not know — the case the manual rates
        # exist for. (Modal's self-hosted endpoint is NOT that case: it bills GPU-seconds.)
        "gen_ai.request.model": "qwen/qwen3-235b-a22b",
        "gen_ai.system": "openrouter",
        "gen_ai.usage.input_tokens": 1_000_000,
        "gen_ai.usage.output_tokens": 500_000,
        **overrides,
    }


class _RecordingExporter(SpanExporter):
    """A fake downstream exporter that keeps whatever the wrapper forwarded."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []
        self.shutdown_calls = 0

    def export(self, spans) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def _span(name: str, attributes: dict) -> ReadableSpan:
    return ReadableSpan(name=name, attributes=attributes)


# --- span_cost_usd: what can and cannot be priced ---


def test_catalog_cost_from_pydantic_ai_wins_over_configured_rates(rates):
    """A model pydantic-ai could price (Gemini, public OpenRouter) keeps ITS number, not our rates."""
    attributes = _model_span_attributes(**{PYDANTIC_AI_COST_ATTRIBUTE: 0.0425})

    assert span_cost_usd(attributes) == 0.0425


def test_configured_rates_price_a_model_no_catalog_knows(rates):
    """An OpenRouter slug with no catalog row: the configured per-Mtok rates supply the cost."""
    # 1M input at $1 + 0.5M output at $2 = $2.00.
    assert span_cost_usd(_model_span_attributes()) == pytest.approx(2.0)


def test_unpriceable_span_reports_no_cost_when_no_rates_are_configured():
    """Rates left at 0.0 means "unknown" — decode reports nothing rather than inventing a cost."""
    assert span_cost_usd(_model_span_attributes()) is None


def test_the_aggregating_agent_run_span_is_never_priced(rates):
    """The double-count guard: pydantic-ai repeats the run's usage on the parent ``agent run`` span.

    That span carries no request model, and Opik SUMS span costs into the trace total — pricing it
    too would report every run at double its real spend.
    """
    agent_run_attributes = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.usage.input_tokens": 1_000_000,
        "gen_ai.usage.output_tokens": 500_000,
    }

    assert span_cost_usd(agent_run_attributes) is None


def test_a_span_without_usage_is_not_priced(rates):
    """A tool span or a model span the provider reported no usage for stays cost-free."""
    assert span_cost_usd({"gen_ai.request.model": "gemini-3.5-flash"}) is None


def test_only_the_configured_side_of_the_rates_is_required(rates, monkeypatch):
    """One rate set and the other zero still prices — an output-only rate is a legitimate config."""
    monkeypatch.setattr(settings, "llm_cost_input_usd_per_mtok", 0.0, raising=False)

    assert span_cost_usd(_model_span_attributes()) == pytest.approx(1.0)  # 0.5M output at $2


# --- CostAnnotatingExporter: what reaches Opik ---


def test_exporter_stamps_the_attribute_opik_reads(rates):
    """The whole point: the forwarded span carries ``gen_ai.usage.cost``, the only key Opik ingests."""
    downstream = _RecordingExporter()

    result = CostAnnotatingExporter(downstream).export(
        [_span("chat qwen3", _model_span_attributes())]
    )

    assert result is SpanExportResult.SUCCESS
    (exported,) = downstream.spans
    assert exported.attributes[OPIK_COST_ATTRIBUTE] == pytest.approx(2.0)
    # The original attributes ride along untouched — the span is annotated, not replaced.
    assert exported.attributes["gen_ai.usage.input_tokens"] == 1_000_000
    assert exported.name == "chat qwen3"


def test_exporter_forwards_an_unpriceable_span_unchanged():
    """No rates, no catalog price: the span goes out as the very same object, with no cost key."""
    downstream = _RecordingExporter()
    span = _span("chat qwen3", _model_span_attributes())

    CostAnnotatingExporter(downstream).export([span])

    assert downstream.spans == [span]
    assert OPIK_COST_ATTRIBUTE not in (span.attributes or {})


def test_exporter_never_overwrites_a_cost_someone_else_set(rates):
    """An upstream cost is closer to the provider's own number than a rate table — leave it alone."""
    downstream = _RecordingExporter()
    attributes = _model_span_attributes(**{OPIK_COST_ATTRIBUTE: 0.99})

    CostAnnotatingExporter(downstream).export([_span("chat qwen3", attributes)])

    (exported,) = downstream.spans
    assert exported.attributes[OPIK_COST_ATTRIBUTE] == 0.99


def test_exporter_delegates_shutdown_and_flush_to_the_wrapped_exporter():
    """The wrapper is transparent: the OTLP exporter underneath still gets its lifecycle calls."""
    downstream = _RecordingExporter()
    exporter = CostAnnotatingExporter(downstream)

    assert exporter.force_flush() is True
    exporter.shutdown()

    assert downstream.shutdown_calls == 1


# --- against REAL pydantic-ai spans, not hand-built attribute dicts ---


@pytest.fixture
def _restore_instrumentation():
    """Save/restore ``Agent._instrument_default`` so global instrumentation never leaks to later tests.

    ``logfire.instrument_pydantic_ai()`` mutates the process-global ``Agent._instrument_default``;
    without this restore every later test's agents would stay instrumented and start emitting spans
    (mirrors the isolation fixture in ``test_flow_tracing.py`` / ``test_observability_capstone.py``).
    """
    prior_instrument = Agent._instrument_default
    try:
        yield
    finally:
        Agent.instrument_all(prior_instrument)


async def test_a_real_agent_run_prices_the_model_span_and_only_the_model_span(
    rates,
    capfire,  # noqa: F811
    _restore_instrumentation,
):
    """The guard that matters, pinned to what pydantic-ai actually emits (no network, scripted model).

    A run produces both a ``chat`` span and an aggregating ``agent run`` span carrying the SAME token
    counts. Opik sums span costs, so exactly one of them may be priced — if a pydantic-ai upgrade ever
    starts putting a request model on the parent span, this test fails instead of the cost dashboard
    silently doubling.
    """
    logfire.instrument_pydantic_ai()
    await Agent(TestModel()).run("hi")
    downstream = _RecordingExporter()

    CostAnnotatingExporter(downstream).export(capfire.exporter.exported_spans)

    priced = {
        span.name: span.attributes[OPIK_COST_ATTRIBUTE]
        for span in downstream.spans
        if OPIK_COST_ATTRIBUTE in (span.attributes or {})
    }
    assert [name for name in priced] == ["chat test"], sorted(
        span.name for span in downstream.spans
    )
    assert priced["chat test"] > 0
