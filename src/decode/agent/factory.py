"""Build the ``decode`` Pydantic AI agent on the configured **LLM Provider**.

The factory owns the Provider Seam: :func:`_build_model` branches on ``settings.llm_provider``
(``gemini`` / ``openrouter`` / ``modal``); everything else in :func:`build_agent` is
provider-agnostic, and the loop owns all runtime behaviour. Tools come from the flat
:mod:`decode.tools.registry` (the single source of truth); ``output_type=[str,
DeferredToolRequests]`` wires the permission gate's deferred-tool seam.
See ADR-0002 §1-2 and ADR-0005 (provider construction facts live on :func:`_build_model`).
"""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.memory.service import assemble_memory
from decode.skills.catalog import assemble_skills_catalog
from decode.tools.agent import set_main_agent
from decode.tools.registry import register_tools

logger = logging.getLogger(__name__)

# Static base system prompt; memory-file injection is layered on top by the instructions hook.
_BASE_INSTRUCTIONS = (
    "You are Decode, a terminal coding agent that helps a developer in their working "
    "directory. You are concise and precise: answer directly, prefer running the work over "
    "describing it, and never invent file contents or command output you have not seen. "
    "When you do not have a tool for something yet, say so plainly rather than pretending."
)


def build_agent(
    *, flow_mode: bool = False, model: str | None = None
) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """Construct the agent on the configured LLM Provider + register the flat tools (ADR-0002 §1-3,7).

    ``flow_mode`` is the Credentials Proxy seam (ADR-0008 §5): only a headless flow build with
    ``settings.runtime_credentials_proxy_enabled`` resolves the provider key from a Kitaru secret.
    ``model`` is the Model Override (ADR-0010 §2): overrides only the active provider's model id
    (never the provider); being a flow input is what lets Kitaru swap it on a what-if Replay.
    """
    built_model = _build_model(flow_mode=flow_mode, model=model)
    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        built_model,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        # Gemini 2.5 sometimes returns empty/thinking-only turns after a tool call; the default
        # output-retry budget of 1 then aborts real multi-turn runs. Give it a few attempts.
        output_retries=3,
    )
    register_tools(agent)
    _register_instructions(agent)
    # Subagent-spawn seam (ADR-0013 §6): the ``agent`` tool re-enters this Agent for every Explore
    # child, reusing its model + client; one ``agent.override(model=…)`` covers parent + children.
    set_main_agent(agent)
    logger.debug("built agent on llm_provider=%s", settings.llm_provider)
    return agent


def _flow_mode_http_client() -> httpx.AsyncClient:
    """A keep-alive-free httpx client for flow-mode (headless ``decode run`` / replay) provider calls.

    Under Kitaru's ``"calls"`` strategy each model call runs in its own event loop; a pooled
    keep-alive connection reused across loops dies with ``RuntimeError('Event loop is closed')``.
    The 120s timeout replaces httpx's 5s default, which google-genai forwards as the request
    deadline — Gemini rejects deadlines under its 10s minimum (400).
    ponytail: the client is not closed; the short-lived headless process reclaims it on exit.
    """
    return httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=0), timeout=httpx.Timeout(120.0)
    )


def _build_model(*, flow_mode: bool = False, model: str | None = None) -> Model:
    """Build the Pydantic AI model for ``settings.llm_provider`` — the Provider Seam (ADR-0005 §3-5).

    ``model`` is the optional Model Override (ADR-0010 §2): ``model or settings.<provider>_model``
    per branch — never the provider branch or the auth/key path; no cross-provider swap. Branch
    facts verified against the installed SDKs: ``gemini`` uses the google-gla API-key path and never
    passes ``vertexai=`` (its deprecation warning would fail under ``filterwarnings=["error"]``);
    ``openrouter`` / ``modal`` both ride ``OpenAIChatModel``, modal over a bespoke ``AsyncOpenAI``
    client for its per-user ``base_url`` + dual-header proxy-token auth (both-or-neither enforced
    upstream by the cli guard). ``gemini`` / ``openrouter`` source their key through
    :func:`_provider_api_key` (the Credentials Proxy seam, ADR-0008 §5); modal is deliberately not
    on the proxy (a header surface, not a single api_key). The trailing :class:`ValueError` is
    defensive — the settings ``Literal`` blocks it upstream.
    """
    provider = settings.llm_provider
    if provider == "gemini":
        # Flow mode hands Gemini a keep-alive-free client (see _flow_mode_http_client); openrouter/
        # modal share the latent issue — wire the same client through their AsyncOpenAI when needed.
        return GoogleModel(
            model or settings.gemini_model,
            provider=GoogleProvider(
                api_key=_provider_api_key("gemini", flow_mode=flow_mode),
                http_client=_flow_mode_http_client() if flow_mode else None,
            ),
        )
    if provider == "openrouter":
        return OpenAIChatModel(
            model or settings.openrouter_model,
            provider=OpenRouterProvider(
                api_key=_provider_api_key("openrouter", flow_mode=flow_mode)
            ),
        )
    if provider == "modal":
        base_url = f"{settings.modal_endpoint_url}/v1"
        token_id = settings.modal_proxy_token_id.get_secret_value()
        token_secret = settings.modal_proxy_token_secret.get_secret_value()
        if token_id and token_secret:
            # Authenticated endpoint: dual Modal-Key / Modal-Secret proxy headers (not Bearer).
            client = AsyncOpenAI(
                base_url=base_url,
                api_key=token_secret,  # non-empty; the OpenAI client requires it
                default_headers={"Modal-Key": token_id, "Modal-Secret": token_secret},
            )
        else:
            # --unauthenticated endpoint: no Modal headers; placeholder api_key (SDK needs non-empty).
            client = AsyncOpenAI(base_url=base_url, api_key="EMPTY")
        return OpenAIChatModel(
            model or settings.modal_endpoint_model,
            provider=OpenAIProvider(openai_client=client),
        )
    raise ValueError(f"unsupported llm_provider: {provider!r}")


