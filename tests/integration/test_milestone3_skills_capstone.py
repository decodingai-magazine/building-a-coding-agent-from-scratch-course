"""The Milestone 3 capstone: the whole Skills flow through the FULL real stack.

This is M3's living proof (task 029) — and, like ``test_milestone1_capstone.py``, it doubles as
documentation. It drives the two tiers of progressive disclosure (ADR-0004 §1) and **both** entry
points into a skill body through the **real** wiring, swapping out only the network boundary:

* the real :func:`decode.agent.factory.build_agent` (so the real ``@agent.instructions`` skills-catalog
  hook, the real flat tool registry, and the real ungated ``skill`` dispatcher are all exercised);
* the real :class:`decode.harness.runner.Runner` + :class:`decode.agent.loop.AgentTurnHandler`
  (so the real turn lifecycle — model-request legs, ungated inline tool execution, the permission
  gate, history carry-over — runs);
* the real :func:`decode.skills.loader.load_skills` behind both entry points: the model's
  ``skill("commit")`` dispatcher AND the user's ``/commit`` TUI command (the real
  :func:`decode.tui.app.parse_skill_command` → :func:`decode.tui.app._handle_skill_command` →
  ``runner.submit`` path).

**No network.** The model is a scripted :class:`~pydantic_ai.models.function.FunctionModel`
(``GEMINI_API_KEY`` is faked only so ``build_agent`` constructs), and every working tree is a fresh
``tmp_path`` so the repo's real ``.decode/`` is never read or written. The test needs no API key and
makes no network call, so it runs in CI under ``make integration-tests`` / ``make ci``.

The five guarantees, one test each (ADR-0004):

1. **Catalog (always injected, cheap):** both built-in skills' ``name`` + ``description`` and the
   ``skill("<name>")`` cue ride a real run's assembled instructions — the menu is on every prompt.
2. **Model dispatcher (body on demand):** a scripted ``skill("commit")`` returns the full built-in
   body as the tool result and emits **no** ``PermissionRequested`` (the dispatcher is ungated).
3. **User TUI slash path (second entry point):** ``/commit`` resolves to the commit **body** (not the
   literal ``/commit``) and that body is what reaches ``runner.submit`` as the turn input.
4. **Project override (both entry points):** a ``<cwd>/.decode/skills/commit.md`` overrides the
   built-in by name, so ``skill("commit")``, ``/commit``, **and** the catalog line all reflect the
   project skill.
5. **Unknown skill:** ``skill("does-not-exist")`` surfaces a :class:`pydantic_ai.ModelRetry` listing
   the available names — the model receives it as a tool retry, never a crash.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.config.settings import settings
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Runner
from decode.permissions.gate import PermissionGate
from decode.skills.loader import load_builtin_skills
from decode.tui.app import InputIntent, _handle_skill_command, parse_skill_command

# --- markers, so the assertions read as a transcript ----------------------------------------

_DONE_TEXT = "done"
# A project commit skill that is unmistakably different from the bundled built-in (task override).
_PROJECT_COMMIT_DESC = "Our team's bespoke commit ritual — squash, sign off, then push."
_PROJECT_COMMIT_BODY = "PROJECT COMMIT RITUAL: squash to one commit, sign off, push to release."


@pytest.fixture(autouse=True)
def _fake_gemini_key(mocker):
    """Let ``build_agent`` construct the Gemini provider offline (the model is overridden)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


# --- scripted, capturing model (mirrors test_skills.py / test_orchestration.py) --------------


