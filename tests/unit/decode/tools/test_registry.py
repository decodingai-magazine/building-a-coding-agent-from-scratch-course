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
from decode.tools.registry import TOOL_READ_ONLY, TOOL_SPECS, register_tools


def _agent(mocker):
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


def test_registry_lists_the_expected_tools():
    names = {spec.name for spec in TOOL_SPECS}
    assert names == {
        "read",
        "glob",
        "grep",
        "write",
        "edit",
        "bash",
        "todo_write",
        "web_fetch",
        "ask_user",
    }


def test_registry_does_not_expose_the_scaffolding_noop_tool():
    # ADR-0002 §7 + AGENTS.md: the task-005 ``noop`` scaffolding is superseded by the real
    # tools (006-011) and must NOT ride on the production agent. It survives only as a
    # TEST-ONLY helper (decode.tools.noop.register_noop), never in the registry.
    assert "noop" not in {spec.name for spec in TOOL_SPECS}
    assert "noop" not in TOOL_READ_ONLY
    # Unknown tools (including ``noop``) default to mutating via the loop's lookup.
    assert is_read_only("noop") is False


def test_read_only_flags_match_each_spec():
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    assert by_name["read"].read_only is True
    assert by_name["glob"].read_only is True
    assert by_name["grep"].read_only is True
    # The mutating file tools are NOT read-only (gated/asked on every call).
    assert by_name["write"].read_only is False
    assert by_name["edit"].read_only is False
    # bash mutates the world: NOT read-only, gated/asked on every call.
    assert by_name["bash"].read_only is False
    # todo_write has session side effects (it rewrites the task store): NOT read-only.
    assert by_name["todo_write"].read_only is False
    # web_fetch has no local side effect (network egress only): tagged read-only, still asked.
    assert by_name["web_fetch"].read_only is True
    # ask_user is the human-interaction tool: NOT read-only (it blocks the turn on the user).
    assert by_name["ask_user"].read_only is False


def test_every_tool_is_gated_except_ask_user():
    # ADR-0002 §3: every side-effecting tool is gated; ask_user is the lone exception —
    # it IS the human-interaction tool, so gating it would double-prompt.
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    assert by_name["ask_user"].gated is False
    for name, spec in by_name.items():
        if name != "ask_user":
            assert spec.gated is True, f"{name} must be gated"


def test_is_read_only_reflects_the_registered_flags():
    # The loop consults this exact function when building a PermissionRequest.
    assert is_read_only("read") is True
    assert is_read_only("glob") is True
    assert is_read_only("grep") is True
    assert is_read_only("write") is False
    assert is_read_only("edit") is False
    assert is_read_only("bash") is False
    assert is_read_only("todo_write") is False
    # web_fetch is tagged read-only (no local side effect); still asked in v1.
    assert is_read_only("web_fetch") is True
    # ask_user is NOT read-only (it blocks the turn on the user).
    assert is_read_only("ask_user") is False
    # Unknown tools default to mutating (gated).
    assert is_read_only("does-not-exist") is False


def test_register_tools_registers_every_spec_on_the_agent(mocker):
    agent = _agent(mocker)

    # build_agent already registers via the registry; the real M1 tools must be on the agent...
    registered = set(agent._function_toolset.tools)
    assert {
        "read",
        "glob",
        "grep",
        "write",
        "edit",
        "bash",
        "todo_write",
        "web_fetch",
        "ask_user",
    } <= registered
    # ...and the scaffolding ``noop`` must NOT be (it is never registered in production).
    assert "noop" not in registered


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