# Kitaru secret key name per proxy-eligible provider (env-var-style names; ADR-0008 §5). ``modal``
# is deliberately absent (dual-token header auth). Public: the cli pre-flight reads the key name.
PROXY_SECRET_KEY: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _provider_api_key(provider: Literal["gemini", "openrouter"], *, flow_mode: bool) -> str:
    """Resolve the provider API key — from settings, or the Kitaru Credentials Proxy (ADR-0008 §5).

    Only a flow-mode run with ``settings.runtime_credentials_proxy_enabled`` resolves it from a
    Kitaru secret, so a deployed flow payload carries the secret *name*, not the raw key.
    """
    if flow_mode and settings.runtime_credentials_proxy_enabled:
        return resolve_provider_key_via_proxy(provider)
    secret = settings.gemini_api_key if provider == "gemini" else settings.openrouter_api_key
    return secret.get_secret_value()


def resolve_provider_key_via_proxy(provider: Literal["gemini", "openrouter"]) -> str:
    """Read the provider API key from a Kitaru secret — the Credentials Proxy (ADR-0008 §5).

    ``kitaru`` is imported lazily so the interactive REPL path never imports it. A missing secret
    surfaces Kitaru's own ``KitaruRuntimeError``; a secret lacking the provider key raises a
    guidance error — never a silent fallback to the settings key. The raw key is returned but
    never logged (only the secret + key *names* are).
    """
    from kitaru import get_secret

    secret_name = settings.runtime_secret_name
    key_name = PROXY_SECRET_KEY[provider]
    secret = get_secret(secret_name)  # raises KitaruRuntimeError if the secret is absent
    api_key = secret.get(key_name)
    if not api_key:
        raise RuntimeError(
            f"Kitaru secret {secret_name!r} has no {key_name!r} value — create it with "
            f"`kitaru secrets set {secret_name} --{key_name}=…`, or unset "
            "RUNTIME_CREDENTIALS_PROXY_ENABLED to read the key from settings."
        )
    logger.debug(
        "resolved provider=%s key via the Kitaru Credentials Proxy (secret=%r, key=%r)",
        provider,
        secret_name,
        key_name,
    )
    return api_key


def _register_instructions(agent: Agent[AgentDeps, str | DeferredToolRequests]) -> None:
    """Assemble the whole system prompt as ONE instructions block (ADR-0002 §8, ADR-0003 §6-7, ADR-0004 §1,§9).

    Four parts (base, persona prompt, memory, Skills Catalog) join into a SINGLE instructions
    source because ``OpenAIChatModel`` emits one ``system`` message per source and strict
    OpenAI-compatible servers (vLLM behind Modal, some OpenRouter models) reject more than one.
    It stays a dynamic per-run hook so persona/memory/skills are read fresh each turn with no
    agent rebuild; empty parts are dropped.
    """

    @agent.instructions
    def assemble_instructions(ctx: RunContext[AgentDeps]) -> str:
        # Memory + skills are HARNESS artifacts: they read harness_home (the launch cwd), not
        # cwd — in a sandbox mode cwd is the Workspace (ADR-0012 §6).
        harness_home = ctx.deps.harness_home or ctx.deps.cwd
        parts = (
            _BASE_INSTRUCTIONS,
            ctx.deps.active_agent.prompt,
            assemble_memory(harness_home),
            assemble_skills_catalog(harness_home),
        )
        return "\n\n".join(part for part in parts if part)
