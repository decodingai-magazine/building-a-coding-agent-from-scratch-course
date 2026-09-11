"""Offline tests for the eval-target key preflight (ADR-0017 §9; task 120).

No infra, no keys, no network: the resolved ``settings`` singleton is patched in place, so the
provider-aware key check and the Makefile-guard exit contract are asserted directly. This is the
fail-fast guard ``make eval-benchmark`` / ``make eval-regression`` run FIRST — it must read
``settings`` (a key in ``.env`` counts, not just the process env) and skip friendly, never traceback.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from decode.config.settings import settings
from evals.harness import keys


@pytest.fixture
def with_keys(mocker):
    """Give ``settings`` a gemini provider with both required keys present (the happy path)."""
    mocker.patch.object(settings, "llm_provider", "gemini")
    mocker.patch.object(
        settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )
    mocker.patch.object(
        settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: "gem-key")
    )


# --- eval_keys_missing: provider-aware, reads settings (ADR-0017 §9) --------------------------------


def test_no_missing_keys_when_opik_and_gemini_present(with_keys):
    assert keys.eval_keys_missing() == []


def test_missing_opik_key_is_reported(with_keys, mocker):
    mocker.patch.object(settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "  "))

    assert keys.eval_keys_missing() == ["OPIK_API_KEY"]


def test_missing_gemini_key_is_reported(with_keys, mocker):
    mocker.patch.object(settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: ""))

    assert keys.eval_keys_missing() == ["GEMINI_API_KEY"]


def test_both_keys_missing_are_reported_in_order(mocker):
    mocker.patch.object(settings, "llm_provider", "gemini")
    mocker.patch.object(settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: ""))
    mocker.patch.object(settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: ""))

    assert keys.eval_keys_missing() == ["OPIK_API_KEY", "GEMINI_API_KEY"]


def test_openrouter_provider_requires_the_openrouter_key(mocker):
    mocker.patch.object(settings, "llm_provider", "openrouter")
    mocker.patch.object(
        settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )
    mocker.patch.object(
        settings, "openrouter_api_key", SimpleNamespace(get_secret_value=lambda: "")
    )

    assert keys.eval_keys_missing() == ["OPENROUTER_API_KEY"]


def test_modal_provider_requires_the_endpoint_url(mocker):
    mocker.patch.object(settings, "llm_provider", "modal")
    mocker.patch.object(
        settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )
    mocker.patch.object(settings, "modal_endpoint_url", "")

    assert keys.eval_keys_missing() == ["MODAL_ENDPOINT_URL"]


# --- require_provider=False: the Opik-only live-project commands (task 163) -------------------------


def test_an_opik_only_caller_does_not_need_the_provider_key(mocker):
    """``evals mine`` / ``online-rule create`` run no inference — the repo's own ``.env`` case.

    ``LLM_PROVIDER=modal`` with no ``MODAL_ENDPOINT_URL`` is what the committed ``.env`` ships; a
    read-only trace query must not be blocked on an endpoint it never calls.
    """
    mocker.patch.object(settings, "llm_provider", "modal")
    mocker.patch.object(
        settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )
    mocker.patch.object(settings, "modal_endpoint_url", "")

    assert keys.eval_keys_missing(require_provider=False) == []
    # The default is byte-identical to before: every other caller still demands the provider key.
    assert keys.eval_keys_missing() == ["MODAL_ENDPOINT_URL"]


def test_an_opik_only_caller_still_needs_the_opik_key(mocker):
    mocker.patch.object(settings, "llm_provider", "modal")
    mocker.patch.object(settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: " "))
    mocker.patch.object(settings, "modal_endpoint_url", "")

    assert keys.eval_keys_missing(require_provider=False) == ["OPIK_API_KEY"]


# --- main(): the Makefile guard exit contract -------------------------------------------------------


def test_main_returns_zero_when_keys_present(with_keys):
    assert keys.main() == 0


def test_main_returns_one_and_prints_friendly_line_when_missing(mocker, capsys):
    mocker.patch.object(settings, "llm_provider", "gemini")
    mocker.patch.object(settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: ""))
    mocker.patch.object(
        settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: "gem-key")
    )

    exit_code = keys.main()

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "OPIK_API_KEY" in err
    assert "skipped" in err.lower()
    # No traceback — the guard is a one-liner, never a raise.
    assert "Traceback" not in err


# --- a judge on its own provider: both keys, deduped, agent first (task 170) ------------------------


def test_a_differing_judge_provider_adds_its_key_after_the_agents(with_keys, mocker):
    """Agent gemini + judge modal: the regression gate drives BOTH, so it needs both keys."""
    mocker.patch.object(settings, "eval_judge_provider", "modal")
    mocker.patch.object(settings, "modal_endpoint_url", "")

    assert keys.eval_keys_missing() == ["MODAL_ENDPOINT_URL"]


def test_both_provider_keys_are_reported_when_both_are_missing(mocker):
    """Agent first, judge second — the operator reads the run's own order."""
    mocker.patch.object(settings, "llm_provider", "openrouter")
    mocker.patch.object(settings, "eval_judge_provider", "gemini")
    mocker.patch.object(
        settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )
    mocker.patch.object(
        settings, "openrouter_api_key", SimpleNamespace(get_secret_value=lambda: "")
    )
    mocker.patch.object(settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: ""))

    assert keys.eval_keys_missing() == ["OPENROUTER_API_KEY", "GEMINI_API_KEY"]


def test_the_same_provider_on_both_sides_is_reported_once(mocker):
    """An explicit judge provider that MATCHES the agent's must not duplicate its key."""
    mocker.patch.object(settings, "llm_provider", "gemini")
    mocker.patch.object(settings, "eval_judge_provider", "gemini")
    mocker.patch.object(settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: ""))
    mocker.patch.object(settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: ""))

    assert keys.eval_keys_missing() == ["OPIK_API_KEY", "GEMINI_API_KEY"]


def test_a_judge_only_caller_skips_the_agents_provider_key(mocker):
    """``require_agent=False`` = online eval: it grades traces the agent ALREADY emitted."""
    mocker.patch.object(settings, "llm_provider", "modal")
    mocker.patch.object(settings, "eval_judge_provider", "gemini")
    mocker.patch.object(settings, "modal_endpoint_url", "")
    mocker.patch.object(
        settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )
    mocker.patch.object(
        settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: "gem-key")
    )

    assert keys.eval_keys_missing(require_agent=False) == []
    # The judge's own key is still demanded — that is the provider this caller DOES call.
    mocker.patch.object(settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: ""))
    assert keys.eval_keys_missing(require_agent=False) == ["GEMINI_API_KEY"]


def test_require_provider_false_wins_over_require_agent(mocker):
    """The master switch still drops EVERY provider key — the Opik-only commands stay keyless."""
    mocker.patch.object(settings, "llm_provider", "modal")
    mocker.patch.object(settings, "eval_judge_provider", "gemini")
    mocker.patch.object(settings, "modal_endpoint_url", "")
    mocker.patch.object(settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: ""))
    mocker.patch.object(
        settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )

    assert keys.eval_keys_missing(require_provider=False) == []
