"""Unit tests for :func:`decode.agent.factory.build_agent`.

ADR-0002 §1-2 / ADR-0005 §3-5: the agent is a Pydantic AI :class:`~pydantic_ai.Agent` built on
the configured **LLM Provider** — model construction is delegated to the ``_build_model()``
Provider Seam (gemini via the ``google-gla`` API-key path, openrouter and modal via
``OpenAIChatModel``). ``output_type=[str, DeferredToolRequests]`` keeps the deferred-tool seam
ready. These tests assert the *construction contract* without making any network call (no model
request is issued just by building the agent — every provider constructs offline).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from decode.agent.deps import AgentDeps
from decode.agent.factory import _build_model, _flow_mode_http_client, build_agent
from decode.agents.loader import load_agent
from decode.entities.agent_def import AgentDef
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.tools import agent as agent_tool_module


class _StubSpawnAgent:
    """A stand-in main Agent for the ``agent`` tool's spawn seam (ADR-0013 §6).

    The tool-visibility tests drive ``TestModel(call_tools='all')`` to enumerate the offered schema —
    which now includes ``agent``. Calling ``agent`` for real would spawn an Explore child whose
    read-only tools a bare TestModel would drive with dummy args (``lsp`` then fails its op validation).
    Patching ``_require_main_agent`` to return this stub folds a fixed report instead, so the child is
    never spawned and the visibility assertions stay about ``prepare=``, not about sub-runs.
    """

    async def run(self, prompt, *, deps, usage_limits, event_stream_handler=None):
        return SimpleNamespace(output="explored")


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
    # Avoid touching real Gemini credentials/env: feed the key from settings explicitly. Pin the
    # provider so a developer's local `.env` (e.g. `LLM_PROVIDER=modal`) can't flip this off gemini.
    mocker.patch("decode.agent.factory.settings.llm_provider", "gemini", create=False)
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    mocker.patch("decode.agent.factory.settings.gemini_model", "gemini-2.5-flash", create=False)

    agent = build_agent()

    assert isinstance(agent.model, GoogleModel)
    assert agent.model.model_name == "gemini-2.5-flash"
    # google-gla (Generative-Language), NOT Vertex: the provider's system name is "google-gla"
    # (pydantic-ai 1.x — it was "google" under 2.0; ADR-0009).
    assert isinstance(agent.model._provider, GoogleProvider)
    assert agent.model.system == "google-gla"


def test_build_agent_respects_a_custom_model_id(mocker):
    mocker.patch("decode.agent.factory.settings.llm_provider", "gemini", create=False)
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


def test_build_agent_registers_a_single_instructions_hook(mocker):
    """ONE instructions source → ONE ``system`` message (regression guard).

    decode's base + active persona + memory + Skills Catalog are assembled inside a single
    ``@agent.instructions`` hook, NOT registered as separate sources, because
    ``OpenAIChatModel`` emits one ``system`` message per instruction source and strict
    OpenAI-compatible servers reject more than one — the vLLM chat template behind a Modal Auto
    Endpoint (and some OpenRouter models) raise "System message must be at the beginning." on the
    second one. So there is no static ``instructions=`` string and exactly one callable entry; the
    content of each part is asserted by the per-part injection tests below.
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )

    agent = build_agent()

    static_strings = [p for p in agent._instructions if isinstance(p, str)]
    callables = [p for p in agent._instructions if callable(p)]
    assert static_strings == []  # no separate static base → no extra system message
    assert len(callables) == 1  # the single assembled-instructions hook


async def test_memory_is_injected_into_the_first_request_instructions(tmp_path, mocker):
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


