"""Unit tests for :func:`decode.agent.factory.build_agent`.

ADR-0002 §1-2: the agent is a Pydantic AI :class:`~pydantic_ai.Agent` on Gemini, built via
the ``google-gla`` API-key path with the model id from ``settings.gemini_model`` and
``output_type=[str, DeferredToolRequests]`` so the deferred-tool seam is ready for task 005
even though chat-only has no tools. These tests assert the *construction contract* without
making any network call (no model request is issued just by building the agent).
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.google import GoogleProvider

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny(reason="test default deny")


async def _no_user_resolver(question: str) -> str:
    raise RuntimeError("no interactive user in this test")


def _deps(cwd: Path) -> AgentDeps:
    """Minimal AgentDeps for a build-and-run test: only ``cwd`` is exercised here."""
    return AgentDeps(
        cwd=cwd,
        emit=lambda event: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_no_user_resolver,
    )


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


def test_build_agent_registers_a_dynamic_memory_instructions_function(mocker):
    """A dynamic (callable) instructions entry must be present so memory is built per run."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )

    agent = build_agent()

    # Beyond the static base string, at least one callable instructions entry (the per-run
    # memory hook) must be registered — that is what reads AGENTS.md/MEMORY.md at prompt-build.
    assert any(callable(p) for p in agent._instructions)


async def test_memory_is_injected_into_the_first_request_instructions(tmp_path, mocker):
    """End-to-end: AGENTS.md content reaches the model via the first ModelRequest instructions."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    (tmp_path / "AGENTS.md").write_text("PROJECT RULE: always run the tests", encoding="utf-8")

    agent = build_agent()
    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        result = await agent.run("hi", deps=_deps(tmp_path))

    first = result.all_messages()[0]
    assert isinstance(first, ModelRequest)
    assert first.instructions is not None
    # Both the static base prompt and the injected memory ride in the same instructions block.
    assert "decode" in first.instructions.lower()
    assert "PROJECT RULE: always run the tests" in first.instructions
    assert f"# From {tmp_path / 'AGENTS.md'}" in first.instructions


async def test_memory_injection_is_evaluated_per_run(tmp_path, mocker):
    """Editing AGENTS.md takes effect on the next run without rebuilding the agent."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("FIRST", encoding="utf-8")

    agent = build_agent()
    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        first_run = await agent.run("hi", deps=_deps(tmp_path))
        agents_md.write_text("SECOND", encoding="utf-8")
        second_run = await agent.run("hi", deps=_deps(tmp_path))

    first_instructions = first_run.all_messages()[0]
    second_instructions = second_run.all_messages()[0]
    assert isinstance(first_instructions, ModelRequest)
    assert isinstance(second_instructions, ModelRequest)
    assert "FIRST" in (first_instructions.instructions or "")
    assert "SECOND" in (second_instructions.instructions or "")
    assert "FIRST" not in (second_instructions.instructions or "")


async def test_no_memory_files_yields_only_the_static_base(tmp_path, mocker):
    """With no memory files, the instructions are just the static base (no empty headers)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )

    agent = build_agent()
    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        result = await agent.run("hi", deps=_deps(tmp_path))

    first = result.all_messages()[0]
    assert isinstance(first, ModelRequest)
    assert first.instructions is not None
    assert "decode" in first.instructions.lower()
    assert "# From" not in first.instructions
