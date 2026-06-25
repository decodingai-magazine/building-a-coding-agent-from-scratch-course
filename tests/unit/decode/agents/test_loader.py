"""Unit tests for the Agents Catalog loader (``decode.agents.loader``; ADR-0003 §5).

The loader reads the four bundled ``builtin/*.md`` files — YAML frontmatter + a system-prompt
body — as **packaged data** (``importlib.resources``, so they ship in the wheel), validates each
into an :class:`~decode.entities.agent_def.AgentDef`, and returns them keyed by name.
:func:`load_agent` returns one persona by name or raises a clear "no such agent" error listing the
available names. These tests pin: the four built-ins load with the right tools/mode/rules from
ADR-0003 §5, the packaged-data path (not a hard-coded repo path), and the frontmatter/body parsing
+ error messages.
"""

import importlib.resources

import pytest

from decode.agents import loader
from decode.entities.agent_def import AgentDef
from decode.permissions.rules import Rule
from decode.permissions.types import PermissionMode

_BUILTIN_NAMES = {"build", "plan", "explore", "code-reviewer"}
_READ_ONLY_TOOLS = {"read", "glob", "grep", "web_fetch", "todo_write"}


# --- the four built-ins ---------------------------------------------------------------------


def test_load_builtin_agents_returns_the_four_personas():
    agents = loader.load_builtin_agents()

    assert set(agents) == _BUILTIN_NAMES
    assert all(isinstance(a, AgentDef) for a in agents.values())
    assert all(name == agent.name for name, agent in agents.items())


def test_build_agent_has_the_full_tool_set_and_default_mode():
    build = loader.load_agent("build")

    assert build.mode is PermissionMode.DEFAULT
    expected = {
        "read", "glob", "grep", "write", "edit", "bash", "todo_write", "web_fetch",
        "ask_user", "enter_plan_mode", "exit_plan_mode", "sleep", "skill",
    }  # fmt: skip
    assert set(build.tools) == expected
    assert build.prompt.strip()


def test_plan_agent_is_plan_mode_and_read_only():
    plan = loader.load_agent("plan")

    assert plan.mode is PermissionMode.PLAN
    assert set(plan.tools) == _READ_ONLY_TOOLS | {
        "enter_plan_mode",
        "exit_plan_mode",
        "ask_user",
        "skill",
    }
    for mutating in ("write", "edit", "bash"):
        assert mutating not in plan.tools


def test_explore_agent_is_read_only_default_mode():
    explore = loader.load_agent("explore")

    assert explore.mode is PermissionMode.DEFAULT
    assert set(explore.tools) == _READ_ONLY_TOOLS | {"ask_user", "skill"}
    for mutating in ("write", "edit", "bash"):
        assert mutating not in explore.tools


def test_code_reviewer_carries_the_git_allow_rule():
    reviewer = loader.load_agent("code-reviewer")

    assert reviewer.mode is PermissionMode.DEFAULT
    assert set(reviewer.tools) == _READ_ONLY_TOOLS | {"bash", "ask_user", "skill"}
    assert "bash(git *)" in reviewer.allow
    assert Rule(tool_name="bash", pattern="git *") in reviewer.allow_rules


# --- packaged-data loading ------------------------------------------------------------------


def test_builtin_files_are_packaged_data_not_a_repo_path():
    # Load through the installed package's resources, not a hard-coded filesystem path.
    files = importlib.resources.files("decode.agents.builtin")
    names = {entry.name for entry in files.iterdir() if entry.name.endswith(".md")}

    assert names == {"build.md", "plan.md", "explore.md", "code-reviewer.md"}
    # Each is readable as package data.
    for md in names:
        assert (files / md).read_text(encoding="utf-8").strip()


# --- load_agent errors ----------------------------------------------------------------------


def test_load_agent_unknown_name_lists_the_available_agents():
    with pytest.raises(ValueError) as excinfo:
        loader.load_agent("nope")

    message = str(excinfo.value)
    assert "nope" in message
    for name in _BUILTIN_NAMES:
        assert name in message


def test_load_builtin_agents_is_independent_per_call():
    # No shared mutable state leaking between calls.
    first = loader.load_builtin_agents()
    second = loader.load_builtin_agents()

    assert first == second
    assert first is not second


# --- frontmatter / body parsing -------------------------------------------------------------


def test_parse_agent_file_splits_frontmatter_and_body():
    text = (
        "---\n"
        "name: demo\n"
        "description: a demo agent\n"
        "tools: [read, glob]\n"
        "mode: default\n"
        "---\n"
        "You are the demo agent.\n"
    )

    agent = loader.parse_agent_file(text)

    assert agent.name == "demo"
    assert agent.tools == ("read", "glob")
    assert agent.mode is PermissionMode.DEFAULT
    assert agent.prompt.strip() == "You are the demo agent."


def test_parse_agent_file_rejects_a_missing_frontmatter_block():
    with pytest.raises(ValueError, match="frontmatter"):
        loader.parse_agent_file("no frontmatter here, just a body\n")


def test_parse_agent_file_rejects_a_bad_mode():
    text = "---\nname: demo\ndescription: x\ntools: [read]\nmode: turbo\n---\nBody.\n"

    with pytest.raises(ValueError, match="mode"):
        loader.parse_agent_file(text)


def test_parse_agent_file_rejects_an_unknown_tool():
    text = "---\nname: demo\ndescription: x\ntools: [read, nope]\nmode: default\n---\nBody.\n"

    with pytest.raises(ValueError, match="nope"):
        loader.parse_agent_file(text)


def test_parse_agent_file_ignores_unknown_frontmatter_keys():
    # A future ``model`` key (step 3) must not break the loader (forward-compatible).
    text = (
        "---\n"
        "name: demo\n"
        "description: x\n"
        "tools: [read]\n"
        "mode: default\n"
        "model: gemini-2.5-pro\n"
        "---\n"
        "Body.\n"
    )

    agent = loader.parse_agent_file(text)

    assert agent.name == "demo"
    assert not hasattr(agent, "model")
