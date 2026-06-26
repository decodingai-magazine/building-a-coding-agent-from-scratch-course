"""Unit tests for :func:`decode.agent.factory.build_agent`.

ADR-0002 §1-2 / ADR-0005 §3-5: the agent is a Pydantic AI :class:`~pydantic_ai.Agent` built on
the configured **LLM Provider** — model construction is delegated to the ``_build_model()``
Provider Seam (gemini via the ``google-gla`` API-key path, openrouter and modal via
``OpenAIChatModel``). ``output_type=[str, DeferredToolRequests]`` keeps the deferred-tool seam
ready. These tests assert the *construction contract* without making any network call (no model
request is issued just by building the agent — every provider constructs offline).
"""

from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelRequest, ToolCallPart
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.google import GoogleProvider

from decode.agent.deps import AgentDeps
from decode.agent.factory import _build_model, build_agent
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


# --- the Skills Catalog injected via the dynamic instructions hook (ADR-0004 §1,§9) ---------


def test_build_agent_registers_a_dynamic_skills_catalog_instructions_function(mocker):
    """A third dynamic callable instructions entry (the Skills Catalog hook) must be registered."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )

    agent = build_agent()

    # Static base + memory hook + agent-prompt hook + skills-catalog hook → at least three
    # callable instruction entries ride per run.
    callables = [p for p in agent._instructions if callable(p)]
    assert len(callables) >= 3


async def test_skills_catalog_is_injected_into_the_run_instructions(tmp_path, mocker):
    """The catalog (skill names + the skill(\"…\") cue) rides the assembled run instructions."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    agent = build_agent()

    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        result = await agent.run("hi", deps=_deps(tmp_path, active_agent=load_agent("build")))

    first = result.all_messages()[0]
    assert isinstance(first, ModelRequest)
    assert first.instructions is not None
    # The built-in skill names and the dispatcher cue both ride the instructions block.
    assert "commit" in first.instructions
    assert "review-diff" in first.instructions
    assert 'skill("<name>")' in first.instructions


async def test_skills_catalog_is_injected_regardless_of_active_agent(tmp_path, mocker):
    """All agents see all skills (ADR-0004 §4): the catalog rides every persona's prompt."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    agent = build_agent()  # built ONCE
    plan = load_agent("plan")
    reviewer = load_agent("code-reviewer")

    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        plan_run = await agent.run("hi", deps=_deps(tmp_path, active_agent=plan))
        reviewer_run = await agent.run("hi", deps=_deps(tmp_path, active_agent=reviewer))

    for run in (plan_run, reviewer_run):
        first = run.all_messages()[0]
        assert isinstance(first, ModelRequest)
        instructions = first.instructions or ""
        assert "commit" in instructions
        assert "review-diff" in instructions
        assert 'skill("<name>")' in instructions


# --- the _build_model() Provider Seam: one model per llm_provider (ADR-0005 §3-5) -----------
#
# Construction is offline for every provider — building the agent issues no model request — so
# these tests assert the model *type* + the client *shape* (base_url / headers / placeholder
# api_key) with ``mocker.patch``ed settings, never a live call (ADR-0005 Consequences).
#
# The attribute paths below were verified against the installed openai 2.43 / pydantic-ai 1.107:
# the custom modal client is reachable at ``agent.model._provider.client``; httpx normalizes the
# ``base_url`` with a trailing slash (``.../v1`` round-trips as ``.../v1/``); ``default_headers``
# carries the Modal proxy headers; ``api_key`` reads back the secret / ``"EMPTY"`` placeholder.

_MODAL_URL = "https://modal-endpoint.example.com"


def _patch_provider(mocker, provider, *, modal_authenticated=True):
    """Patch the settings ``_build_model()`` reads for ``provider``; return its expected facts.

    Mirrors the existing ``gemini_api_key`` patch pattern (``decode.agent.factory.settings.*``).
    Returns ``(expected_model_cls, expected_system, expected_model_name)``.
    """
    mocker.patch("decode.agent.factory.settings.llm_provider", provider, create=False)
    if provider == "gemini":
        mocker.patch(
            "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
        )
        mocker.patch("decode.agent.factory.settings.gemini_model", "gemini-2.5-flash", create=False)
        return GoogleModel, "google", "gemini-2.5-flash"
    if provider == "openrouter":
        mocker.patch(
            "decode.agent.factory.settings.openrouter_api_key", SecretStr("or-key"), create=False
        )
        mocker.patch(
            "decode.agent.factory.settings.openrouter_model",
            "qwen/qwen3-coder:free",
            create=False,
        )
        return OpenAIChatModel, "openrouter", "qwen/qwen3-coder:free"
    if provider == "modal":
        mocker.patch("decode.agent.factory.settings.modal_endpoint_url", _MODAL_URL, create=False)
        mocker.patch(
            "decode.agent.factory.settings.modal_endpoint_model",
            "openai/gpt-oss-120b",
            create=False,
        )
        token_id = "wk-id" if modal_authenticated else ""
        token_secret = "ws-secret" if modal_authenticated else ""
        mocker.patch(
            "decode.agent.factory.settings.modal_proxy_token_id",
            SecretStr(token_id),
            create=False,
        )
        mocker.patch(
            "decode.agent.factory.settings.modal_proxy_token_secret",
            SecretStr(token_secret),
            create=False,
        )
        return OpenAIChatModel, "openai", "openai/gpt-oss-120b"
    raise AssertionError(f"unhandled provider in test helper: {provider}")


@pytest.mark.parametrize(
    ("provider", "modal_authenticated"),
    [
        ("gemini", False),
        ("openrouter", False),
        ("modal", True),
        ("modal", False),
    ],
    ids=["gemini", "openrouter", "modal-authenticated", "modal-unauthenticated"],
)
def test_build_agent_constructs_the_model_for_the_configured_provider(
    mocker, provider, modal_authenticated
):
    """``build_agent()`` delegates to ``_build_model()``, which builds the selected provider."""
    expected_cls, expected_system, expected_model_name = _patch_provider(
        mocker, provider, modal_authenticated=modal_authenticated
    )

    agent = build_agent()

    assert isinstance(agent.model, expected_cls)
    assert agent.model.system == expected_system
    assert agent.model.model_name == expected_model_name


def test_modal_authenticated_client_carries_both_proxy_headers(mocker):
    """Both proxy tokens set → custom ``base_url`` + dual Modal-Key / Modal-Secret headers."""
    _patch_provider(mocker, "modal", modal_authenticated=True)

    agent = build_agent()

    client = agent.model._provider.client
    assert str(client.base_url) == f"{_MODAL_URL}/v1/"
    headers = dict(client.default_headers)
    assert headers["Modal-Key"] == "wk-id"
    assert headers["Modal-Secret"] == "ws-secret"
    # Non-bearer proxy scheme: the secret also rides as api_key (the SDK requires it non-empty).
    assert client.api_key == "ws-secret"


def test_modal_unauthenticated_client_has_no_modal_headers_and_placeholder_api_key(mocker):
    """Neither proxy token set (``--unauthenticated``) → no Modal headers, ``api_key == "EMPTY"``."""
    _patch_provider(mocker, "modal", modal_authenticated=False)

    agent = build_agent()

    client = agent.model._provider.client
    assert str(client.base_url) == f"{_MODAL_URL}/v1/"
    headers = dict(client.default_headers)
    assert "Modal-Key" not in headers
    assert "Modal-Secret" not in headers
    assert client.api_key == "EMPTY"


def test_build_model_rejects_an_unsupported_provider(mocker):
    """Defensive: a value past the three branches (the settings ``Literal`` blocks it) raises."""
    mocker.patch("decode.agent.factory.settings.llm_provider", "anthropic", create=False)

    with pytest.raises(ValueError, match="unsupported llm_provider"):
        _build_model()
