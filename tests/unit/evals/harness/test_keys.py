"""Offline tests for the Langfuse and inference-key preflight."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from decode.config.settings import settings
from evals.harness import keys


def _secret(value: str) -> SimpleNamespace:
    return SimpleNamespace(get_secret_value=lambda: value)


@pytest.fixture
def with_keys(mocker):
    mocker.patch.object(settings, "llm_provider", "gemini")
    mocker.patch.object(settings, "eval_judge_provider", "")
    mocker.patch.object(settings, "langfuse_public_key", _secret("pk-test"))
    mocker.patch.object(settings, "langfuse_secret_key", _secret("sk-test"))
    mocker.patch.object(settings, "gemini_api_key", _secret("gem-test"))


def test_no_missing_keys_when_langfuse_and_gemini_are_present(with_keys):
    assert keys.eval_keys_missing() == []


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("langfuse_public_key", "LANGFUSE_PUBLIC_KEY"),
        ("langfuse_secret_key", "LANGFUSE_SECRET_KEY"),
    ],
)
def test_missing_langfuse_credentials_are_reported(with_keys, mocker, field, expected):
    mocker.patch.object(settings, field, _secret("  "))
    assert keys.eval_keys_missing() == [expected]


def test_missing_langfuse_and_inference_keys_are_reported_in_order(mocker):
    mocker.patch.object(settings, "llm_provider", "gemini")
    mocker.patch.object(settings, "langfuse_public_key", _secret(""))
    mocker.patch.object(settings, "langfuse_secret_key", _secret(""))
    mocker.patch.object(settings, "gemini_api_key", _secret(""))
    assert keys.eval_keys_missing() == [
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "GEMINI_API_KEY",
    ]


def test_openrouter_provider_requires_its_api_key(with_keys, mocker):
    mocker.patch.object(settings, "llm_provider", "openrouter")
    mocker.patch.object(settings, "openrouter_api_key", _secret(""))
    assert keys.eval_keys_missing() == ["OPENROUTER_API_KEY"]


def test_modal_provider_requires_the_endpoint_url(with_keys, mocker):
    mocker.patch.object(settings, "llm_provider", "modal")
    mocker.patch.object(settings, "modal_endpoint_url", "")
    assert keys.eval_keys_missing() == ["MODAL_ENDPOINT_URL"]


def test_read_only_langfuse_commands_do_not_require_provider_keys(with_keys, mocker):
    mocker.patch.object(settings, "llm_provider", "modal")
    mocker.patch.object(settings, "modal_endpoint_url", "")

    assert keys.eval_keys_missing(require_provider=False) == []
    assert keys.eval_keys_missing() == ["MODAL_ENDPOINT_URL"]


def test_read_only_commands_still_require_langfuse_keys(with_keys, mocker):
    mocker.patch.object(settings, "langfuse_secret_key", _secret(""))
    assert keys.eval_keys_missing(require_provider=False) == ["LANGFUSE_SECRET_KEY"]


def test_provider_keys_for_agent_and_judge_are_deduplicated(with_keys, mocker):
    mocker.patch.object(settings, "llm_provider", "gemini")
    mocker.patch.object(settings, "eval_judge_provider", "openrouter")
    mocker.patch.object(settings, "openrouter_api_key", _secret(""))
    assert keys.eval_keys_missing() == ["OPENROUTER_API_KEY"]


def test_main_returns_zero_when_keys_are_present(with_keys):
    assert keys.main() == 0


def test_main_prints_a_friendly_line_when_keys_are_missing(mocker, capsys):
    mocker.patch.object(settings, "llm_provider", "gemini")
    mocker.patch.object(settings, "langfuse_public_key", _secret(""))
    mocker.patch.object(settings, "langfuse_secret_key", _secret("sk-test"))
    mocker.patch.object(settings, "gemini_api_key", _secret("gem-test"))

    assert keys.main() == 1
    error = capsys.readouterr().err
    assert "LANGFUSE_PUBLIC_KEY" in error
    assert "skipped" in error.lower()
    assert "Traceback" not in error
