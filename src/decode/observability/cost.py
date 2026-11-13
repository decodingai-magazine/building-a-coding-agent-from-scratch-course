"""Span cost annotations for Langfuse OTLP ingestion (ADR-0024).

pydantic-ai computes prices for models in its genai-prices catalog, but publishes the value as
``operation.cost``. Decode forwards it as ``gen_ai.usage.cost`` and, when the catalog has no row,
falls back to configured per-million-token rates. No rates configured means no cost attribute,
because a wrong number in a cost dashboard is worse than a blank one.

That fallback is for a PER-TOKEN model the catalog missed (an OpenRouter slug), not for the Modal
endpoint: self-hosted serving bills GPU-seconds, so pricing its tokens would report a number nobody
is charged. Left unconfigured — the default — a Modal trace carries tokens and no cost, which is the
honest answer (ADR-0014 §8).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from pydantic_ai.messages import ModelResponse

from decode.config.settings import settings

logger = logging.getLogger(__name__)

# What pydantic-ai writes when genai-prices could price the response (models/instrumented.py), and
# the GenAI cost attribute exported to Langfuse.
PYDANTIC_AI_COST_ATTRIBUTE = "operation.cost"
GEN_AI_COST_ATTRIBUTE = "gen_ai.usage.cost"

INPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.output_tokens"

# The model-span marker prevents double-counting: pydantic-ai also puts the run's aggregated usage
# on the parent ``agent run`` span. Only per-call ``chat`` spans carry a request model.
REQUEST_MODEL_ATTRIBUTE = "gen_ai.request.model"

_TOKENS_PER_MILLION = 1_000_000


def span_cost_usd(attributes: Mapping[str, Any]) -> float | None:
    """The USD cost for one model span, or ``None`` when it cannot be priced honestly.

    Priority: the catalog price pydantic-ai already computed, then the configured rates over the
    token counts on the span. ``None`` covers every ambiguous case — a span that is not a model call,
    a priced-at-zero response, or unset rates — and the caller then adds no cost attribute.
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


def run_cost_usd(responses: Sequence[ModelResponse]) -> float | None:
    """The USD cost of a whole run's model responses, or ``None`` when it cannot be priced honestly.

    The same honesty rule as :func:`span_cost_usd`, applied to the run's message history instead of
    to one exported span — this is what ``decode run --summary-json`` reports, and it must agree
    with the trace (ADR-0022 §1). Priority is identical: the catalog price pydantic-ai computes
    (``ModelResponse.cost()``, genai-prices), then the configured per-million rates over the run's
    own token totals, then ``None``.

    All-or-nothing on the catalog: if ANY response has no catalog row, the whole run falls back to
    the rates. Summing a catalog price for some responses and zero for the rest would silently
    understate the run, and a wrong number in a cost dashboard is worse than a blank one. A run that
    made no request at all costs ``0.0`` — nothing was spent, and that is not an unknown.
    """
    catalog_total = Decimal(0)
    input_tokens = 0
    output_tokens = 0
    catalog_priced_everything = True
    for response in responses:
        input_tokens += response.usage.input_tokens or 0
        output_tokens += response.usage.output_tokens or 0
        price = _catalog_price(response)
        if price is None:
            catalog_priced_everything = False
        else:
            catalog_total += price
    if catalog_priced_everything:
        return float(catalog_total)

    input_rate = settings.llm_cost_input_usd_per_mtok
    output_rate = settings.llm_cost_output_usd_per_mtok
    if not input_rate and not output_rate:
        return None
    return (input_tokens * input_rate + output_tokens * output_rate) / _TOKENS_PER_MILLION


def _catalog_price(response: ModelResponse) -> Decimal | None:
    """What genai-prices charges for ``response``, or ``None`` when its catalog has no row.

    ``ModelResponse.cost()`` asserts on a missing model name and raises ``LookupError`` for an
    unknown ``(provider, model)`` — both are "the catalog cannot price this", never a run failure,
    so every exception maps to ``None`` and the caller's fallback decides.
    """
    if not response.model_name:
        return None
    try:
        return response.cost().total_price
    except Exception:
        logger.debug("genai-prices could not price model %r", response.model_name, exc_info=True)
        return None
