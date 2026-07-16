"""Unit tests for the ungated ``skill`` dispatcher (``decode.tools.skills``) — ADR-0004 §2,7.

Direct tests pin the built-in ``commit`` body, the project override, the resource trailer, the
harness-home split, and the ``ModelRetry`` (listing names) on an unknown skill. Loop-driven
tests ride the real ``build_agent`` + ``AgentTurnHandler`` + gate (scripted ``FunctionModel``,
no network) to prove the §7 invariant: the dispatcher is ungated (no ``PermissionRequested``,
even in plan mode) while the mutation a skill *describes* still rides its own gate.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.config.settings import settings
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Boundary, TurnContext
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode, ToolKind
from decode.skills.loader import load_builtin_skills, load_skills
from decode.skills.payload import format_skill_payload
from decode.tools import KNOWN_TOOL_NAMES, skills
from decode.tools.registry import TOOL_SPECS

# direct-call harness


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


async def _no_user_resolver(question: str) -> str:
    raise AssertionError("the skill dispatcher must not ask the user a question")


def _ctx(cwd: Path) -> RunContext[AgentDeps]:
    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,  # type: ignore[arg-type]
        gate=PermissionGate(),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=_no_user_resolver,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=False)  # type: ignore[arg-type]


def _write_project_skill(cwd: Path, *, name: str, body: str) -> Path:
    """Write a project skill ``<cwd>/<settings.skills_dir>/<name>/SKILL.md`` and return its path."""
    skill_dir = cwd / settings.skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: a {name} skill\n---\n{body}\n", encoding="utf-8"
    )
    return path


# direct: name + signature


def test_skill_tool_name_is_stable():
    assert skills.SKILL_TOOL_NAME == "skill"


def test_skill_takes_ctx_and_name_only_no_args():
    # ADR-0004 §2: the lazy v1 dispatcher is ``name``-only — no structured ``args`` parameter.
    import inspect

    params = list(inspect.signature(skills.skill).parameters)
    assert params == ["ctx", "name"]


# harness-home split: skills are a harness artifact, read from harness_home


async def test_skill_dispatcher_reads_harness_home_not_the_workspace_cwd(tmp_path):
    # In a sandbox mode ``deps.cwd`` is the Workspace, but the project's skills catalog stays anchored at
    # Harness Home — so the dispatcher must resolve a project skill placed under harness_home, and must
    # NOT see one that only exists under the Workspace ``cwd`` (proving it reads harness_home).
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_project_skill(home, name="deploy", body="DEPLOY-FROM-HARNESS-HOME")
    _write_project_skill(workspace, name="ghost", body="SHOULD-NOT-LOAD")
    deps = AgentDeps(
        cwd=workspace,
        harness_home=home,
        emit=lambda _e: None,  # type: ignore[arg-type]
        gate=PermissionGate(),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=_no_user_resolver,
    )
    ctx: RunContext[AgentDeps] = RunContext(
        deps=deps, model=None, usage=None, tool_call_approved=False
    )  # type: ignore[arg-type]

    out = await skills.skill(ctx, "deploy")
    assert "DEPLOY-FROM-HARNESS-HOME" in out  # resolved from Harness Home, not the Workspace

    with pytest.raises(ModelRetry, match="No skill named 'ghost'"):
        await skills.skill(ctx, "ghost")  # a Workspace-only skill is invisible to the dispatcher


# direct: happy path + override + unknown


async def test_skill_returns_the_builtin_commit_body(tmp_path):
    # With no project skills present, ``skill("commit")`` returns the bundled built-in body first
    # (only the standing outputs-default trailer follows it).
    expected = load_builtin_skills()["commit"].body

    result = await skills.skill(_ctx(tmp_path), "commit")

    assert result.startswith(expected)
    assert "git add" in result and "git commit" in result


async def test_skill_unknown_name_raises_model_retry_listing_available_names(tmp_path):
    # An unknown name is a model mistake, not a crash: a ModelRetry whose message lists the skills.
    with pytest.raises(ModelRetry) as excinfo:
        await skills.skill(_ctx(tmp_path), "nope")

    message = str(excinfo.value)
    assert "nope" in message
    for name in load_builtin_skills():
        assert name in message


async def test_skill_respects_a_project_override(tmp_path):
    # A project ``<cwd>/.decode/skills/commit/SKILL.md`` overrides the built-in by name (ADR-0004 §3):
    # the dispatcher returns the PROJECT body.
    _write_project_skill(tmp_path, name="commit", body="Our team's commit ritual.")

    result = await skills.skill(_ctx(tmp_path), "commit")

    assert result.startswith("Our team's commit ritual.")


async def test_skill_returns_a_project_only_skill(tmp_path):
    # A project-only skill (no built-in of that name) is dispatchable too.
    _write_project_skill(tmp_path, name="deploy", body="Ship it to staging first.")

    result = await skills.skill(_ctx(tmp_path), "deploy")

    assert result.startswith("Ship it to staging first.")


# direct: the resource trailer


def _add_resource(cwd: Path, *, name: str, relpath: str = "references/x.md") -> Path:
    """Add a sibling resource file under a project skill's dir (makes it resource-bearing)."""
    target = cwd / settings.skills_dir / name / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("bundled", encoding="utf-8")
    return target


