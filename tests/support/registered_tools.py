"""Read the tools registered on an Agent through pydantic-ai's **public** surface.

``Agent.toolsets`` (public, documented as "all toolsets registered on the agent, including a
function toolset holding tools that were registered on the agent directly") is the supported way
in; a decode agent has exactly ONE entry — the :class:`~pydantic_ai.toolsets.FunctionToolset`
that :func:`decode.tools.registry.register_tools` filled via ``agent.tool``. Its ``tools`` field
is the public ``{name: Tool}`` dict, the same object the private ``agent._function_toolset.tools``
used to reach. :func:`registered_tools` asserts that position invariant instead of assuming it,
so the shortcut cannot rot silently on a pydantic-ai upgrade.

**Registration, not visibility.** ``TestModel.last_model_request_parameters.function_tools`` is
also public, but it reports what the model was *offered* — the set left after each tool's
``prepare=`` callback filtered it by the active persona's allowlist (ADR-0003 §6). Tests that
assert what the registry put on the agent read this dict, so editing a persona can never break a
registration test, and "``noop`` is not registered" keeps meaning *unregistered* rather than the
weaker *hidden for this run*.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent
from pydantic_ai.tools import Tool
from pydantic_ai.toolsets import FunctionToolset


def registered_tools(agent: Agent[Any, Any]) -> dict[str, Tool[Any]]:
    """Return ``{tool name: Tool}`` for every tool registered on ``agent``.

    Guards the two assumptions the public path rests on: the agent carries exactly one toolset,
    and that toolset is the function toolset holding directly-registered tools.
    """
    toolsets = agent.toolsets
    assert len(toolsets) == 1, f"expected exactly one toolset, got {len(toolsets)}: {toolsets}"
    toolset = toolsets[0]
    assert isinstance(toolset, FunctionToolset), f"not the function toolset: {type(toolset)}"
    return toolset.tools
