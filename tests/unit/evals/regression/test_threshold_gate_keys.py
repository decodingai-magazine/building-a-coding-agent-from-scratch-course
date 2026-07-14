"""The pre-merge threshold gate's key preflight is provider-aware, not a hardcoded copy (ADR-0017 §9).

Regression guard for the Blocker in task 122: the gate module
(``evals/regression/test_thresholds.py``) used to gate its own execution on a hardcoded
``REQUIRED_KEYS = ("GEMINI_API_KEY", "OPIK_API_KEY")`` read from ``os.environ`` — a third, divergent
copy of the provider-aware, settings-backed preflight. An openrouter/modal operator then ran
``make eval-regression``, the harness key guard passed, ``evals sync`` ran, and the gate pytest SKIPPED
demanding a ``GEMINI_API_KEY`` they neither had nor needed — ``make`` exiting 0 having gated nothing.

These offline tests pin the shared predicate the gate now uses (``eval_keys_missing``): it names the
active provider's key, and the gate wires that exact shared symbol so it cannot drift back into a
divergent copy. No Opik, no keys, no agent run — the ``settings`` singleton is patched in place.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from decode.config.settings import settings
from evals.harness.keys import eval_keys_missing

# provider -> (the env-var name its inference needs, the settings attr the preflight reads).
# ``gemini``/``openrouter`` hold a SecretStr (``.get_secret_value()``); ``modal`` a plain URL string.
_PROVIDER_KEY = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "modal": "MODAL_ENDPOINT_URL",
}


def _set_provider_key(mocker, provider: str, *, value: str) -> None:
    """Patch the active provider's key on ``settings`` to ``value`` ('' = missing)."""
    if provider == "modal":
        mocker.patch.object(settings, "modal_endpoint_url", value)
    else:
        attr = "openrouter_api_key" if provider == "openrouter" else "gemini_api_key"
        mocker.patch.object(settings, attr, SimpleNamespace(get_secret_value=lambda: value))


@pytest.fixture
def with_opik(mocker):
    """Give ``settings`` a present ``OPIK_API_KEY`` — the always-required half of the preflight."""
    mocker.patch.object(
        settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )


@pytest.mark.parametrize(("provider", "expected_key"), sorted(_PROVIDER_KEY.items()))
def test_gate_predicate_names_the_active_providers_key_when_missing(
    with_opik, mocker, provider, expected_key
):
    """With OPIK present but the active provider's inference key absent, the gate demands THAT key.

    This is the exact defect: an openrouter/modal (or .env-only) operator must not be told to set
    ``GEMINI_API_KEY`` — the skip reason names the provider-correct variable.
    """
    mocker.patch.object(settings, "llm_provider", provider)
    _set_provider_key(mocker, provider, value="")

    assert eval_keys_missing() == [expected_key]


@pytest.mark.parametrize("provider", sorted(_PROVIDER_KEY))
def test_gate_predicate_is_empty_when_provider_key_and_opik_present(with_opik, mocker, provider):
    """The active provider's key + OPIK present → nothing missing → the gate RUNS (does not skip)."""
    mocker.patch.object(settings, "llm_provider", provider)
    _set_provider_key(mocker, provider, value="present")

    assert eval_keys_missing() == []


def test_gate_module_wires_the_shared_predicate_not_a_divergent_copy():
    """The gate calls the ONE shared ``eval_keys_missing`` — no hardcoded ``REQUIRED_KEYS`` copy.

    Guards against reintroducing the Blocker: if someone re-adds a local key predicate the identity
    check breaks, and the vacuous-skip-for-non-gemini bug is back.
    """
    from evals.regression import test_thresholds as gate

    assert gate.eval_keys_missing is eval_keys_missing
    assert not hasattr(gate, "REQUIRED_KEYS")
    assert not hasattr(gate, "_missing_keys")
