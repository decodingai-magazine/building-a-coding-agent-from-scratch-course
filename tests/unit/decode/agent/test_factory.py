"""Unit tests for :func:`decode.agent.factory.build_agent`.

ADR-0002 §1-2: the agent is a Pydantic AI :class:`~pydantic_ai.Agent` on Gemini, built via
the ``google-gla`` API-key path with the model id from ``settings.gemini_model`` and
``output_type=[str, DeferredToolRequests]`` so the deferred-tool seam is ready for task 005
even though chat-only has no tools. These tests assert the *construction contract* without
making any network call (no model request is issued just by building the agent).
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_ai.messages import ModelRequest, ToolCallPart
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.google import GoogleProvider

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agents.loader import load_agent
from decode.entities.agent_def import AgentDef
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny(reason="test default deny")


async def _no_user_resolver(question: str) -> str:
    raise RuntimeError("no interactive user in this test")


async def _benign_user_resolver(question: str) -> str:
    """An ``ask_user`` resolver that returns a value so a TestModel-driven run can complete.

    A bare ``TestModel`` calls every visible tool, including ``ask_user`` (which is ungated); a
    resolver that returns rather than raises lets the visible-tool tests finish so we can inspect
    the full set of tools the model was offered.
    """
    return "an answer"


def _deps(cwd: Path, *, active_agent: AgentDef | None = None) -> AgentDeps:
    """Minimal AgentDeps for a build-and-run test (``cwd`` + an optional active agent).

    When an ``active_agent`` is supplied the run is a visible-tool / prompt probe driven by a
    ``TestModel`` that may call every tool, so the ``ask_user`` resolver returns (not raises); the
    older chat-only tests pass no ``active_agent`` and keep the raising resolver.
    """
    kwargs: dict[str, object] = {}
    resolver = _no_user_resolver
    if active_agent is not None:
        kwargs["active_agent"] = active_agent
        resolver = _benign_user_resolver
    return AgentDeps(
        cwd=cwd,
        emit=lambda event: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=resolver,
        **kwargs,  # type: ignore[arg-type]
    )


def _tool_names_called(messages: list[object]) -> set[str]:
    """Every tool name the model actually called across ``messages`` (the visible-tool proof)."""
    called: set[str] = set()
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolCallPart):
                called.add(part.tool_name)
    return called


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


# --- per-agent tool restriction via the per-tool prepare= callback (ADR-0003 §6) ------------


async def test_plan_agent_run_omits_write_edit_and_bash_from_the_visible_tools(tmp_path, mocker):
    """With ``active_agent = plan`` the model never sees write/edit/bash (they are hidden).

    A bare ``TestModel`` calls **every** tool in the schema it is offered, so the set of tools it
    actually called == the visible tool schema for the run. Asserting the mutating tools are never
    called is the spike-confirmed proof that ``prepare= -> None`` hid them (ADR-0003 §6).
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    agent = build_agent()
    plan = load_agent("plan")

    with agent.override(model=TestModel(custom_output_text="ok")):
        result = await agent.run("hi", deps=_deps(tmp_path, active_agent=plan))

    called = _tool_names_called(result.all_messages())
    # The mutating tools are absent from the plan persona's allowlist → hidden → never called.
    assert "write" not in called
    assert "edit" not in called
    assert "bash" not in called
    # The read-only set the plan persona DOES allow is offered (and so called by TestModel).
    assert {"read", "glob", "grep"} <= called


async def test_build_agent_run_offers_the_full_mutating_tool_set(tmp_path, mocker):
    """With ``active_agent = build`` the mutating tools are visible (the full M1 set)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    agent = build_agent()
    build = load_agent("build")

    with agent.override(model=TestModel(custom_output_text="ok")):
        result = await agent.run("hi", deps=_deps(tmp_path, active_agent=build))

    called = _tool_names_called(result.all_messages())
    assert {"write", "edit", "bash"} <= called


async def test_tool_visibility_follows_the_active_agent_per_run_without_rebuild(tmp_path, mocker):
    """Switching ``deps.active_agent`` changes the visible tools on the next run — one agent."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    agent = build_agent()  # built ONCE
    plan = load_agent("plan")
    build = load_agent("build")

    with agent.override(model=TestModel(custom_output_text="ok")):
        plan_run = await agent.run("hi", deps=_deps(tmp_path, active_agent=plan))
        build_run = await agent.run("hi", deps=_deps(tmp_path, active_agent=build))

    assert "bash" not in _tool_names_called(plan_run.all_messages())
    assert "bash" in _tool_names_called(build_run.all_messages())


# --- per-agent system prompt via the dynamic instructions hook (ADR-0003 §6,7) --------------


def test_build_agent_registers_a_dynamic_agent_prompt_instructions_function(mocker):
    """A dynamic callable instructions entry for the per-agent prompt must be registered."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )

    agent = build_agent()

    # Beyond the static base + the memory hook, the agent-prompt hook is also callable; at least
    # two callable instruction entries (memory + agent prompt) ride per run.
    callables = [p for p in agent._instructions if callable(p)]
    assert len(callables) >= 2


async def test_active_agent_prompt_is_injected_into_the_run_instructions(tmp_path, mocker):
    """The code-reviewer prompt rides the assembled instructions when it is the active agent."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    agent = build_agent()
    reviewer = load_agent("code-reviewer")

    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        result = await agent.run("hi", deps=_deps(tmp_path, active_agent=reviewer))

    first = result.all_messages()[0]
    assert isinstance(first, ModelRequest)
    assert first.instructions is not None
    # A distinctive line from the code-reviewer body rides in the same instructions block.
    assert "code-reviewer agent" in first.instructions
    # The static base prompt is still present alongside it.
    assert "decode" in first.instructions.lower()


async def test_switching_active_agent_changes_the_prompt_on_the_next_turn(tmp_path, mocker):
    """Reassigning ``deps.active_agent`` swaps the injected prompt next run — no rebuild."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    agent = build_agent()  # built ONCE
    reviewer = load_agent("code-reviewer")
    build = load_agent("build")

    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        first_run = await agent.run("hi", deps=_deps(tmp_path, active_agent=reviewer))
        second_run = await agent.run("hi", deps=_deps(tmp_path, active_agent=build))

    first = first_run.all_messages()[0]
    second = second_run.all_messages()[0]
    assert isinstance(first, ModelRequest)
    assert isinstance(second, ModelRequest)
    assert "code-reviewer agent" in (first.instructions or "")
    assert "build agent" in (second.instructions or "")
    assert "code-reviewer agent" not in (second.instructions or "")
