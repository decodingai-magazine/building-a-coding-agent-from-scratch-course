"""Unit tests for :func:`decode.agent.factory.build_agent`.

ADR-0002 §1-2: the agent is a Pydantic AI :class:`~pydantic_ai.Agent` on Gemini, built via
the ``google-gla`` API-key path with the model id from ``settings.gemini_model`` and
``output_type=[str, DeferredToolRequests]`` so the deferred-tool seam is ready for task 005
even though chat-only has no tools. These tests assert the *construction contract* without
making any network call (no model request is issued just by building the agent).
"""

from pydantic import SecretStr
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent


def test_build_agent_uses_google_model_with_configured_id(mocker):
    # Avoid touching real Gemini credentials/env: feed the key from settings explicitly.
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    mocker.patch("decode.agent.factory.settings.gemini_model", "gemini-2.5-flash", create=False)

    agent = build_agent()

    assert isinstance(agent.model, GoogleModel)
    assert agent.model.model_name == "gemini-2.5-flash"
    # google-gla (Generative-Language), NOT Vertex: the provider's system name is "google".
    assert isinstance(agent.model._provider, GoogleProvider)
    assert agent.model.system == "google"


def test_build_agent_respects_a_custom_model_id(mocker):
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    mocker.patch("decode.agent.factory.settings.gemini_model", "gemini-2.5-pro", create=False)

    agent = build_agent()

    assert agent.model.model_name == "gemini-2.5-pro"


def test_build_agent_declares_agent_deps_type(mocker):
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )

    agent = build_agent()

    # deps_type is what Pydantic AI validates `deps=` against at run time.
    assert agent.deps_type is AgentDeps


def test_build_agent_includes_deferred_tool_requests_in_output(mocker):
    """The deferred-tool seam must be present now so task 005 needs no factory rewrite."""
    from pydantic_ai import DeferredToolRequests

    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )

    agent = build_agent()

    # The output schema accepts both plain text and a deferred-tool-requests result.
    output_types = set(agent.output_type) if isinstance(agent.output_type, list) else set()
    assert str in output_types
    assert DeferredToolRequests in output_types


def test_build_agent_sets_a_static_base_instruction(mocker):
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )

    agent = build_agent()

    # `_instructions` is a normalized list of static strings / functions; a static base
    # prompt identifying decode as a terminal coding agent must be present.
    static_text = " ".join(p for p in agent._instructions if isinstance(p, str))
    assert "decode" in static_text.lower()
    assert "coding agent" in static_text.lower()
