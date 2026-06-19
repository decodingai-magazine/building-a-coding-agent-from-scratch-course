"""Build the ``decode`` Pydantic AI agent on Gemini (ADR-0002 §1-2).

The factory is the **provider-swap seam**: M1 wires Gemini via the ``google-gla`` API-key
path; M2 swaps in OpenRouter / Modal here without touching the loop. It is intentionally
thin — construct the :class:`~pydantic_ai.Agent`, nothing else — so the loop in
:mod:`decode.agent.loop` owns all the runtime behaviour.

Two construction facts confirmed against the installed SDK (pydantic-ai 1.107, google-genai
2.9), recorded so the choice is not re-litigated:

* **google-gla, not Vertex.** ``GoogleProvider(api_key=...)`` builds a
  ``google.genai.Client(vertexai=False, ...)`` — the Generative-Language endpoint. We pass
  the key explicitly from :data:`decode.config.settings` (the single config reader) rather
  than relying on the ``GEMINI_API_KEY`` env fallback, and we never pass ``vertexai=`` (any
  Vertex/Cloud argument, including ``vertexai=False``, raises a deprecation warning that
  ``filterwarnings=["error"]`` would turn into a test failure).
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

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.tools.registry import register_tools

logger = logging.getLogger(__name__)

# Static base system prompt: decode is a terminal coding agent. Kept deliberately short for
# M1 — memory-file injection (AGENTS.md / MEMORY.md) is layered on top in task 012.
_BASE_INSTRUCTIONS = (
    "You are decode, a terminal coding agent that helps a developer in their working "
    "directory. You are concise and precise: answer directly, prefer running the work over "
    "describing it, and never invent file contents or command output you have not seen. "
    "When you do not have a tool for something yet, say so plainly rather than pretending."
)


def build_agent() -> Agent[AgentDeps, str | DeferredToolRequests]:
    """Construct the Gemini agent and register the flat tool set (ADR-0002 §1-3,7).

    The model id comes from ``settings.gemini_model`` and the API key from
    ``settings.gemini_api_key`` (both config-driven). ``deps_type=AgentDeps`` is what the
    loop validates ``deps=`` against; ``output_type=[str, DeferredToolRequests]`` lets a run
    resolve to a deferred-tool result so the loop can route gated calls through the gate. The
    flat :mod:`decode.tools.registry` registers every tool (the gated ``noop`` plus the
    read-only ``read`` / ``glob`` / ``grep``) so the whole permission path is exercised.
    """
    provider = GoogleProvider(api_key=settings.gemini_api_key.get_secret_value())
    model = GoogleModel(settings.gemini_model, provider=provider)
    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        model,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        instructions=_BASE_INSTRUCTIONS,
    )
    register_tools(agent)
    logger.debug("built Gemini agent on model=%s (google-gla)", settings.gemini_model)
    return agent
