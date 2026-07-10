"""The Credentials Proxy seam in :func:`decode.agent.factory.build_agent` (ADR-0008 §5, task 061).

In flow mode with ``runtime_credentials_proxy_enabled`` on, model construction resolves the
provider API key through Kitaru secrets instead of the settings ``SecretStr``; interactive runs
and the default off-switch stay byte-unchanged. Asserted offline: building the agent issues no
model request, ``kitaru.get_secret`` is patched, and the constructed key is read back off the
provider's client (attribute paths verified against openai 2.43 / pydantic-ai 1.107 / google-genai).
"""

from __future__ import annotations

import logging

import pytest
from kitaru import KitaruRuntimeError
from pydantic import SecretStr

from decode.agent.factory import _build_model, build_agent, resolve_provider_key_via_proxy

# Distinct sentinels so a test can tell *which* source the key came from: the settings ``SecretStr``
# or the Kitaru secret. The proxy path must use the Kitaru one and never the settings one.
_SETTINGS_GEMINI_KEY = "SETTINGS-gemini-key-must-not-be-used"
_SETTINGS_OPENROUTER_KEY = "SETTINGS-openrouter-key-must-not-be-used"
_KITARU_GEMINI_KEY = "KITARU-secret-gemini-key-2f9c"
_KITARU_OPENROUTER_KEY = "KITARU-secret-openrouter-key-7a1e"


def _gemini_api_key(agent) -> str:
    """Read back the api key the google-gla provider was constructed with (offline)."""
    return agent.model._provider.client._api_client.api_key


def _openrouter_api_key(agent) -> str:
    """Read back the api key the OpenAI-compatible OpenRouter provider was constructed with."""
    return agent.model._provider.client.api_key


