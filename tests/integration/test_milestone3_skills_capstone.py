"""Milestone 3 capstone: the whole Skills flow through the FULL real stack (ADR-0004).

Proves all three tiers of progressive disclosure and BOTH entry points into a skill body:
real build_agent (the @agent.instructions skills-catalog hook + the ungated ``skill``
dispatcher), real Runner + AgentTurnHandler, real load_skills behind the model's
``skill("commit")`` AND the user's ``/commit`` TUI path (parse_skill_command →
_handle_skill_command → runner.submit). Swapped/faked: a scripted FunctionModel plays the
model (GEMINI_API_KEY faked so build_agent constructs); every working tree is a fresh
tmp_path so the repo's real ``.decode/`` is never touched. Fully offline — no network, no
API key, no skipif.

Seven guarantees, one test each: catalog always injected, ungated dispatcher body, TUI slash
path submits the body (not the literal ``/commit``), project override wins on every surface,
unknown skill → ModelRetry, built-ins are tier-2-only (no resource trailer), and the tier-3
bundled-resource chain (catalog → body+trailer → gated ``read`` of the bundled file).
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
from decode.skills.loader import load_builtin_skills, load_skills
from decode.skills.payload import OUTPUTS_DIR, format_skill_payload
from decode.tui.app import InputIntent, _handle_skill_command, parse_skill_command

# --- markers, so the assertions read as a transcript ----------------------------------------

_DONE_TEXT = "done"
# A project commit skill that is unmistakably different from the bundled built-in (task override).
_PROJECT_COMMIT_DESC = "Our team's bespoke commit ritual — squash, sign off, then push."
_PROJECT_COMMIT_BODY = "PROJECT COMMIT RITUAL: squash to one commit, sign off, push to release."

# The trailer the shared payload helper appends only when a skill ships bundled resources (ADR-0004
# §5). Built-ins are SKILL.md-only, so this marker must be ABSENT from a built-in's payload.
_TRAILER_MARKER = "Bundled files for this skill"


def _assert_payload_delivers(payload: str, body: str) -> None:
    """Assert ``payload`` is ``body`` plus the standing outputs default, and nothing else.

    Every payload ends with the outputs trailer (``.decode/outputs/`` — see ``skills/payload.py``),
    so a body is delivered as a PREFIX rather than byte-for-byte equality. Asserting the prefix
    still catches a truncated or reordered body, which is what these tests are really about.
    """
    assert payload.startswith(body), "the payload must open with the skill body, unmodified"
    assert OUTPUTS_DIR in payload[len(body) :], "the payload must close with the outputs default"


# The tier-3 project skill (task 034) — a resource-bearing skill that lives ONLY in this test
# (a ``tmp_path`` fixture; nothing checked in under ``src/``). Its body references a bundled file by
# relative path, and a sibling ``references/<file>.md`` holds known contents the model reads on demand.
_TIER3_NAME = "pdf-export"
_TIER3_DESC = "Export the working tree to a polished, branded PDF report."
_TIER3_BODY = "Render the report, then verify it against references/checklist.md before sending."
_TIER3_REF_RELPATH = "references/checklist.md"
_TIER3_REF_CONTENTS = "1. Embed the cover page.\n2. Check the fonts.\n3. Verify the page numbers."
_TIER3_REF_MARKER = "Embed the cover page"  # a known line of the bundled file, post line-numbering


@pytest.fixture(autouse=True)
def _fake_gemini_key(mocker):
    """Let ``build_agent`` construct the Gemini provider offline (the model is overridden)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


# --- scripted, capturing model (mirrors test_skills.py / test_orchestration.py) --------------