async def test_memory_injection_reads_harness_home_not_the_workspace_cwd(tmp_path, mocker):
    """ADR-0012 §6: memory is a harness artifact → injected from ``harness_home``, not the sandbox cwd.

    With ``cwd`` (the Workspace) and ``harness_home`` (the launch cwd) distinct, the ``AGENTS.md`` under
    Harness Home reaches the model, while an ``AGENTS.md`` that only exists in the Workspace does NOT —
    proving the instructions hook reads ``harness_home``.
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (home / "AGENTS.md").write_text("HARNESS-HOME RULE: from home", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("WORKSPACE RULE: should not load", encoding="utf-8")
    deps = AgentDeps(
        cwd=workspace,
        harness_home=home,
        emit=lambda event: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=_no_user_resolver,
    )

    agent = build_agent()
    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        result = await agent.run("hi", deps=deps)

    first = result.all_messages()[0]
    assert isinstance(first, ModelRequest)
    assert first.instructions is not None
    assert "HARNESS-HOME RULE: from home" in first.instructions
    assert "WORKSPACE RULE" not in first.instructions  # the Workspace AGENTS.md is not injected
    assert f"# From {home / 'AGENTS.md'}" in first.instructions


async def test_build_agent_retries_empty_model_responses_before_giving_up(tmp_path, mocker):
    """build_agent() raises the output-retry budget so a few empty/thinking-only turns don't abort.

    Gemini 2.5 sometimes returns an empty (thinking-only) response after a tool call; pydantic-ai
    re-requests on an empty turn, but its default output-retry budget is 1 — a *second* empty turn
    aborts a real ``decode run`` with "Exceeded maximum output retries". Two empty turns then text must
    succeed here, which only holds because ``build_agent`` sets ``output_retries`` above the default 1.
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    calls = {"n": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] <= 2:
            return ModelResponse(parts=[])  # empty / thinking-only, like Gemini 2.5 post-tool
        return ModelResponse(parts=[TextPart("done")])

    agent = build_agent()
    with agent.override(model=FunctionModel(model_fn)):
        result = await agent.run("hi", deps=_deps(tmp_path))

    assert result.output == "done"
    assert calls["n"] == 3  # two empty turns tolerated, the third returns text


async def test_build_agent_gives_tools_a_modelretry_budget_beyond_one(tmp_path, mocker):
    """build_agent() raises the per-tool ``ModelRetry`` budget so consecutive nags don't abort.

    Tools coach the model with ``ModelRetry`` (``edit`` "old_string not found", ``grep`` "no
    matches"); pydantic-ai's default per-tool budget of 1 turns the SECOND consecutive failure
    into ``UnexpectedModelBehavior``, killing the whole turn (the demo-2-bug-hunt crash). Three
    failures then a success must complete — which only holds because ``build_agent`` sets the
    Agent-wide ``retries`` above the default 1.
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    attempts = {"n": 0}

    def wobbly(ctx: RunContext[AgentDeps]) -> str:
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise ModelRetry("not yet; call wobbly again.")
        return "wobbly: ok"

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last_parts = messages[-1].parts
        if any(isinstance(p, UserPromptPart | RetryPromptPart) for p in last_parts):
            return ModelResponse(parts=[ToolCallPart(tool_name="wobbly", args="{}")])
        return ModelResponse(parts=[TextPart("done")])

    agent = build_agent()
    agent.tool(wobbly)  # no per-tool override: inherits the Agent default — the value under test
    with agent.override(model=FunctionModel(model_fn)):
        result = await agent.run("hi", deps=_deps(tmp_path))

    assert result.output == "done"
    assert attempts["n"] == 4  # three ModelRetry nags tolerated, the fourth call succeeds


def test_flow_mode_http_client_is_loop_safe_with_a_valid_deadline():
    """The flow-mode provider client disables keep-alive and sets a deadline ≥ Gemini's 10s minimum.

    Both values are load-bearing and only fail on a real cross-loop run (so they can't be caught by a
    scripted-model test) — guard them here. Keep-alive off stops Kitaru's per-call event loops from
    reusing a connection across loops (``RuntimeError('Event loop is closed')``); the deadline (httpx's
    read timeout, which google-genai forwards) must clear Gemini's 10s floor or the API returns 400.
    """
    client = _flow_mode_http_client()
    # google-genai forwards httpx's read timeout as the request deadline; Gemini rejects < 10s.
    assert client.timeout.read is not None and client.timeout.read >= 10
    # No pooled keep-alive connection can outlive a checkpoint's event loop (deep httpx internals; pinned).
    assert client._transport._pool._max_keepalive_connections == 0


def test_gemini_flow_mode_wires_the_loop_safe_http_client(mocker):
    """`_build_model(flow_mode=True)` for gemini reaches for the loop-safe client; the REPL path doesn't.

    Complements the isolated ``_flow_mode_http_client`` test by proving the *wiring* — that flow mode
    (Kitaru's per-call event loops) attaches the keep-alive-free client, while the one-loop interactive
    path keeps httpx's default keep-alive. Spying on the helper avoids reaching into ``GoogleModel``
    internals for the attached client.
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    mocker.patch("decode.agent.factory.settings.llm_provider", "gemini")
    spy = mocker.patch("decode.agent.factory._flow_mode_http_client", wraps=_flow_mode_http_client)

    _build_model(flow_mode=True)
    assert spy.call_count == 1  # flow mode builds the keep-alive-free client

    _build_model(flow_mode=False)
    assert spy.call_count == 1  # interactive path does NOT (one event loop — keep-alive is fine)


def test_flow_mode_reads_the_gemini_key_from_settings(mocker):
    """ADR-0015 §4: flow mode no longer changes key sourcing — the key comes from ``Settings``, period.

    The retired model-key secret resolution used to divert a flow-mode build to ``kitaru.get_secret``.
    It is deleted: a flow-mode build reads the same settings ``SecretStr`` the REPL does, and no
    secret-store lookup happens (``flow_mode`` now only selects the keep-alive-free HTTP client,
    ADR-0010 §3).
    """
    mocker.patch("decode.agent.factory.settings.llm_provider", "gemini")
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key",
        SecretStr("settings-gemini-key"),
        create=False,
    )
    mocker.patch("decode.agent.factory.settings.gemini_model", "gemini-2.5-flash")
    get_secret = mocker.patch("kitaru.get_secret")

    agent = build_agent(flow_mode=True)

    assert agent.model._provider.client._api_client.api_key == "settings-gemini-key"
    get_secret.assert_not_called()  # no kitaru secret-store lookup on the key path any more


