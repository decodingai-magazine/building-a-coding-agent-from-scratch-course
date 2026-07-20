"""Span cost for Opik — the one attribute its OTLP ingestion reads (ADR-0014 §8).

Opik prices a span server-side by looking up ``(provider, model)`` in its own table, and that table
covers none of decode's three providers reliably: it has no ``openrouter`` row at all, and a
self-hosted Modal endpoint (``Qwen/Qwen3.6-35B-A3B-FP8``) can never have one. Its ONLY other input is
an explicit ``gen_ai.usage.cost`` attribute, which short-circuits the lookup — including for a model
it has never heard of.

pydantic-ai already computes the number for anything the genai-prices catalog knows (public Gemini
and OpenRouter models), but publishes it as ``operation.cost``, a key Opik does not read. So the two
halves here are: forward that value under the key Opik DOES read, and, when the catalog had no row,
fall back to the configured per-million-token rates. No rates configured → no cost attribute, because
a wrong number in a cost dashboard is worse than a blank one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from decode.config.settings import settings

# What pydantic-ai writes when genai-prices could price the response (models/instrumented.py), and
# what Opik reads instead. Neither key is ours to rename — both are external contracts.
PYDANTIC_AI_COST_ATTRIBUTE = "operation.cost"
OPIK_COST_ATTRIBUTE = "gen_ai.usage.cost"

INPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.output_tokens"

# The model-span marker, and the reason this gate exists at all: pydantic-ai ALSO puts the run's
# aggregated usage on the parent ``agent run`` span, and Opik sums span costs into the trace total —
# pricing both would report every run at double its real cost. Only the per-call ``chat`` spans carry
# a request model, which is the same signal Opik uses to type a span as ``llm``.
REQUEST_MODEL_ATTRIBUTE = "gen_ai.request.model"

_TOKENS_PER_MILLION = 1_000_000


def span_cost_usd(attributes: Mapping[str, Any]) -> float | None:
    """The USD cost for one model span, or ``None`` when it cannot be priced honestly.

    Priority: the catalog price pydantic-ai already computed, then the configured rates over the
    token counts on the span. ``None`` covers every ambiguous case — a span that is not a model call,
    a priced-at-zero response, unset rates — and the caller then adds no attribute, leaving Opik's
    own lookup untouched.
    """
    if REQUEST_MODEL_ATTRIBUTE not in attributes:
        return None

    catalog_cost = attributes.get(PYDANTIC_AI_COST_ATTRIBUTE)
    if isinstance(catalog_cost, int | float) and not isinstance(catalog_cost, bool):
        return float(catalog_cost)

    input_rate = settings.llm_cost_input_usd_per_mtok
    output_rate = settings.llm_cost_output_usd_per_mtok
    if not input_rate and not output_rate:
        return None

    input_tokens = _token_count(attributes.get(INPUT_TOKENS_ATTRIBUTE))
    output_tokens = _token_count(attributes.get(OUTPUT_TOKENS_ATTRIBUTE))
    if not input_tokens and not output_tokens:
        # No usage means this is not a model span (or the provider reported none) — never price it.
        return None
    return (input_tokens * input_rate + output_tokens * output_rate) / _TOKENS_PER_MILLION


def _token_count(value: Any) -> int:
    """A usage attribute as a non-negative int — anything else reads as zero tokens."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)
