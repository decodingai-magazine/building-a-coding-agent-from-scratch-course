"""Scripted-agent builder shared by the Headless Runtime tests (ADR-0008).

A ``FunctionModel``-backed real decode agent (all tools registered), injected through the
``_build_runtime_agent`` seam so a flow round-trips offline with no network model call. It lives in
``tests/support`` — a regular importable module, not a ``conftest`` — alongside
:mod:`support.runtime_fixtures`; the runtime store-isolation fixtures are registered at the rootdir
conftest so they apply in any collection order (see that module for the importlib reason, task 065).
Import it as ``from support.runtime_agents import make_scripted_agent``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from decode.agent.deps import AgentDeps
from decode.tools.registry import register_tools

# A model leg: a callable producing the next ``ModelResponse`` from the message history.
ModelLeg = Callable[[list[ModelResponse], AgentInfo], ModelResponse]


def make_scripted_agent(
    responses: Sequence[ModelResponse],
    *,
    name: str = "decode-runtime",
) -> tuple[Agent[AgentDeps, str | DeferredToolRequests], dict[str, int]]:
    """Build a real decode agent (all tools registered) on a scripted ``FunctionModel``.

    ``responses`` is replayed one per model leg (the last one repeats if the agent asks for more),
    so a test scripts e.g. ``[<call write>, <final text>]``. Returns the agent plus a mutable
    ``{"legs": n}`` counter the caller can assert against (e.g. to prove a replay served the turn
    from cache without a fresh model leg). The agent carries ``deps_type=AgentDeps`` and
    ``output_type=[str, DeferredToolRequests]`` exactly like ``build_agent()``.
    """
    counter = {"legs": 0}

    def model_fn(messages: list[ModelResponse], info: AgentInfo) -> ModelResponse:
        index = min(counter["legs"], len(responses) - 1)
        counter["legs"] += 1
        return responses[index]

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(model_fn),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        name=name,
    )
    register_tools(agent)
    return agent, counter