def test_flow_mode_reads_the_openrouter_key_from_settings(mocker):
    """The same settings-only key sourcing for the other single-api-key provider (ADR-0015 §4)."""
    mocker.patch("decode.agent.factory.settings.llm_provider", "openrouter")
    mocker.patch(
        "decode.agent.factory.settings.openrouter_api_key",
        SecretStr("settings-openrouter-key"),
        create=False,
    )
    mocker.patch("decode.agent.factory.settings.openrouter_model", "openrouter/free")
    get_secret = mocker.patch("kitaru.get_secret")

    agent = build_agent(flow_mode=True)

    assert agent.model._provider.client.api_key == "settings-openrouter-key"
    get_secret.assert_not_called()


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


# per-agent tool restriction via the per-tool prepare= callback (ADR-0003 §6)


async def test_plan_agent_run_omits_write_edit_and_bash_from_the_visible_tools(tmp_path, mocker):
    """With ``active_agent = plan`` the model never sees write/edit/bash (they are hidden).

    A bare ``TestModel`` calls **every** tool in the schema it is offered, so the set of tools it
    actually called == the visible tool schema for the run. Asserting the mutating tools are never
    called is the spike-confirmed proof that ``prepare= -> None`` hid them (ADR-0003 §6). The visible
    ``agent`` tool (ADR-0013 §4) IS called; its spawn seam is stubbed so no real Explore child runs.
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    mocker.patch.object(agent_tool_module, "_require_main_agent", return_value=_StubSpawnAgent())
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
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    mocker.patch.object(agent_tool_module, "_require_main_agent", return_value=_StubSpawnAgent())
    agent = build_agent()
    build = load_agent("build")

    with agent.override(model=TestModel(custom_output_text="ok")):
        result = await agent.run("hi", deps=_deps(tmp_path, active_agent=build))

    called = _tool_names_called(result.all_messages())
    assert {"write", "edit", "bash"} <= called


async def test_tool_visibility_follows_the_active_agent_per_run_without_rebuild(tmp_path, mocker):
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    mocker.patch.object(agent_tool_module, "_require_main_agent", return_value=_StubSpawnAgent())
    agent = build_agent()  # built ONCE
    plan = load_agent("plan")
    build = load_agent("build")

    with agent.override(model=TestModel(custom_output_text="ok")):
        plan_run = await agent.run("hi", deps=_deps(tmp_path, active_agent=plan))
        build_run = await agent.run("hi", deps=_deps(tmp_path, active_agent=build))

    assert "bash" not in _tool_names_called(plan_run.all_messages())
    assert "bash" in _tool_names_called(build_run.all_messages())


# per-agent system prompt via the dynamic instructions hook (ADR-0003 §6,7)


async def test_active_agent_prompt_is_injected_into_the_run_instructions(tmp_path, mocker):
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


# the Skills Catalog injected via the dynamic instructions hook (ADR-0004 §1,§9)


async def test_skills_catalog_is_injected_into_the_run_instructions(tmp_path, mocker):
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


# the _build_model() Provider Seam: one model per llm_provider (ADR-0005 §3-5)
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
        # GoogleModel.system is "google-gla" under pydantic-ai 1.x (was "google" under 2.0; ADR-0009).
        return GoogleModel, "google-gla", "gemini-2.5-flash"
    if provider == "openrouter":
        mocker.patch(
            "decode.agent.factory.settings.openrouter_api_key", SecretStr("or-key"), create=False
        )
        mocker.patch(
            "decode.agent.factory.settings.openrouter_model",
            "openrouter/free",
            create=False,
        )
        return OpenAIChatModel, "openrouter", "openrouter/free"
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
    expected_cls, expected_system, expected_model_name = _patch_provider(
        mocker, provider, modal_authenticated=modal_authenticated
    )

    agent = build_agent()

    assert isinstance(agent.model, expected_cls)
    assert agent.model.system == expected_system
    assert agent.model.model_name == expected_model_name


def test_modal_authenticated_client_carries_both_proxy_headers(mocker):
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


# Model Override: the model id threaded as a flow parameter (ADR-0010 §2, task 067)
#
# ``_build_model`` / ``build_agent`` take ``model: str | None = None``. ``None`` reads
# ``settings.<provider>_model`` (byte-unchanged — the interactive REPL path); a value overrides
# ONLY the active provider's model id. The provider stays ``LLM_PROVIDER``-selected — the override
# never changes the provider class or the auth/key path (permanent non-goal: no cross-provider
# swap). Construction stays offline for every provider (building a model issues no model request).

_OVERRIDE_MODEL_ID = "some-override-model-id"

_PROVIDER_CASES = [
    ("gemini", False),
    ("openrouter", False),
    ("modal", True),
    ("modal", False),
]
_PROVIDER_CASE_IDS = ["gemini", "openrouter", "modal-authenticated", "modal-unauthenticated"]


@pytest.mark.parametrize(
    ("provider", "modal_authenticated"), _PROVIDER_CASES, ids=_PROVIDER_CASE_IDS
)
def test_build_model_with_none_matches_the_settings_default(mocker, provider, modal_authenticated):
    _, expected_system, expected_model_name = _patch_provider(
        mocker, provider, modal_authenticated=modal_authenticated
    )

    model = _build_model(model=None)

    assert model.model_name == expected_model_name
    assert model.system == expected_system


@pytest.mark.parametrize(
    ("provider", "modal_authenticated"), _PROVIDER_CASES, ids=_PROVIDER_CASE_IDS
)
def test_build_model_override_sets_only_the_model_id(mocker, provider, modal_authenticated):
    expected_cls, expected_system, _ = _patch_provider(
        mocker, provider, modal_authenticated=modal_authenticated
    )

    model = _build_model(model=_OVERRIDE_MODEL_ID)

    assert model.model_name == _OVERRIDE_MODEL_ID  # only the id moved to the override
    assert isinstance(model, expected_cls)  # ...the provider class is unchanged
    assert model.system == expected_system  # ...and so is the provider system


def test_gemini_override_keeps_the_google_provider_and_settings_key(mocker):
    _patch_provider(mocker, "gemini")

    model = _build_model(model="gemini-2.5-pro")

    assert model.model_name == "gemini-2.5-pro"
    assert isinstance(model, GoogleModel)
    assert isinstance(model._provider, GoogleProvider)
    # The auth path is byte-identical: the provider still carries the settings-sourced key.
    assert model._provider.client._api_client.api_key == "test-key"


def test_openrouter_override_keeps_the_openrouter_provider_and_key(mocker):
    _patch_provider(mocker, "openrouter")

    model = _build_model(model="some-openrouter-id")

    assert model.model_name == "some-openrouter-id"
    assert isinstance(model, OpenAIChatModel)
    assert isinstance(model._provider, OpenRouterProvider)
    assert model._provider.client.api_key == "or-key"


def test_modal_override_keeps_the_custom_client_and_proxy_headers(mocker):
    _patch_provider(mocker, "modal", modal_authenticated=True)

    model = _build_model(model="some-modal-id")

    assert model.model_name == "some-modal-id"
    assert isinstance(model, OpenAIChatModel)
    assert isinstance(model._provider, OpenAIProvider)
    client = model._provider.client
    assert str(client.base_url) == f"{_MODAL_URL}/v1/"
    headers = dict(client.default_headers)
    assert headers["Modal-Key"] == "wk-id"
    assert headers["Modal-Secret"] == "ws-secret"


def test_build_agent_threads_the_model_override_to_the_model(mocker):
    _patch_provider(mocker, "gemini")

    agent = build_agent(model="gemini-2.5-pro")

    assert agent.model.model_name == "gemini-2.5-pro"


def test_build_agent_without_a_model_is_the_settings_default(mocker):
    _patch_provider(mocker, "gemini")

    agent = build_agent()

    assert agent.model.model_name == "gemini-2.5-flash"
