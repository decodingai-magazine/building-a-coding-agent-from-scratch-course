"""Unit tests for the :class:`decode.entities.agent_def.AgentDef` entity (ADR-0003 §5).

``AgentDef`` is the parsed + validated result of one Agents Catalog Markdown file: a persona's
``name`` / ``description`` / ``tools`` allowlist / default ``mode`` / optional agent-scoped
``allow`` + ``deny`` rules / system-prompt ``body``. The entity owns the *validation* (the loader
just feeds it parsed frontmatter): an unknown tool name, a bad mode, an empty name/prompt, or a
malformed allow/deny rule must each fail loudly with a clear message. These tests pin that contract
without going through the file loader (the loader has its own tests).
"""

import dataclasses

import pytest

from decode.entities.agent_def import AgentDef
from decode.permissions.rules import Rule
from decode.permissions.types import PermissionMode


def test_agent_def_carries_its_fields():
    agent = AgentDef(
        name="build",
        description="a capable build agent",
        tools=("read", "write", "bash"),
        mode=PermissionMode.DEFAULT,
        allow=("bash(git *)",),
        deny=(),
        prompt="You are the build agent.",
    )

    assert agent.name == "build"
    assert agent.description == "a capable build agent"
    assert agent.tools == ("read", "write", "bash")
    assert agent.mode is PermissionMode.DEFAULT
    assert agent.prompt == "You are the build agent."


def test_agent_def_allow_and_deny_default_to_empty():
    agent = AgentDef(
        name="explore",
        description="read the codebase",
        tools=("read",),
        mode=PermissionMode.DEFAULT,
        prompt="Explore.",
    )

    assert agent.allow == ()
    assert agent.deny == ()


def test_agent_def_subagent_defaults_to_false():
    # No ``subagent`` key → a primary persona, selectable as the main agent (ADR-0013 §3). The
    # default keeps every existing persona a primary with no other change.
    agent = AgentDef(
        name="build",
        description="read the codebase",
        tools=("read",),
        mode=PermissionMode.DEFAULT,
        prompt="Build.",
    )

    assert agent.subagent is False


def test_agent_def_carries_the_subagent_flag_when_set():
    # A subagent-only persona (explore) declares ``subagent: true`` — it may only be spawned via
    # the Agent tool, never selected as the main agent (ADR-0013 §3).
    agent = AgentDef(
        name="explore",
        description="read the codebase",
        tools=("read",),
        mode=PermissionMode.DEFAULT,
        prompt="Explore.",
        subagent=True,
    )

    assert agent.subagent is True


def test_agent_def_is_frozen_and_hashable():
    agent = AgentDef(
        name="explore",
        description="x",
        tools=("read",),
        mode=PermissionMode.DEFAULT,
        prompt="Explore.",
    )

    hash(agent)  # frozen + slotted -> hashable
    with pytest.raises(dataclasses.FrozenInstanceError):
        agent.name = "mutated"  # type: ignore[misc]


def test_agent_def_parses_allow_rules_into_rule_objects():
    agent = AgentDef(
        name="code-reviewer",
        description="review a diff",
        tools=("read", "bash"),
        mode=PermissionMode.DEFAULT,
        allow=("bash(git *)",),
        prompt="Review.",
    )

    assert agent.allow_rules == (Rule(tool_name="bash", pattern="git *"),)
    assert agent.deny_rules == ()


# --- validation -----------------------------------------------------------------------------


def test_agent_def_rejects_an_unknown_tool_name():
    with pytest.raises(ValueError, match="nope"):
        AgentDef(
            name="bad",
            description="x",
            tools=("read", "nope"),
            mode=PermissionMode.DEFAULT,
            prompt="x",
        )


def test_agent_def_accepts_orchestration_tool_names():
    # enter_plan_mode/exit_plan_mode/sleep are registered by task 021 but must validate now.
    agent = AgentDef(
        name="build",
        description="x",
        tools=("read", "enter_plan_mode", "exit_plan_mode", "sleep"),
        mode=PermissionMode.DEFAULT,
        prompt="x",
    )

    assert "enter_plan_mode" in agent.tools
    assert "sleep" in agent.tools


def test_agent_def_rejects_an_empty_name():
    with pytest.raises(ValueError, match="name"):
        AgentDef(
            name="   ",
            description="x",
            tools=("read",),
            mode=PermissionMode.DEFAULT,
            prompt="x",
        )


def test_agent_def_rejects_an_empty_prompt():
    with pytest.raises(ValueError, match="prompt"):
        AgentDef(
            name="build",
            description="x",
            tools=("read",),
            mode=PermissionMode.DEFAULT,
            prompt="   ",
        )


def test_agent_def_rejects_a_malformed_allow_rule():
    with pytest.raises(ValueError, match="rule"):
        AgentDef(
            name="build",
            description="x",
            tools=("bash",),
            mode=PermissionMode.DEFAULT,
            allow=("(missing-tool-name)",),
            prompt="x",
        )


def test_agent_def_rejects_a_malformed_deny_rule():
    with pytest.raises(ValueError, match="rule"):
        AgentDef(
            name="build",
            description="x",
            tools=("bash",),
            mode=PermissionMode.DEFAULT,
            deny=("bash(unbalanced",),
            prompt="x",
        )
