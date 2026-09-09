"""Resolve the compaction context window for the run's **ACTIVE** model, probing the provider.

Why this is not in :mod:`decode.config.settings`: the ``settings`` singleton is built at IMPORT
time and its sources must never raise or do I/O, so a network probe cannot live in a validator —
it would put a provider call on ``--help`` and on every unit test. This module is the seam that
runs *later*, when the model is actually about to be used, and it knows the **Model Override**
(``--model``, ADR-0010 §2) that ``Settings`` structurally cannot.

Resolution order, first hit wins, and the winner is named in one log line:

``explicit COMPACTION_CONTEXT_WINDOW_TOKENS`` > provider probe > static table > 200,000 fallback

An explicit setting wins outright and performs **no** probe: the operator owns that number.

Probing rules. Every one of them exists to keep a best-effort optimisation from becoming a failure
mode: it never runs at import, never blocks past :data:`PROBE_TIMEOUT_S`, never raises (any
exception falls through to the table and then the fallback, as a DEBUG line), and runs at most once
per process per ``(provider, model id)`` — failures included, so an offline run pays one timeout,
not one per turn.

Deliberately SYNCHRONOUS. Callers are startup paths (the cli notice, deps construction) that are
not necessarily in an event loop, and a stray async client on such a path is an ``Event loop is
closed`` hazard. One blocking call bounded by a short timeout is the simpler correct thing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import httpx

from decode.config.settings import (
    UNKNOWN_MODEL_CONTEXT_WINDOW,
    Settings,
    context_window_for,
    settings,
)

logger = logging.getLogger(__name__)

# How long a probe may block before it loses to the static table. Short on purpose: a Modal endpoint
# may cold-start a GPU to answer ``GET /v1/models``, and waiting minutes at startup to *maybe*
# improve a number the table already approximates is a worse trade than falling back. The real
# inference call that follows will do the cold start anyway.
PROBE_TIMEOUT_S = 10.0

# The public OpenRouter model catalog — no auth, every model's ``context_length`` in one payload.
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Where the resolved window came from; ``fallback`` is the only value that means "we guessed".
WindowSource = Literal["explicit", "probe", "table", "fallback"]

# In-process memo of probe results, keyed by (provider, model id). ``None`` values are cached too:
# a probe that failed once (offline, 401, DNS) must not be retried on every resolution. Process
# scoped by design — on-disk caching across processes is deliberately out of scope.
_probe_cache: dict[tuple[str, str], int | None] = {}


@dataclass(frozen=True, slots=True)
class ContextWindow:
    """A resolved window plus the provenance the startup notice needs to tell the truth."""

    tokens: int
    source: WindowSource

    @property
    def is_assumed(self) -> bool:
        """True only when NEITHER the probe nor the table produced a number.

        The cli notice gates on this: claiming "assumed" after a successful probe would be a lie,
        and a warning that cries wolf is a warning operators learn to skip.
        """
        return self.source == "fallback"


def reset_probe_cache() -> None:
    """Drop the in-process probe memo. For tests — nothing in production re-resolves a model."""
    _probe_cache.clear()


def resolve_context_window(model: str | None = None, *, config: Settings | None = None) -> int:
    """The window in tokens for ``model`` (the Model Override) or the configured active model.

    The number-only front door for call sites that just need to divide by it; use
    :func:`resolve_context_window_detail` when the provenance matters.
    """
    return resolve_context_window_detail(model, config=config).tokens


def resolve_context_window_detail(
    model: str | None = None, *, config: Settings | None = None
) -> ContextWindow:
    """Resolve the window and say where it came from, walking the four-step order.

    ``model`` is the per-run Model Override; ``None`` means the configured ``active_model``. This is
    the whole reason the seam exists: ``decode run --model <smaller-window-id>`` must compact against
    that id's window, not against the window of the model in ``.env``.

    ``config`` defaults to the process ``settings`` singleton and exists so tests can hand in a real
    :class:`Settings` — "explicit wins" reads ``model_fields_set``, which only a real instance has.
    """
    cfg = config if config is not None else settings
    model_id = model or cfg.active_model

    # Step 1 — an operator-supplied number wins outright, and short-circuits BEFORE any probe.
    if "compaction_context_window_tokens" in cfg.model_fields_set:
        return _resolved(model_id, cfg.compaction_context_window_tokens, "explicit")

    # Step 2 — ask the provider. Best effort: None means "could not answer", never an error.
    probed = _probe_cached(cfg.llm_provider, model_id, cfg)
    if probed is not None:
        return _resolved(model_id, probed, "probe")

    # Step 3 — the offline static table.
    known = context_window_for(model_id)
    if known is not None:
        return _resolved(model_id, known, "table")

    # Step 4 — the conservative guess, and the only outcome the startup notice warns about.
    return _resolved(model_id, UNKNOWN_MODEL_CONTEXT_WINDOW, "fallback")


def _resolved(model_id: str, tokens: int, source: WindowSource) -> ContextWindow:
    """Log the winner, then return it — the "observable in a log line" half of the contract."""
    logger.info("context window for %r: %d tokens (source=%s)", model_id, tokens, source)
    return ContextWindow(tokens=tokens, source=source)


def _probe_cached(provider: str, model_id: str, cfg: Settings) -> int | None:
    """Memoised :func:`_probe`, keyed by ``(provider, model id)`` — one network hit per process."""
    key = (provider, model_id)
    if key in _probe_cache:
        return _probe_cache[key]
    result = _probe(provider, model_id, cfg)
    _probe_cache[key] = result
    return result


def _probe(provider: str, model_id: str, cfg: Settings) -> int | None:
    """Ask the active provider for ``model_id``'s max input window; ``None`` if it cannot say.

    The blanket ``except Exception`` is the point, not sloppiness: this is a best-effort
    optimisation over a fallback that already works, so *every* failure mode — offline, DNS, 401,
    timeout, a payload shape that changed — must degrade to the table rather than take down a run
    that has not even started. A traceback at startup would be strictly worse than a slightly wrong
    compaction threshold.
    """
    try:
        if provider == "modal":
            return _probe_openai_compatible(model_id, cfg)
        if provider == "gemini":
            return _probe_gemini(model_id, cfg)
        if provider == "openrouter":
            return _probe_openrouter(model_id)
    except Exception:
        logger.debug("context window probe failed for %r on %s", model_id, provider, exc_info=True)
        return None
    return None


def _probe_openai_compatible(model_id: str, cfg: Settings) -> int | None:
    """``GET {modal_endpoint_url}/v1/models`` → ``data[].max_model_len`` (vLLM behind Modal).

    Auth mirrors :func:`decode.agent.factory._build_model`: the dual ``Modal-Key`` / ``Modal-Secret``
    proxy-token headers when BOTH are set, no headers at all for an ``--unauthenticated`` endpoint.
    """
    base_url = cfg.modal_endpoint_url
    if not base_url:
        return None
    token_id = cfg.modal_proxy_token_id.get_secret_value()
    token_secret = cfg.modal_proxy_token_secret.get_secret_value()
    headers = (
        {"Modal-Key": token_id, "Modal-Secret": token_secret} if token_id and token_secret else None
    )
    response = httpx.get(
        f"{base_url}/v1/models", headers=headers, timeout=PROBE_TIMEOUT_S, follow_redirects=True
    )
    response.raise_for_status()
    entries = response.json()["data"]
    return _match_window(entries, model_id, id_key="id", window_key="max_model_len")


def _probe_openrouter(model_id: str) -> int | None:
    """``GET /api/v1/models`` → the matching ``data[].context_length``. Public, no auth."""
    response = httpx.get(_OPENROUTER_MODELS_URL, timeout=PROBE_TIMEOUT_S, follow_redirects=True)
    response.raise_for_status()
    entries = response.json()["data"]
    return _match_window(entries, model_id, id_key="id", window_key="context_length")


def _probe_gemini(model_id: str, cfg: Settings) -> int | None:
    """``client.models.get(model=…)`` → ``input_token_limit`` via the google-genai SDK.

    Imported lazily so this module stays cheap for the two providers that never touch it. Without a
    key there is nothing to ask, so skip rather than burn a timeout on a guaranteed 401.
    """
    api_key = cfg.gemini_api_key.get_secret_value()
    if not api_key:
        return None
    from google import genai

    client = genai.Client(api_key=api_key)
    limit = client.models.get(model=model_id).input_token_limit
    return _positive_int(limit)


def _match_window(entries: object, model_id: str, *, id_key: str, window_key: str) -> int | None:
    """Pick ``model_id``'s window out of a provider's model list, tolerating any payload shape.

    Exact id match first. The single-entry fallback covers the dedicated-endpoint case: a vLLM
    server behind Modal serves exactly ONE model, and its reported id routinely differs from the
    configured one by a vendor prefix or quantization suffix — with one row there is no ambiguity
    about which window is being reported. With several rows and no match we return ``None`` rather
    than guess, because guessing is the bug this whole feature fixes.
    """
    if not isinstance(entries, list) or not entries:
        return None
    rows = [entry for entry in entries if isinstance(entry, dict)]
    for entry in rows:
        if entry.get(id_key) == model_id:
            return _positive_int(entry.get(window_key))
    if len(rows) == 1:
        return _positive_int(rows[0].get(window_key))
    return None


def _positive_int(value: object) -> int | None:
    """Accept only a usable window. A ``0``/negative/``None``/non-numeric field is not an answer.

    Guards the division in the TUI gauge and the compaction trigger from a provider that reports a
    field it does not actually populate.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None