async def test_skill_with_a_bundled_resource_returns_body_plus_trailer(tmp_path):
    # A project skill whose directory ships a sibling resource gets body + the shared payload's
    # resource trailer (the cwd-relative dir the model can ``read`` from) — formatted by the one
    # ``format_skill_payload`` helper the TUI path also uses.
    _write_project_skill(tmp_path, name="deploy", body="Ship it to staging first.")
    _add_resource(tmp_path, name="deploy")

    result = await skills.skill(_ctx(tmp_path), "deploy")

    found = load_skills(tmp_path)["deploy"]
    assert result == format_skill_payload(found, cwd=tmp_path)
    assert result.startswith("Ship it to staging first.")
    assert ".decode/skills/deploy" in result  # the trailer names the cwd-relative dir
    assert result != found.body  # a trailer was appended


async def test_skill_builtin_returns_body_without_a_resource_manifest(tmp_path):
    # A built-in is SKILL.md-only (``resource_dir`` is None) → body + the outputs default only,
    # never a phantom resource manifest.
    result = await skills.skill(_ctx(tmp_path), "commit")

    assert result.startswith(load_builtin_skills()["commit"].body)
    assert "Bundled files" not in result  # no resource manifest
    assert ".decode/outputs/" in result  # the standing outputs default rides every payload


async def test_skill_resourceless_project_skill_returns_body_without_a_resource_manifest(tmp_path):
    # A project skill with only a SKILL.md (no siblings) ships no resources → no resource manifest.
    _write_project_skill(tmp_path, name="deploy", body="Ship it to staging first.")

    result = await skills.skill(_ctx(tmp_path), "deploy")

    assert result.startswith("Ship it to staging first.")
    assert "Bundled files" not in result  # no resource manifest
    assert ".decode/outputs/" in result


# registry + agents wiring


def test_skill_is_registered_as_an_other_kind_spec():
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    assert "skill" in by_name
    assert by_name["skill"].kind is ToolKind.OTHER
    assert by_name["skill"].func is skills.skill


def test_skill_is_a_known_tool_name():
    assert "skill" in KNOWN_TOOL_NAMES


def test_all_primary_builtin_agents_list_skill():
    # ADR-0004 §4: the skill dispatcher is available to every *primary* persona. The explore
    # subagent deliberately omits it — read-only by construction, skill is not needed (ADR-0013 §2).
    from decode.agents.loader import load_builtin_agents

    agents = load_builtin_agents()
    assert set(agents) == {"build", "plan", "explore", "code-reviewer"}
    for name in ("build", "plan", "code-reviewer"):
        assert "skill" in agents[name].tools, f"{name} must list the skill dispatcher (ADR-0004 §4)"
    assert "skill" not in agents["explore"].tools  # subagent excludes skill (ADR-0013 §2)


async def test_agent_omitting_skill_hides_the_dispatcher():
    # The per-tool ``prepare=`` callback (ADR-0003 §6) returns None when the active agent omits the
    # tool. A persona whose ``tools`` lacks ``skill`` hides the dispatcher; one that lists it shows it.
    from pydantic_ai.tools import ToolDefinition

    from decode.agents.loader import load_agent, parse_agent_file
    from decode.tools.registry import _restrict_to_active_agent

    prepare = _restrict_to_active_agent("skill")
    tool_def = ToolDefinition(name="skill", parameters_json_schema={"type": "object"})
    without_skill = parse_agent_file(
        "---\nname: noskill\ndescription: x\ntools: [read]\nmode: default\n---\nBody.\n"
    )

    class _Ctx:
        def __init__(self, agent):
            self.deps = type("D", (), {"active_agent": agent})()

    assert await prepare(_Ctx(without_skill), tool_def) is None  # type: ignore[arg-type]
    assert await prepare(_Ctx(load_agent("build")), tool_def) is tool_def  # type: ignore[arg-type]


# loop-driven harness