class _FakeSecret:
    """A stand-in for ``kitaru.Secret`` — only the ``.get(key)`` the proxy uses is implemented."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._values.get(key, default)


def _patch_gemini_settings(mocker, *, proxy_enabled: bool) -> None:
    mocker.patch("decode.agent.factory.settings.llm_provider", "gemini", create=False)
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key",
        SecretStr(_SETTINGS_GEMINI_KEY),
        create=False,
    )
    mocker.patch("decode.agent.factory.settings.gemini_model", "gemini-2.5-flash", create=False)
    mocker.patch(
        "decode.agent.factory.settings.runtime_credentials_proxy_enabled",
        proxy_enabled,
        create=False,
    )
    mocker.patch(
        "decode.agent.factory.settings.runtime_secret_name", "decode-llm-creds", create=False
    )


def _patch_openrouter_settings(mocker, *, proxy_enabled: bool) -> None:
    mocker.patch("decode.agent.factory.settings.llm_provider", "openrouter", create=False)
    mocker.patch(
        "decode.agent.factory.settings.openrouter_api_key",
        SecretStr(_SETTINGS_OPENROUTER_KEY),
        create=False,
    )
    mocker.patch("decode.agent.factory.settings.openrouter_model", "openrouter/free", create=False)
    mocker.patch(
        "decode.agent.factory.settings.runtime_credentials_proxy_enabled",
        proxy_enabled,
        create=False,
    )
    mocker.patch(
        "decode.agent.factory.settings.runtime_secret_name", "decode-llm-creds", create=False
    )


# the gate: proxy off (default) OR interactive → byte-unchanged settings read


def test_flow_mode_with_proxy_disabled_reads_the_settings_key(mocker):
    _patch_gemini_settings(mocker, proxy_enabled=False)
    get_secret = mocker.patch("kitaru.get_secret")

    agent = build_agent(flow_mode=True)

    assert _gemini_api_key(agent) == _SETTINGS_GEMINI_KEY
    get_secret.assert_not_called()  # the proxy was never consulted


def test_interactive_mode_with_proxy_enabled_still_reads_the_settings_key(mocker):
    """Proxy ON but **interactive** (``flow_mode=False``, the default) → settings key, no lookup.

    The interactive REPL must be unaffected by the proxy: only a flow-mode run resolves via Kitaru.
    """
    _patch_gemini_settings(mocker, proxy_enabled=True)
    get_secret = mocker.patch("kitaru.get_secret")

    agent = build_agent()  # default flow_mode=False

    assert _gemini_api_key(agent) == _SETTINGS_GEMINI_KEY
    get_secret.assert_not_called()


# proxy on + flow mode → resolve from Kitaru, not from settings


def test_flow_mode_with_proxy_enabled_resolves_gemini_key_from_kitaru(mocker):
    _patch_gemini_settings(mocker, proxy_enabled=True)
    get_secret = mocker.patch(
        "kitaru.get_secret",
        return_value=_FakeSecret({"GEMINI_API_KEY": _KITARU_GEMINI_KEY}),
    )

    agent = build_agent(flow_mode=True)

    assert _gemini_api_key(agent) == _KITARU_GEMINI_KEY
    assert _gemini_api_key(agent) != _SETTINGS_GEMINI_KEY  # settings key was not used
    get_secret.assert_called_once_with("decode-llm-creds")


def test_flow_mode_with_proxy_enabled_resolves_openrouter_key_from_kitaru(mocker):
    _patch_openrouter_settings(mocker, proxy_enabled=True)
    get_secret = mocker.patch(
        "kitaru.get_secret",
        return_value=_FakeSecret({"OPENROUTER_API_KEY": _KITARU_OPENROUTER_KEY}),
    )

    agent = build_agent(flow_mode=True)

    assert _openrouter_api_key(agent) == _KITARU_OPENROUTER_KEY
    assert _openrouter_api_key(agent) != _SETTINGS_OPENROUTER_KEY
    get_secret.assert_called_once_with("decode-llm-creds")


def test_proxy_reads_the_provider_specific_secret_key_name(mocker):
    _patch_gemini_settings(mocker, proxy_enabled=True)
    fake = _FakeSecret({"GEMINI_API_KEY": _KITARU_GEMINI_KEY, "OPENROUTER_API_KEY": "other"})
    mocker.patch("kitaru.get_secret", return_value=fake)

    agent = build_agent(flow_mode=True)

    assert _gemini_api_key(agent) == _KITARU_GEMINI_KEY


# the invariant: the raw key is never logged


def test_proxy_does_not_log_the_raw_key(mocker, caplog):
    _patch_gemini_settings(mocker, proxy_enabled=True)
    mocker.patch(
        "kitaru.get_secret",
        return_value=_FakeSecret({"GEMINI_API_KEY": _KITARU_GEMINI_KEY}),
    )

    with caplog.at_level(logging.DEBUG, logger="decode.agent.factory"):
        build_agent(flow_mode=True)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert _KITARU_GEMINI_KEY not in logged  # the raw key never reaches the logs
    assert "decode-llm-creds" in logged  # the secret *name* is fine to log


# enabled-but-secret-missing → surface the error, never silently fall back


def test_missing_secret_surfaces_kitaru_error_without_falling_back(mocker):
    _patch_gemini_settings(mocker, proxy_enabled=True)
    mocker.patch(
        "kitaru.get_secret",
        side_effect=KitaruRuntimeError("Secret `decode-llm-creds` was not found."),
    )

    with pytest.raises(KitaruRuntimeError, match="decode-llm-creds"):
        build_agent(flow_mode=True)


def test_secret_missing_the_provider_key_raises_a_clear_error(mocker):
    _patch_gemini_settings(mocker, proxy_enabled=True)
    mocker.patch("kitaru.get_secret", return_value=_FakeSecret({"OTHER_KEY": "x"}))

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        build_agent(flow_mode=True)


def test_resolve_key_via_proxy_returns_the_secret_value(mocker):
    mocker.patch(
        "decode.agent.factory.settings.runtime_secret_name", "decode-llm-creds", create=False
    )
    mocker.patch(
        "kitaru.get_secret",
        return_value=_FakeSecret({"GEMINI_API_KEY": _KITARU_GEMINI_KEY}),
    )

    assert resolve_provider_key_via_proxy("gemini") == _KITARU_GEMINI_KEY


def test_build_model_flow_mode_defaults_to_false(mocker):
    _patch_gemini_settings(mocker, proxy_enabled=True)
    get_secret = mocker.patch("kitaru.get_secret")

    model = _build_model()  # no flow_mode → interactive default

    assert model._provider.client._api_client.api_key == _SETTINGS_GEMINI_KEY
    get_secret.assert_not_called()
