"""Unit tests for the Agents Catalog loader (``decode.agents.loader``) — ADR-0003 §5.

Pins the four built-ins (tools/mode/rules, the explore subagent flag), the packaged-data path
(``importlib.resources``, not a hard-coded repo path), ``load_agent`` /
``load_primary_agent`` error messages, and the frontmatter/body parsing rules.
"""

import importlib.resources

import pytest

from decode.agents import loader
from decode.entities.agent_def import AgentDef
from decode.permissions.rules import Rule
from decode.permissions.types import PermissionMode
from decode.tools import agent as agent_tool

_BUILTIN_NAMES = {"build", "plan", "explore", "code-reviewer"}
# ``lsp`` (task 052 / ADR-0007) is a read-only Code Intelligence tool, so every persona that has
# ``read``/``grep`` carries it too.
_READ_ONLY_TOOLS = {"read", "glob", "grep", "lsp", "web_fetch", "todo_write"}


# the four built-ins


def test_load_builtin_agents_returns_the_four_personas():
    agents = loader.load_builtin_agents()

    assert set(agents) == _BUILTIN_NAMES
    assert all(isinstance(a, AgentDef) for a in agents.values())
    assert all(name == agent.name for name, agent in agents.items())


def test_build_agent_has_the_full_tool_set_and_default_mode():
    build = loader.load_agent("build")

    assert build.mode is PermissionMode.DEFAULT
    expected = {
        "read", "glob", "grep", "lsp", "write", "edit", "bash", "todo_write", "web_fetch",
        "ask_user", "enter_plan_mode", "exit_plan_mode", "sleep", "skill", "agent",
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
        "agent",  # a primary may spawn read-only Explore subagents (ADR-0013 §4)
    }
    for mutating in ("write", "edit", "bash"):
        assert mutating not in plan.tools


def test_explore_agent_is_a_read_only_default_mode_subagent():
    # ADR-0013 §3: explore is demoted to a subagent with a minimal read-only toolset — exactly
    # ``read`` / ``glob`` / ``grep`` / ``lsp`` (no ``web_fetch`` / ``todo_write`` / ``ask_user`` /
    # ``skill``: a subagent cannot ask, and child bookkeeping/fetching is out of scope).
    explore = loader.load_agent("explore")

    assert explore.mode is PermissionMode.DEFAULT
    assert explore.subagent is True
    assert explore.tools == ("read", "glob", "grep", "lsp")
    for excluded in ("web_fetch", "todo_write", "ask_user", "skill", "write", "edit", "bash"):
        assert excluded not in explore.tools


def test_only_explore_is_a_subagent():
    # The primary/subagent axis (ADR-0013 §3): explore is the one subagent; the other three
    # built-ins stay primaries (``subagent is False``) selectable as the main agent.
    agents = loader.load_builtin_agents()

    assert agents["explore"].subagent is True
    for primary in ("build", "plan", "code-reviewer"):
        assert agents[primary].subagent is False


def test_all_builtin_personas_expose_the_lsp_tool():
    # ADR-0007 / task 052: ``lsp`` is a read-only Code Intelligence tool every persona benefits from;
    # without it in the persona's ``tools`` the per-tool ``prepare=`` callback would hide it.
    agents = loader.load_builtin_agents()

    assert set(agents) == _BUILTIN_NAMES
    for name, agent in agents.items():
        assert "lsp" in agent.tools, f"{name} persona must expose the lsp tool"


def test_code_reviewer_carries_the_git_allow_rule():
    reviewer = loader.load_agent("code-reviewer")

    assert reviewer.mode is PermissionMode.DEFAULT
    assert set(reviewer.tools) == _READ_ONLY_TOOLS | {"bash", "ask_user", "skill", "agent"}
    assert "bash(git *)" in reviewer.allow
    assert Rule(tool_name="bash", pattern="git *") in reviewer.allow_rules


# the explore body — the Subagent Report contract (ADR-0017 §8)


def test_explore_body_states_the_three_part_report_contract():
    # ADR-0017 §8: an Explore child hands back a tight structured summary — the finding, the
    # file:line evidence, the trace it followed. Pinned on stable structural markers (the section
    # labels + the ``file:line`` term), never on full sentences, so a wording tweak cannot shatter
    # this. A report with no file:line evidence is the hallucination tell §7-ii pairs with.
    body = loader.load_agent("explore").prompt.lower()

    for marker in ("finding", "file:line", "trace"):
        assert marker in body, f"the explore report contract must name {marker!r}"
    # The compression contract: N sibling reports share ONE caller budget (§6), a report can be
    # cut from the end, so the finding leads.
    assert "sibling" in body
    assert "truncat" in body


