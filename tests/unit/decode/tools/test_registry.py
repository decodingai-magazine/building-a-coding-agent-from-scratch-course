"""Unit tests for the flat tool registry (``decode.tools.registry``).

ADR-0002 §7 / ADR-0003 §2: tools live in a **flat registry** — no plugin machinery. The registry
is the one place that (a) registers every tool on the :class:`~pydantic_ai.Agent` and (b) records
each tool's :class:`~decode.permissions.types.ToolKind`, which the loop reads via
:func:`decode.tools.tool_kind` when it builds a
:class:`~decode.entities.permissions.PermissionRequest`. ``is_read_only`` is now derived from the
kind (``kind is READ_ONLY``) so existing callers keep working.

These tests assert the registry's two jobs without a network call: the agent ends up with all
the expected tools, and the tool-kind map matches each tool's declared classification.
"""

from pydantic import SecretStr
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.test import TestModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.permissions.types import ToolKind
from decode.tools import is_read_only, tool_kind
from decode.tools.registry import TOOL_KIND, TOOL_READ_ONLY, TOOL_SPECS, register_tools


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
    # TEST-ONLY helper (support.noop_helper.register_noop), never in the registry.
    assert "noop" not in {spec.name for spec in TOOL_SPECS}
    assert "noop" not in TOOL_KIND
    assert "noop" not in TOOL_READ_ONLY
    # Unknown tools (including ``noop``) default to OTHER (mutating) via the loop's lookup.
    assert tool_kind("noop") is ToolKind.OTHER
    assert is_read_only("noop") is False


def test_tool_kinds_match_each_spec():
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    # Read-only tools (no disk/exec side effect): the three file readers, web_fetch, todo_write.
    assert by_name["read"].kind is ToolKind.READ_ONLY
    assert by_name["glob"].kind is ToolKind.READ_ONLY
    assert by_name["grep"].kind is ToolKind.READ_ONLY
    # web_fetch has no local side effect (network egress only): READ_ONLY.
    assert by_name["web_fetch"].kind is ToolKind.READ_ONLY
    # todo_write is an in-memory checklist with no disk/exec side effect: READ_ONLY (ADR-0003 §2),
    # so it works in plan mode and never prompts.
    assert by_name["todo_write"].kind is ToolKind.READ_ONLY
    # The mutating file tools are FILE_EDIT (edit mode auto-allows these).
    assert by_name["write"].kind is ToolKind.FILE_EDIT
    assert by_name["edit"].kind is ToolKind.FILE_EDIT
    # bash mutates the world via shell exec: OTHER (edit mode still asks for it).
    assert by_name["bash"].kind is ToolKind.OTHER
    # ask_user is the human-interaction tool (ungated — never reaches the gate): OTHER.
    assert by_name["ask_user"].kind is ToolKind.OTHER


def test_tool_kind_reflects_the_registered_kinds():
    # The loop consults this exact function when building a PermissionRequest.
    assert tool_kind("read") is ToolKind.READ_ONLY
    assert tool_kind("glob") is ToolKind.READ_ONLY
    assert tool_kind("grep") is ToolKind.READ_ONLY
    assert tool_kind("web_fetch") is ToolKind.READ_ONLY
    assert tool_kind("todo_write") is ToolKind.READ_ONLY
    assert tool_kind("write") is ToolKind.FILE_EDIT
    assert tool_kind("edit") is ToolKind.FILE_EDIT
    assert tool_kind("bash") is ToolKind.OTHER
    assert tool_kind("ask_user") is ToolKind.OTHER
    # Unknown tools default to OTHER (mutating/gated).
    assert tool_kind("does-not-exist") is ToolKind.OTHER


def test_is_read_only_is_derived_from_the_kind():
    # ADR-0003 §2: read_only is kept (derived as ``kind is READ_ONLY``) so existing callers work.
    assert is_read_only("read") is True
    assert is_read_only("glob") is True
    assert is_read_only("grep") is True
    assert is_read_only("web_fetch") is True
    # todo_write is now READ_ONLY (in-memory checklist, no side effect).
    assert is_read_only("todo_write") is True
    assert is_read_only("write") is False
    assert is_read_only("edit") is False
    assert is_read_only("bash") is False
    # ask_user is OTHER (it blocks the turn on the user, ungated).
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


async def test_restrict_to_active_agent_hides_disallowed_tools(monkeypatch):
    """The per-tool ``prepare=`` callback returns ``None`` for a tool the active agent omits.

    Unit-level proof of ADR-0003 §6: ``_restrict_to_active_agent("bash")`` is the prepare for the
    ``bash`` tool. Given an active agent whose ``tools`` lacks ``bash`` it returns ``None`` (hide);
    given one that includes ``bash`` it returns the unchanged definition (show).
    """
    from pydantic_ai.tools import ToolDefinition

    from decode.agents.loader import load_agent
    from decode.tools.registry import _restrict_to_active_agent

    prepare = _restrict_to_active_agent("bash")
    tool_def = ToolDefinition(name="bash", parameters_json_schema={"type": "object"})

    class _Ctx:
        def __init__(self, agent_name: str) -> None:
            self.deps = type("D", (), {"active_agent": load_agent(agent_name)})()

    # plan omits bash → hidden; build includes bash → shown (returns the same definition).
    assert await prepare(_Ctx("plan"), tool_def) is None  # type: ignore[arg-type]
    assert await prepare(_Ctx("build"), tool_def) is tool_def  # type: ignore[arg-type]