@pytest.fixture
def agent(mocker):
    """A real `decode` agent built with a dummy key (never used: tests override the model)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


def _loop_deps(emit, *, gate: PermissionGate, cwd: Path) -> AgentDeps:
    return AgentDeps(
        cwd=cwd,
        emit=emit,
        gate=gate,
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=_no_user_resolver,
    )


def _scripted_model(steps: list[DeltaToolCall]) -> FunctionModel:
    """Stream one scripted tool call per model request, then plain text once the steps run out.

    The ungated ``skill`` executes inline within a single ``agent.iter`` leg, so the model is
    re-requested after its return; a gated ``bash`` instead resolves the leg to
    ``DeferredToolRequests`` for the gate to decide.
    """
    state = {"i": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        i = state["i"]
        if i >= len(steps):
            yield "done"
            return
        state["i"] += 1
        yield {0: steps[i]}

    return FunctionModel(stream_function=stream_function)


async def _drive(handler: AgentTurnHandler, ctx: TurnContext) -> None:
    """Drive the handler generator to completion, draining nothing at each boundary."""
    agen = handler(ctx)
    boundary = await agen.asend(None)
    while True:
        assert isinstance(boundary, Boundary)
        try:
            boundary = await agen.asend([])
        except StopAsyncIteration:
            break
    await agen.aclose()


def _tool_return_strings(handler: AgentTurnHandler) -> list[str]:
    """Every tool-return content string in the handler's accumulated history."""
    return [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _skill_call(name: str) -> DeltaToolCall:
    return DeltaToolCall(name="skill", json_args=json.dumps({"name": name}))


def _commit_bash_call() -> DeltaToolCall:
    # The mutation the ``commit`` skill describes — it rides the gated ``bash`` tool.
    return DeltaToolCall(name="bash", json_args=json.dumps({"command": "git commit -m x"}))


async def test_skill_through_the_loop_returns_the_body_and_is_ungated(agent, tmp_path):
    """A scripted ``skill("commit")`` returns the body as the tool result and emits no prompt."""
    emitted: list[events.Event] = []
    gate = PermissionGate()  # DEFAULT
    deps = _loop_deps(emitted.append, gate=gate, cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)

    steps = [_skill_call("commit")]
    ctx = TurnContext(0, "load the commit skill", emitted.append)
    with agent.override(model=_scripted_model(steps)):
        await _drive(handler, ctx)

    # The built-in commit body came back as the tool result...
    returns = _tool_return_strings(handler)
    assert any("git commit" in r for r in returns)
    # ...with no permission prompt: the dispatcher is ungated and never reaches the gate.
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)]


async def test_skill_is_callable_in_plan_mode(agent, tmp_path):
    """The dispatcher is ungated, so it is callable even while the gate is in plan mode."""
    emitted: list[events.Event] = []
    gate = PermissionGate(mode=PermissionMode.PLAN)
    deps = _loop_deps(emitted.append, gate=gate, cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)

    steps = [_skill_call("commit")]
    ctx = TurnContext(0, "load the commit skill", emitted.append)
    with agent.override(model=_scripted_model(steps)):
        await _drive(handler, ctx)

    returns = _tool_return_strings(handler)
    assert any("git commit" in r for r in returns), "skill must return its body even in plan mode"
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)]


async def test_skill_ungated_but_the_induced_commit_is_gated_in_default_mode(agent, tmp_path):
    """ADR-0004 §7 invariant: the dispatcher is ungated; the action it describes still hits the gate.

    Worked example ``commit``: ``skill("commit")`` returns its body with NO ``PermissionRequested``,
    but the ``git commit`` the skill describes (a gated ``bash`` call) DOES reach the gate — in
    default mode it is *asked* (a ``PermissionRequested`` is emitted) and then denied here.
    """
    emitted: list[events.Event] = []
    gate = PermissionGate()  # DEFAULT
    deps = _loop_deps(emitted.append, gate=gate, cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)

    steps = [_skill_call("commit"), _commit_bash_call()]
    ctx = TurnContext(0, "load the commit skill then commit", emitted.append)
    with agent.override(model=_scripted_model(steps)):
        await _drive(handler, ctx)

    # The dispatcher returned the body (its result is in the history) and asked for nothing.
    returns = _tool_return_strings(handler)
    assert any("git commit" in r for r in returns)
    # Exactly one permission prompt was emitted — for the bash commit, not the skill dispatch.
    prompts = [e for e in emitted if isinstance(e, events.PermissionRequested)]
    assert len(prompts) == 1
    assert prompts[0].name == "bash"


async def test_skill_ungated_but_the_induced_commit_is_denied_in_plan_mode(agent, tmp_path):
    """ADR-0004 §7 invariant in plan mode: skill loads, the ``git commit`` it describes is denied.

    ``skill("commit")`` returns its body with NO ``PermissionRequested`` even in plan mode, while the
    ``git commit`` the skill describes is auto-denied by the plan-mode gate (the denial reason reaches
    the model as the tool result).
    """
    emitted: list[events.Event] = []
    gate = PermissionGate(mode=PermissionMode.PLAN)
    deps = _loop_deps(emitted.append, gate=gate, cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)

    steps = [_skill_call("commit"), _commit_bash_call()]
    ctx = TurnContext(0, "load the commit skill then commit", emitted.append)
    with agent.override(model=_scripted_model(steps)):
        await _drive(handler, ctx)

    # The dispatcher loaded the body; it never produced a permission prompt.
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)]
    # The induced git commit was denied by the plan-mode gate — the model was told why.
    returns = _tool_return_strings(handler)
    assert any("plan mode" in r.lower() for r in returns), "plan mode must deny the induced commit"