async def _deny_permission(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


async def _no_user_question(question: str) -> str:
    raise AssertionError("the skills capstone must never ask the user a question")


def _model(captured: list[list[ModelMessage]], *, steps: list[DeltaToolCall]) -> FunctionModel:
    """A FunctionModel that records each leg's incoming messages, then scripts one tool call per leg.

    Each model request appends the messages it saw to ``captured`` (so a test can read the assembled
    instructions and the user prompt the model received), streams the next scripted tool call, and —
    once the steps run out (including the leg after an ungated tool returns, or a ``ModelRetry``
    re-request) — streams ``_DONE_TEXT`` so the turn reaches its would-stop boundary.
    """
    state = {"i": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        captured.append(list(messages))
        i = state["i"]
        if i >= len(steps):
            yield _DONE_TEXT
            return
        state["i"] += 1
        yield {0: steps[i]}

    return FunctionModel(stream_function=stream_function)


def _skill_call(name: str) -> DeltaToolCall:
    return DeltaToolCall(name="skill", json_args=json.dumps({"name": name}))


def _make_runner(
    agent, *, cwd: Path, events_seen: list[events.Event]
) -> tuple[Runner, AgentTurnHandler]:
    """A real ``Runner`` + ``AgentTurnHandler`` whose every event flows into ``events_seen``.

    Both the tool-emit sink (``deps.emit``) and the runner's turn-lifecycle sink point at the same
    list, so a single collection captures everything the turn produces (mirrors the M1 capstone).
    """
    deps = AgentDeps(
        cwd=cwd,
        emit=events_seen.append,
        gate=PermissionGate(),  # DEFAULT
        resolve_permission=_deny_permission,
        resolve_user_question=_no_user_question,
    )
    handler = AgentTurnHandler(agent, deps=deps)
    runner = Runner(handler, on_event=events_seen.append)
    return runner, handler


async def _run_turn(runner: Runner, text: str) -> None:
    """Submit one prompt and drive the runner to idle (one whole turn)."""
    await runner.submit(text, InputIntent.STEER)
    await runner.wait_idle()


# --- readers over the captured legs / accumulated history ------------------------------------


def _first_instructions(captured: list[list[ModelMessage]]) -> str:
    """The assembled instructions block the model saw on the first request."""
    first = captured[0][0]
    assert isinstance(first, ModelRequest)
    return first.instructions or ""


def _tool_returns(handler: AgentTurnHandler) -> list[str]:
    """Every tool-return content string in the handler's accumulated history."""
    return [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _user_prompts(captured: list[list[ModelMessage]]) -> list[str]:
    """Every ``UserPromptPart`` text the model saw across all captured legs."""
    return [
        str(part.content)
        for leg in captured
        for message in leg
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


def _retry_messages(captured: list[list[ModelMessage]]) -> list[str]:
    """Every ``RetryPromptPart`` message the model saw (a tool ``ModelRetry`` fed back as a retry)."""
    return [
        str(part.content)
        for leg in captured
        for message in leg
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]


def _permission_requests(events_seen: list[events.Event]) -> list[events.PermissionRequested]:
    return [e for e in events_seen if isinstance(e, events.PermissionRequested)]


def _write_project_skill(cwd: Path, *, name: str, description: str, body: str) -> Path:
    """Write a project skill under ``<cwd>/<settings.skills_dir>`` and return its path."""
    skills_dir = cwd / settings.skills_dir
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8"
    )
    return path


# --- 1. catalog (always injected, cheap) ----------------------------------------------------


async def test_skills_catalog_rides_every_real_run_instructions(tmp_path):
    """Both built-in skills (name + description) and the ``skill("…")`` cue ride the instructions.

    A real ``build_agent()`` turn through the real loop (scripted ``FunctionModel``, faked key, no
    network) assembles its instructions per run; the cheap menu half of progressive disclosure
    (ADR-0004 §1) must be in it — every built-in skill's name + one-line description, plus the cue
    naming the on-demand ``skill("<name>")`` call.
    """
    agent = build_agent()
    captured: list[list[ModelMessage]] = []
    events_seen: list[events.Event] = []
    runner, _handler = _make_runner(agent, cwd=tmp_path, events_seen=events_seen)

    with agent.override(model=_model(captured, steps=[])):
        await _run_turn(runner, "what can you do?")

    instructions = _first_instructions(captured)
    builtins = load_builtin_skills()
    assert set(builtins) == {"commit", "review-diff"}, "the two built-ins must ship in the catalog"
    for name, skill in builtins.items():
        assert name in instructions, f"the catalog must advertise the {name!r} skill name"
        assert skill.description in instructions, f"the catalog must carry {name!r}'s description"
    # The cue names the exact on-demand call — the bridge to the dispatcher (the body-on-demand half).
    assert 'skill("' in instructions, "the catalog must tell the model how to load a skill's body"


# --- 2. model dispatcher (body on demand, ungated) ------------------------------------------


async def test_model_dispatcher_returns_the_builtin_body_ungated(tmp_path):
    """A scripted ``skill("commit")`` returns the built-in body and emits no permission prompt.

    Through the real loop the ungated ``skill`` dispatcher executes inline and feeds the full commit
    body back as the tool result; because it never reaches the permission gate, **no**
    ``PermissionRequested`` is emitted (ADR-0004 §7 — loading instructions grants no authority).
    """
    agent = build_agent()
    captured: list[list[ModelMessage]] = []
    events_seen: list[events.Event] = []
    runner, handler = _make_runner(agent, cwd=tmp_path, events_seen=events_seen)

    with agent.override(model=_model(captured, steps=[_skill_call("commit")])):
        await _run_turn(runner, "load the commit skill")

    expected_body = load_builtin_skills()["commit"].body
    returns = _tool_returns(handler)
    assert any(r == expected_body for r in returns), (
        "the dispatcher must return the full commit body"
    )
    assert any("Conventional Commits" in r for r in returns)
    # Ungated: the dispatcher never reached the permission gate, so nothing was asked.
    assert not _permission_requests(events_seen), (
        "the skill dispatcher must not prompt for permission"
    )
    assert not any(isinstance(e, events.AgentError) for e in events_seen), "the turn must not crash"


# --- 3. user TUI slash path (second entry point) --------------------------------------------


async def test_tui_slash_command_submits_the_skill_body_not_the_literal_slash(tmp_path):
    """``/commit`` resolves to the commit body and that body is the turn input ``runner.submit`` sees.

    The user-facing second entry point (ADR-0004 §5) drives the real
    ``parse_skill_command`` → ``_handle_skill_command`` → ``runner.submit`` chain: the parsed
    ``/commit`` resolves through the same ``load_skills`` the dispatcher uses, and the **body** (not
    the literal ``/commit``) becomes the user prompt the model receives.
    """
    agent = build_agent()
    captured: list[list[ModelMessage]] = []
    events_seen: list[events.Event] = []
    emitted_lines: list[str] = []
    runner, _handler = _make_runner(agent, cwd=tmp_path, events_seen=events_seen)

    parsed = parse_skill_command("/commit")
    assert parsed == ("commit", ""), "the TUI must parse /commit into (name, trailing)"
    name, trailing = parsed
    turn_input = _handle_skill_command(name, trailing, cwd=tmp_path, emit=emitted_lines.append)
    assert turn_input is not None, (
        "a known /commit must resolve to a turn input, not a discovery line"
    )

    with agent.override(model=_model(captured, steps=[])):
        await _run_turn(runner, turn_input)

    # The body that the dispatcher would return is exactly what the TUI submitted (same loader).
    expected_body = load_builtin_skills()["commit"].body
    assert turn_input == expected_body, "the TUI must resolve /commit to the built-in commit body"
    prompts = _user_prompts(captured)
    assert any("Conventional Commits" in p for p in prompts), (
        "the body reached the model as the input"
    )
    assert not any(p.strip() == "/commit" for p in prompts), (
        "the literal /commit must not reach the model"
    )


# --- 4. project override (both entry points + the catalog) ----------------------------------


async def test_project_override_wins_for_both_entry_points_and_the_catalog(tmp_path):
    """A ``<cwd>/.decode/skills/commit.md`` overrides the built-in for dispatcher, TUI, and catalog.

    With an intentional same-name project override present (ADR-0004 §3), all three surfaces resolve
    to the **project** commit skill: the model's ``skill("commit")`` returns the project body, the
    user's ``/commit`` submits the project body, and the always-injected catalog line shows the
    project description (the built-in's description is gone).
    """
    _write_project_skill(
        tmp_path, name="commit", description=_PROJECT_COMMIT_DESC, body=_PROJECT_COMMIT_BODY
    )
    builtin_commit_desc = load_builtin_skills()["commit"].description

    # (a) the catalog line reflects the PROJECT description, not the built-in's.
    agent = build_agent()
    captured: list[list[ModelMessage]] = []
    events_seen: list[events.Event] = []
    runner, _handler = _make_runner(agent, cwd=tmp_path, events_seen=events_seen)
    with agent.override(model=_model(captured, steps=[])):
        await _run_turn(runner, "what can you do?")
    instructions = _first_instructions(captured)
    assert _PROJECT_COMMIT_DESC in instructions, "the catalog must show the project description"
    assert builtin_commit_desc not in instructions, "the built-in description must be overridden"
    assert "review-diff" in instructions, "an unoverridden built-in still rides the catalog"

    # (b) the model dispatcher returns the PROJECT body.
    captured_b: list[list[ModelMessage]] = []
    events_b: list[events.Event] = []
    runner_b, handler_b = _make_runner(agent, cwd=tmp_path, events_seen=events_b)
    with agent.override(model=_model(captured_b, steps=[_skill_call("commit")])):
        await _run_turn(runner_b, "load the commit skill")
    assert any(r == _PROJECT_COMMIT_BODY for r in _tool_returns(handler_b)), (
        "skill('commit') must return the project override body"
    )

    # (c) the TUI /commit submits the PROJECT body.
    parsed = parse_skill_command("/commit")
    assert parsed is not None
    name, trailing = parsed
    turn_input = _handle_skill_command(name, trailing, cwd=tmp_path, emit=lambda _line: None)
    assert turn_input == _PROJECT_COMMIT_BODY, "/commit must submit the project override body"


# --- 5. unknown skill (ModelRetry, not a crash) ---------------------------------------------


async def test_unknown_skill_surfaces_a_model_retry_listing_available_names(tmp_path):
    """``skill("does-not-exist")`` reaches the model as a retry listing the names — never a crash.

    An unknown name is a model mistake, not an exception: the dispatcher raises a
    :class:`pydantic_ai.ModelRetry`, which the loop feeds back as a ``RetryPromptPart`` and
    re-requests the model. The turn completes (no ``AgentError``), and the retry message lists the
    bad name plus the available skills so the model can correct itself.
    """
    agent = build_agent()
    captured: list[list[ModelMessage]] = []
    events_seen: list[events.Event] = []
    runner, _handler = _make_runner(agent, cwd=tmp_path, events_seen=events_seen)

    with agent.override(model=_model(captured, steps=[_skill_call("does-not-exist")])):
        await _run_turn(runner, "load a skill that is not there")

    retries = _retry_messages(captured)
    assert retries, "the unknown skill must reach the model as a tool retry (RetryPromptPart)"
    message = "\n".join(retries)
    assert "does-not-exist" in message, "the retry must name the bad skill the model asked for"
    for name in load_builtin_skills():
        assert name in message, f"the retry must list the available skill {name!r}"
    # A model mistake, not a crash: the turn finished cleanly with no agent error.
    assert not any(isinstance(e, events.AgentError) for e in events_seen), (
        "ModelRetry must not crash"
    )