@pytest.mark.parametrize("name", sorted(_BUILTIN_NAMES))
def test_no_builtin_persona_body_carries_the_synthesis_instruction(name):
    # ADR-0017 §9: compiling the N reports into one answer (prose + text diagram) is the Synthesis
    # FOOTER's job — appended just-in-time by the harness to every aggregated ``agent`` result. It
    # lives in exactly ONE place so a future persona author CANNOT forget it, and so it costs nothing
    # on the (many) turns that fan out no children. Both halves of that break if a persona also
    # carries the wording: the PARENTS (build / plan / code-reviewer) would pay for it every turn and
    # drift out of sync with the harness, and the CHILD (explore) would be told to do its parent's
    # job. Pinned on stable markers, not sentences, so a footer re-wording cannot shatter this.
    body = loader.load_agent(name).prompt.lower()

    for leaked in ("synthes", "diagram", "mermaid", "ascii", "box-drawing"):
        assert leaked not in body, (
            f"{leaked!r} belongs to the Synthesis Footer, not the {name} persona"
        )
    assert agent_tool.SYNTHESIS_FOOTER.lower() not in body  # nor the footer itself, verbatim


# the primaries' delegation nudge — WHEN to reach for the agent tool (task 110)


@pytest.mark.parametrize("name", sorted(_BUILTIN_NAMES - {"explore"}))
def test_every_primary_that_grants_the_agent_tool_is_told_when_to_delegate(name):
    # The fan-out is only worth building if the model REACHES for it. The tool description says how
    # to fan out once the model has decided to call the tool; nothing in the personas said WHEN to
    # decide that — and their "read the relevant files and search the codebase" bullet actively pulls
    # the other way, so a bare "explore this repo" fanned out only by luck (a live unsteered probe
    # went 0-tool-call / 1-angle / 3-angle / 5-angle across runs). Pinned on the stable markers — the
    # tool's name, the broad-question trigger, and ADR-0017 §1's minimum width — never on a sentence.
    persona = loader.load_agent(name)
    # Whitespace-collapsed: the model reads the body as flowed prose, so a marker must not depend on
    # where a paragraph happens to wrap (it wraps at 100 columns, mid-phrase, in every persona file).
    body = " ".join(persona.prompt.lower().split())

    assert agent_tool.AGENT_TOOL_NAME in persona.tools, f"{name} must grant the agent tool"
    assert f"`{agent_tool.AGENT_TOOL_NAME}` call" in body, (
        f"{name} must name the agent tool to call"
    )
    assert "at least 3 distinct angles" in body  # ADR-0017 §1's default width for a broad question
    assert "serially" in body  # …and what it is instead of


def test_the_explore_subagent_is_never_told_to_delegate():
    # explore has no ``agent`` tool (recursion is structurally impossible, ADR-0013 §3) — telling a
    # child to delegate would coach it toward a tool it cannot see.
    explore = loader.load_agent("explore")

    assert agent_tool.AGENT_TOOL_NAME not in explore.tools
    assert "distinct angles" not in explore.prompt.lower()


# packaged-data loading


def test_builtin_files_are_packaged_data_not_a_repo_path():
    # Load through the installed package's resources, not a hard-coded filesystem path.
    files = importlib.resources.files("decode.agents.builtin")
    names = {entry.name for entry in files.iterdir() if entry.name.endswith(".md")}

    assert names == {"build.md", "plan.md", "explore.md", "code-reviewer.md"}
    # Each is readable as package data.
    for md in names:
        assert (files / md).read_text(encoding="utf-8").strip()


# load_agent errors


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


# load_primary_agent


def test_load_primary_agent_returns_a_primary():
    build = loader.load_primary_agent("build")

    assert build.name == "build"
    assert build.subagent is False


def test_load_primary_agent_rejects_the_explore_subagent_listing_only_primaries():
    # A subagent cannot be selected as the main agent — the friendly line offers only the primaries
    # (build / code-reviewer / plan), never the subagent-only explore.
    with pytest.raises(ValueError) as excinfo:
        loader.load_primary_agent("explore")

    message = str(excinfo.value)
    assert "explore" in message  # names what the user tried
    assert "subagent" in message  # explains why it was rejected
    assert "available agents: build, code-reviewer, plan" in message


def test_load_primary_agent_unknown_name_lists_only_primaries():
    with pytest.raises(ValueError) as excinfo:
        loader.load_primary_agent("nope")

    message = str(excinfo.value)
    assert "nope" in message
    assert "available agents: build, code-reviewer, plan" in message


# frontmatter / body parsing


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


def test_parse_agent_file_reads_the_subagent_flag():
    text = (
        "---\nname: demo\ndescription: x\ntools: [read]\nmode: default\nsubagent: true\n---\nB.\n"
    )

    agent = loader.parse_agent_file(text)

    assert agent.subagent is True


def test_parse_agent_file_defaults_subagent_to_false_when_absent():
    text = "---\nname: demo\ndescription: x\ntools: [read]\nmode: default\n---\nBody.\n"

    agent = loader.parse_agent_file(text)

    assert agent.subagent is False


def test_parse_agent_file_rejects_a_non_bool_subagent():
    # Present-but-not-a-bool is a catalog authoring error surfaced loudly (mirrors the ``allow`` /
    # ``deny`` list validation) — ``not-a-bool`` parses to a YAML string, not a boolean.
    text = (
        "---\nname: demo\ndescription: x\ntools: [read]\nmode: default\n"
        "subagent: not-a-bool\n---\nBody.\n"
    )

    with pytest.raises(ValueError, match="subagent"):
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
