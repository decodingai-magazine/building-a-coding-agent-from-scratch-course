"""Unit tests for the flat tool registry (``decode.tools.registry``).

ADR-0002 §7: tools live in a **flat registry** — no plugin machinery. The registry is the one
place that (a) registers every tool on the :class:`~pydantic_ai.Agent` and (b) records each
tool's ``read_only`` flag, which the loop reads via :func:`decode.tools.is_read_only` when it
builds a :class:`~decode.entities.permissions.PermissionRequest`.

These tests assert the registry's two jobs without a network call: the agent ends up with all
the expected tools, and the read-only map matches each tool's declared flag.
"""

from pydantic import SecretStr
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.test import TestModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.tools import is_read_only
from decode.tools.registry import TOOL_SPECS, register_tools


def _agent(mocker):
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


def test_registry_lists_the_expected_tools():
    names = {spec.name for spec in TOOL_SPECS}
    assert names == {"noop", "read", "glob", "grep", "write", "edit", "bash"}


def test_read_only_flags_match_each_spec():
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    assert by_name["read"].read_only is True
    assert by_name["glob"].read_only is True
    assert by_name["grep"].read_only is True
    # noop stands in for a mutating tool, so it is NOT read-only (still gated/asked).
    assert by_name["noop"].read_only is False
    # The mutating file tools are NOT read-only (gated/asked on every call).
    assert by_name["write"].read_only is False
    assert by_name["edit"].read_only is False
    # bash mutates the world: NOT read-only, gated/asked on every call.
    assert by_name["bash"].read_only is False


def test_is_read_only_reflects_the_registered_flags():
    # The loop consults this exact function when building a PermissionRequest.
    assert is_read_only("read") is True
    assert is_read_only("glob") is True
    assert is_read_only("grep") is True
    assert is_read_only("noop") is False
    assert is_read_only("write") is False
    assert is_read_only("edit") is False
    assert is_read_only("bash") is False
    # Unknown tools default to mutating (gated).
    assert is_read_only("does-not-exist") is False


def test_register_tools_registers_every_spec_on_the_agent(mocker):
    agent = _agent(mocker)

    # build_agent already registers via the registry; the tools must be on the agent.
    registered = set(agent._function_toolset.tools)
    assert {"noop", "read", "glob", "grep", "write", "edit", "bash"} <= registered


def test_register_tools_registers_every_spec_onto_a_bare_agent():
    # The registry is the single source of truth: pointing it at a fresh agent registers
    # exactly the specs it knows about (no factory in the loop).
    bare: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        TestModel(),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
    )

    register_tools(bare)

    assert set(bare._function_toolset.tools) == {spec.name for spec in TOOL_SPECS}
