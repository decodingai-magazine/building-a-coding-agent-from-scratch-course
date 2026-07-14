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