async def _deny_permission(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


async def _approve_permission(request: PermissionRequest) -> PermissionDecision:
    """The scripted human who approves — the standing verdict for the tier-3 ``read`` (task 034).

    Read-only tools auto-allow under ``default`` mode (ADR-0003 §1), so the gate grants the tier-3
    ``read`` without ever reaching this resolver; it stands as the would-be human verdict so the read
    is unambiguously *approved*, not denied, end to end.
    """
    return PermissionDecision.allow()


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


def _read_call(path: str) -> DeltaToolCall:
    """The scripted ``read(path)`` the model issues after the trailer points it at a bundled file."""
    return DeltaToolCall(name="read", json_args=json.dumps({"path": path}))


def _make_runner(
    agent,
    *,
    cwd: Path,
    events_seen: list[events.Event],
    resolve_permission=_deny_permission,
) -> tuple[Runner, AgentTurnHandler]:
    """A real ``Runner`` + ``AgentTurnHandler`` whose every event flows into ``events_seen``.

    Both the tool-emit sink (``deps.emit``) and the runner's turn-lifecycle sink point at the same
    list, so a single collection captures everything the turn produces (mirrors the M1 capstone).
    ``resolve_permission`` defaults to deny (the ungated-skill tests never prompt anyway); the tier-3
    test passes the approving resolver so the gated ``read`` is approved end to end.
    """
    deps = AgentDeps(
        cwd=cwd,
        emit=events_seen.append,
        gate=PermissionGate(),  # DEFAULT
        resolve_permission=resolve_permission,
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
    """Write a project skill ``<cwd>/<settings.skills_dir>/<name>/SKILL.md`` and return its path."""
    skill_dir = cwd / settings.skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8"
    )
    return path


def _write_tier3_skill(cwd: Path) -> tuple[Path, Path]:
    """Write the resource-bearing tier-3 project skill and its sibling bundled file (task 034).

    Lays out ``<cwd>/.decode/skills/pdf-export/SKILL.md`` (whose body references ``references/…`` by
    relative path) **plus** a sibling ``references/checklist.md`` with known contents. The sibling is
    what makes the directory resource-bearing, so the loader sets ``resource_dir`` and the dispatcher
    appends the trailer. Returns ``(skill_md_path, bundled_file_path)``.
    """
    skill_path = _write_project_skill(
        cwd, name=_TIER3_NAME, description=_TIER3_DESC, body=_TIER3_BODY
    )
    bundled = cwd / settings.skills_dir / _TIER3_NAME / _TIER3_REF_RELPATH
    bundled.parent.mkdir(parents=True, exist_ok=True)
    bundled.write_text(_TIER3_REF_CONTENTS, encoding="utf-8")
    return skill_path, bundled


def _tier3_rel_dir() -> str:
    """The cwd-relative skill directory the trailer surfaces (e.g. ``.decode/skills/pdf-export``)."""
    return f"{settings.skills_dir.as_posix()}/{_TIER3_NAME}"


def _seen_tool_returns(captured: list[list[ModelMessage]]) -> list[str]:
    """Every tool-return content the model SAW across all captured legs (its incoming history).

    Distinct from :func:`_tool_returns` (which reads the handler's accumulated history): this proves
    a tool result was actually fed back *to the model* on a later leg — e.g. that the skill's trailer
    reached the model before it issued the tier-3 ``read``.
    """
    return [
        str(part.content)
        for leg in captured
        for message in leg
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


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
    delivered = [r for r in returns if r.startswith(expected_body)]
    assert delivered, "the dispatcher must return the full commit body"
    _assert_payload_delivers(delivered[0], expected_body)
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

    # The body that the dispatcher would return is what the TUI submitted (same loader, same payload).
    expected_body = load_builtin_skills()["commit"].body
    _assert_payload_delivers(turn_input, expected_body)
    prompts = _user_prompts(captured)
    assert any("Conventional Commits" in p for p in prompts), (
        "the body reached the model as the input"
    )
    assert not any(p.strip() == "/commit" for p in prompts), (
        "the literal /commit must not reach the model"
    )


# --- 4. project override (both entry points + the catalog) ----------------------------------


async def test_project_override_wins_for_both_entry_points_and_the_catalog(tmp_path):
    """A ``<cwd>/.decode/skills/commit/SKILL.md`` overrides the built-in for dispatcher, TUI, and catalog.

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
    assert any(r.startswith(_PROJECT_COMMIT_BODY) for r in _tool_returns(handler_b)), (
        "skill('commit') must return the project override body"
    )

    # (c) the TUI /commit submits the PROJECT body.
    parsed = parse_skill_command("/commit")
    assert parsed is not None
    name, trailing = parsed
    turn_input = _handle_skill_command(name, trailing, cwd=tmp_path, emit=lambda _line: None)
    _assert_payload_delivers(turn_input, _PROJECT_COMMIT_BODY)


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


# --- 6. built-ins are tier-2 only (no resource trailer, both entry points) ------------------


async def test_builtin_skills_are_tier_2_only_with_no_resource_trailer(tmp_path):
    """``skill("commit")`` and ``/commit`` return the built-in body with **no** resource trailer.

    A built-in ships only a ``SKILL.md`` (``resource_dir is None`` — ADR-0004 §3), so the shared
    payload helper appends no RESOURCE manifest: progressive disclosure stops at tier 2 for it. Both
    entry points must return the body unmodified with the tier-3 trailer marker absent — that trailer
    is the project-skill-only bridge to bundled resources (task 034). The standing outputs default
    rides every payload, built-in included, so the body is a prefix rather than the whole string.
    """
    commit_body = load_builtin_skills()["commit"].body

    # (a) the model dispatcher returns the bare body — no trailer appended.
    agent = build_agent()
    captured: list[list[ModelMessage]] = []
    events_seen: list[events.Event] = []
    runner, handler = _make_runner(agent, cwd=tmp_path, events_seen=events_seen)
    with agent.override(model=_model(captured, steps=[_skill_call("commit")])):
        await _run_turn(runner, "load the commit skill")

    returns = _tool_returns(handler)
    delivered = [r for r in returns if r.startswith(commit_body)]
    assert delivered, "the dispatcher must return the built-in body unmodified"
    _assert_payload_delivers(delivered[0], commit_body)
    assert not any(_TRAILER_MARKER in r for r in returns), (
        "a built-in is SKILL.md-only — it must carry no resource trailer"
    )

    # (b) the TUI /commit path returns the same body — the outputs default, no RESOURCE trailer.
    turn_input = _handle_skill_command("commit", "", cwd=tmp_path, emit=lambda _line: None)
    _assert_payload_delivers(turn_input, commit_body)
    assert _TRAILER_MARKER not in turn_input


# --- 7. tier-3 bundled-resource project skill (the full three-tier chain) --------------------


async def test_tier3_project_skill_drives_the_full_three_tier_flow(tmp_path):
    """A resource-bearing project skill proves catalog → body+trailer → on-demand bundled read.

    A ``<cwd>/.decode/skills/pdf-export/SKILL.md`` with a sibling ``references/checklist.md`` is driven
    through the **real** ``build_agent()`` + loop with a scripted ``FunctionModel`` (faked key, no
    network). One turn walks all three tiers (ADR-0004 §1,§5): the model calls ``skill("pdf-export")``
    (tier 2) and, prompted by the returned trailer, then calls ``read("<dir>/references/checklist.md")``
    (tier 3). The catalog (tier 1) rides the run's instructions. The ``/pdf-export`` TUI path injects
    the identical payload.
    """
    skill_path, bundled = _write_tier3_skill(tmp_path)
    rel_dir = _tier3_rel_dir()  # .decode/skills/pdf-export — the dir the trailer surfaces
    read_path = f"{rel_dir}/{_TIER3_REF_RELPATH}"  # the bundled file the model reads on demand

    agent = build_agent()
    captured: list[list[ModelMessage]] = []
    events_seen: list[events.Event] = []
    runner, handler = _make_runner(
        agent, cwd=tmp_path, events_seen=events_seen, resolve_permission=_approve_permission
    )

    with agent.override(
        model=_model(captured, steps=[_skill_call(_TIER3_NAME), _read_call(read_path)])
    ):
        await _run_turn(runner, "use the pdf export skill")

    # --- Tier 1: the catalog menu advertises name + description, and carries NO path -----------
    instructions = _first_instructions(captured)
    assert _TIER3_NAME in instructions, "the catalog must advertise the tier-3 skill name"
    assert _TIER3_DESC in instructions, "the catalog must carry the tier-3 skill description"
    assert rel_dir not in instructions, "the catalog is name+description only — no resource path"
    assert str(skill_path) not in instructions, "the catalog must not leak the SKILL.md path"

    # --- Tier 2 + surfacing: skill('pdf-export') returns body + trailer, ungated ---------------
    found = load_skills(tmp_path)[_TIER3_NAME]
    expected_payload = format_skill_payload(found, cwd=tmp_path)
    returns = _tool_returns(handler)
    assert expected_payload in returns, "the dispatcher must return the shared body+trailer payload"
    assert any(_TIER3_BODY in r and f"{rel_dir}/" in r for r in returns), (
        "the dispatcher payload must carry the body PLUS the trailer naming the cwd-relative dir"
    )
    # Ungated: the skill dispatcher never reached the permission gate, so it asked for nothing.
    assert not any(r.name == "skill" for r in _permission_requests(events_seen)), (
        "the skill dispatcher must not prompt for permission"
    )

    # --- Tier 3: prompted by the trailer, the gated read returns the bundled file's contents ---
    assert any(f"{rel_dir}/" in r for r in _seen_tool_returns(captured)), (
        "the model must have SEEN the trailer (skill return fed back) before issuing the read"
    )
    assert any(_TIER3_REF_MARKER in r for r in returns), (
        "the read must return the bundled references/checklist.md contents on demand"
    )
    # The read auto-allows under default mode (read-only; ADR-0003 §1) with the approving resolver
    # standing as the would-be verdict: no permission prompt is surfaced and nothing crashed.
    assert not _permission_requests(events_seen), "the read auto-allows — no prompt is surfaced"
    assert not any(isinstance(e, events.AgentError) for e in events_seen), "the turn must not crash"
    assert bundled.read_text(encoding="utf-8") == _TIER3_REF_CONTENTS, "the bundled file is intact"

    # --- the /<name> TUI path injects the SAME payload (second entry point, one helper) --------
    tui_input = _handle_skill_command(_TIER3_NAME, "", cwd=tmp_path, emit=lambda _line: None)
    assert tui_input == expected_payload, (
        "the /pdf-export TUI path must inject the same body+trailer"
    )
