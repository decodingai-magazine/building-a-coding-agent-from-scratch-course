"""Build the ``decode`` Pydantic AI agent on the configured **LLM Provider** (ADR-0002 §1-2, ADR-0005).

The factory owns the **Provider Seam** ADR-0002 §1 promised and ADR-0005 fills: model
construction is delegated to :func:`_build_model`, which branches on ``settings.llm_provider``
(``gemini`` / ``openrouter`` / ``modal``). The rest of :func:`build_agent` —
``deps_type=AgentDeps``, ``output_type=[str, DeferredToolRequests]``, ``register_tools``, and the
single ``@agent.instructions`` hook (one assembled system message — see :func:`_register_instructions`)
— is provider-agnostic, so a provider swap touches one branch and nothing in the loop
(:mod:`decode.agent.loop` owns all the runtime behaviour). The factory stays thin — construct the
:class:`~pydantic_ai.Agent`, nothing else.

Three construction facts confirmed against the installed SDK (pydantic-ai 1.107, openai 2.43,
google-genai 2.9), recorded so the choice is not re-litigated:

* **gemini — google-gla, not Vertex.** ``GoogleProvider(api_key=...)`` builds a
  ``google.genai.Client(vertexai=False, ...)`` — the Generative-Language endpoint. We pass
  the key explicitly from :data:`decode.config.settings` (the single config reader) rather
  than relying on the ``GEMINI_API_KEY`` env fallback, and we never pass ``vertexai=`` (any
  Vertex/Cloud argument, including ``vertexai=False``, raises a deprecation warning that
  ``filterwarnings=["error"]`` would turn into a test failure).
* **openrouter / modal — one OpenAI-compatible model class.** Both ride
  :class:`~pydantic_ai.models.openai.OpenAIChatModel`; only modal needs a custom
  :class:`~openai.AsyncOpenAI` client, because of its per-user ``base_url`` and the optional
  dual-header proxy-token auth (``Modal-Key`` / ``Modal-Secret``, not the OpenAI
  ``Authorization: Bearer`` scheme — ADR-0005 §5). An ``--unauthenticated`` endpoint takes no
  Modal headers and a placeholder ``api_key="EMPTY"`` (the SDK requires it non-empty).
* **Deferred-tool seam now.** ``output_type=[str, DeferredToolRequests]`` is set so a run can
  resolve to a deferred-tool-requests result. This wires the permission gate: a gated tool
  raises ``ApprovalRequired``, the run resolves to ``DeferredToolRequests``, and the loop
  routes it through the gate before resuming.

Tools are registered via the flat :mod:`decode.tools.registry` (task 006) — the factory does
not hand-register individual tools; the registry is the single source of truth for which tools
exist and which are read-only.
"""

from __future__ import annotations

import logging

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
from decode.tools.registry import register_tools

logger = logging.getLogger(__name__)

# Static base system prompt: decode is a terminal coding agent. Kept deliberately short for
# M1 — memory-file injection (AGENTS.md / MEMORY.md) is layered on top in task 012.
_BASE_INSTRUCTIONS = (
    "You are Decode, a terminal coding agent that helps a developer in their working "
    "directory. You are concise and precise: answer directly, prefer running the work over "
    "describing it, and never invent file contents or command output you have not seen. "
    "When you do not have a tool for something yet, say so plainly rather than pretending."
)


def build_agent() -> Agent[AgentDeps, str | DeferredToolRequests]:
    """Construct the agent on the configured LLM Provider + register the flat tools (ADR-0002 §1-3,7).

    Model construction is delegated to :func:`_build_model` — the Provider Seam that branches on
    ``settings.llm_provider`` (ADR-0005 §3). Everything here is provider-agnostic:
    ``deps_type=AgentDeps`` is what the loop validates ``deps=`` against;
    ``output_type=[str, DeferredToolRequests]`` lets a run resolve to a deferred-tool result so the
    loop can route gated calls through the gate. The flat :mod:`decode.tools.registry` registers the
    real M1 tools (``read`` / ``glob`` / ``grep`` / ``write`` / ``edit`` / ``bash`` / ``todo_write`` /
    ``web_fetch`` / ``ask_user``); the task-005 scaffolding ``noop`` is deliberately not among them.
    """
    model = _build_model()
    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        model,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )
    register_tools(agent)
    _register_instructions(agent)
    logger.debug("built agent on llm_provider=%s", settings.llm_provider)
    return agent


def _build_model() -> Model:
    """Build the Pydantic AI model for ``settings.llm_provider`` — the Provider Seam (ADR-0005 §3-5).

    Three branches, each verified offline against the installed SDK (no model request is issued by
    constructing a model):

    * ``gemini`` — :class:`~pydantic_ai.models.google.GoogleModel` on the google-gla API-key path;
      never passes ``vertexai=`` (``filterwarnings=["error"]`` would fail on the deprecation warning).
    * ``openrouter`` — :class:`~pydantic_ai.models.openai.OpenAIChatModel` via
      :class:`~pydantic_ai.providers.openrouter.OpenRouterProvider` (``model.system == "openrouter"``).
    * ``modal`` — ``OpenAIChatModel`` over a custom :class:`~openai.AsyncOpenAI` client (its per-user
      ``base_url`` and the optional dual-header proxy-token auth need a bespoke client). Both proxy
      tokens set → ``Modal-Key`` + ``Modal-Secret`` headers (the secret also rides as ``api_key``,
      which the SDK requires non-empty); neither set (an ``--unauthenticated`` endpoint) → no Modal
      headers and a placeholder ``api_key="EMPTY"``. The both-or-neither invariant is enforced
      upstream at the cli guard (task 039), so here modal is fully authed or fully unauthed.

    A value past the three branches raises :class:`ValueError` — defensive only; the settings
    ``Literal`` blocks it upstream, but a future literal added before its branch is wired is caught.
    """
    provider = settings.llm_provider
    if provider == "gemini":
        return GoogleModel(
            settings.gemini_model,
            provider=GoogleProvider(api_key=settings.gemini_api_key.get_secret_value()),
        )
    if provider == "openrouter":
        return OpenAIChatModel(
            settings.openrouter_model,
            provider=OpenRouterProvider(api_key=settings.openrouter_api_key.get_secret_value()),
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
            settings.modal_endpoint_model,
            provider=OpenAIProvider(openai_client=client),
        )
    raise ValueError(f"unsupported llm_provider: {provider!r}")


def _register_instructions(agent: Agent[AgentDeps, str | DeferredToolRequests]) -> None:
    """Assemble the whole system prompt as ONE instructions block (ADR-0002 §8, ADR-0003 §6-7, ADR-0004 §1,§9).

    decode's system prompt has four parts: the static base, the active Agent persona's prompt, project
    memory (``AGENTS.md`` / ``MEMORY.md``), and the Skills Catalog. They are joined into a **single**
    ``@agent.instructions`` string rather than registered as four separate instruction sources, because
    pydantic-ai's :class:`~pydantic_ai.models.openai.OpenAIChatModel` emits **one ``system`` message per
    instruction source**, and strict OpenAI-compatible servers reject more than one — the vLLM chat
    template behind a Modal Auto Endpoint (and some OpenRouter models) raise *"System message must be at
    the beginning."* on the second one. One source → one system message → portable across every
    provider. (``GoogleModel`` already concatenated them, so gemini's behaviour is unchanged.)

    It stays a **dynamic** per-run hook so the persona, memory, and skills are read fresh each turn — an
    ``/agent`` switch, an edited ``AGENTS.md`` / ``MEMORY.md``, or a freshly dropped-in skill all take
    effect on the next turn with no agent rebuild. Each part contributes nothing when empty
    (``assemble_memory`` / ``assemble_skills_catalog`` return ``""`` and are dropped), so no empty
    headers ride and a no-memory / no-skills run is just the base + persona.
    """

    @agent.instructions
    def assemble_instructions(ctx: RunContext[AgentDeps]) -> str:
        parts = (
            _BASE_INSTRUCTIONS,
            ctx.deps.active_agent.prompt,
            assemble_memory(ctx.deps.cwd),
            assemble_skills_catalog(ctx.deps.cwd),
        )
        return "\n\n".join(part for part in parts if part)
