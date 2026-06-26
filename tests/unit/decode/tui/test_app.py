"""Unit tests for the pure, decidable pieces of ``decode.tui.app``.

The interactive loop (``prompt_async`` inside ``patch_stdout``) reads real stdin and
cannot be driven from a unit test without a pseudo-terminal, so it is exercised by the
end-to-end smoke instead. Everything that has a decidable contract — quit-intent parsing,
the keybinding-intent enum, and the footer hint text — is extracted into pure functions
and tested here.
"""

import asyncio
from pathlib import Path

import pytest
from rich.console import Console

from decode.agent.deps import AgentDeps
from decode.agent.loop import AgentTurnHandler
from decode.agents.loader import load_agent
from decode.config.settings import settings
from decode.entities import events
from decode.entities.permissions import (
    PermissionDecision,
    PermissionOutcome,
    PermissionRequest,
)
from decode.harness.decisions import DecisionChannel
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.tui import app


def _record_console() -> Console:
    """A console that records its output so the event-sink prefix can be asserted (Fix 2)."""
    return Console(width=100, record=True)


def test_event_sink_prefixes_the_assistant_answer_with_decode_once_per_turn():
    # Fix 2: `Decode ` is added once, before the first AssistantTextDelta of a turn.
    console = _record_console()
    sink = app._make_event_sink(console)

    sink(events.TurnStarted(turn_id=0, prompt="hi"))
    sink(events.AssistantTextDelta(text="hello "))
    sink(events.AssistantTextDelta(text="world"))
    sink(events.TurnFinished(turn_id=0))

    out = console.export_text()
    assert "Decode " in out
    assert out.count("Decode ") == 1  # exactly once for the turn


def test_event_sink_resets_the_decode_prefix_each_turn():
    # A new turn re-emits the `Decode ` prefix before its first delta.
    console = _record_console()
    sink = app._make_event_sink(console)

    sink(events.TurnStarted(turn_id=0, prompt="first"))
    sink(events.AssistantTextDelta(text="one"))
    # The next TurnStarted flushes turn 0's buffered line (with its prefix); TurnFinished flushes
    # turn 1's — buffered lines surface when the turn ends, not per chunk.
    sink(events.TurnStarted(turn_id=1, prompt="second"))
    sink(events.AssistantTextDelta(text="two"))
    sink(events.TurnFinished(turn_id=1))

    assert console.export_text().count("Decode ") == 2


def test_event_sink_does_not_prefix_non_assistant_events():
    # Tool/permission/etc. events never get the `Decode ` prefix.
    console = _record_console()
    sink = app._make_event_sink(console)

    sink(events.TurnStarted(turn_id=0, prompt="hi"))
    sink(events.ToolResult(tool_call_id="t1", name="bash", output="ok"))

    assert "Decode " not in console.export_text()


def test_event_sink_streams_deltas_onto_one_line_not_one_per_chunk():
    """Streamed answer deltas flow onto a single line, not one line per chunk.

    Regression guard: deltas were printed with the default ``end="\\n"``, so every streamed token
    landed on its own line. They must now concatenate; a visual break happens only at the terminal
    width or a model-emitted ``\\n``.
    """
    console = _record_console()
    sink = app._make_event_sink(console)

    sink(events.TurnStarted(turn_id=0, prompt="hi"))
    for chunk in ["I", "'m", " ready to draft", " the ADR."]:
        sink(events.AssistantTextDelta(text=chunk))
    sink(events.TurnFinished(turn_id=0))

    out = console.export_text()
    # The chunks concatenate on one line (the `Decode ` prefix leads that same line).
    assert "Decode I'm ready to draft the ADR." in out
    # Not shredded: no individual chunk sits alone on its own line (the old bug).
    lines = out.splitlines()
    assert "'m" not in lines
    assert " ready to draft" not in lines


def test_event_sink_respects_a_model_emitted_newline():
    """A real ``\\n`` inside the streamed text DOES break the line (the model's intended newline)."""
    console = _record_console()
    sink = app._make_event_sink(console)

    sink(events.TurnStarted(turn_id=0, prompt="hi"))
    sink(events.AssistantTextDelta(text="first paragraph.\n\nsecond paragraph."))
    sink(events.TurnFinished(turn_id=0))

    lines = [ln for ln in console.export_text().splitlines() if ln.strip()]
    assert any(ln.endswith("first paragraph.") for ln in lines)
    assert any(ln.strip() == "second paragraph." for ln in lines)


def test_is_quit_command_matches_slash_quit():
    assert app.is_quit_command("/quit") is True


def test_is_quit_command_ignores_surrounding_whitespace():
    assert app.is_quit_command("  /quit  ") is True


def test_is_quit_command_is_false_for_other_input():
    assert app.is_quit_command("hello") is False
    assert app.is_quit_command("/quitter") is False
    assert app.is_quit_command("") is False


def test_is_compact_command_matches_slash_compact():
    assert app.is_compact_command("/compact") is True


def test_is_compact_command_ignores_surrounding_whitespace():
    assert app.is_compact_command("  /compact  ") is True


def test_is_compact_command_is_false_for_other_input():
    assert app.is_compact_command("/compactx") is False
    assert app.is_compact_command("compact") is False
    assert app.is_compact_command("/quit") is False
    assert app.is_compact_command("") is False


def test_footer_hint_mentions_steer_followup_and_abort():
    hint = app.footer_hint("build", "default")

    assert "steer" in hint.lower()
    assert "follow-up" in hint.lower()
    assert "Alt+Enter" in hint
    assert "abort" in hint.lower()
    assert "Esc" in hint


def test_footer_hint_mentions_quit():
    hint = app.footer_hint("build", "default")

    assert "/quit" in hint


def test_footer_hint_mentions_compact():
    hint = app.footer_hint("build", "default")

    assert "/compact" in hint


def test_footer_hint_includes_the_active_agent_and_mode():
    # ADR-0003 §9: the footer shows the live agent + mode so the user always knows the state.
    hint = app.footer_hint("plan", "edit")

    assert "agent:plan" in hint
    assert "mode:edit" in hint


def test_footer_hint_mentions_the_mode_cycle_and_slash_commands():
    hint = app.footer_hint("build", "default")

    assert "Shift+Tab" in hint
    assert "/agent" in hint
    assert "/mode" in hint


def test_input_intent_enum_has_steer_followup_and_abort():
    # The keybindings record/emit intent for now (no harness until task 003).
    assert app.InputIntent.STEER.value == "steer"
    assert app.InputIntent.FOLLOW_UP.value == "follow-up"
    assert app.InputIntent.ABORT.value == "abort"


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "allow", "a", "  yes  ", "ALLOW"])
def test_parse_permission_answer_allows_on_affirmative(answer):
    decision = app.parse_permission_answer(answer)

    assert decision.outcome is PermissionOutcome.ALLOW


@pytest.mark.parametrize("answer", ["n", "no", "deny", "d", "", "anything else", "maybe"])
def test_parse_permission_answer_denies_on_anything_else(answer):
    # ADR-0002 §3 + the safe default: anything that is not a clear "yes" denies.
    decision = app.parse_permission_answer(answer)

    assert decision.outcome is PermissionOutcome.DENY
    assert decision.reason  # a human-facing reason is fed back to the model


@pytest.mark.parametrize("answer", ["a", "A", "always", "ALWAYS", "  always  "])
def test_is_always_answer_true_for_always_variants(answer):
    # `a`/`always` means allow AND persist a rule (task 018); `y`/`yes` is allow-once.
    assert app.is_always_answer(answer) is True


@pytest.mark.parametrize("answer", ["y", "yes", "n", "no", "", "allow"])
def test_is_always_answer_false_for_everything_else(answer):
    assert app.is_always_answer(answer) is False


def test_deny_permission_resolver_is_the_safe_headless_default():
    # Headless / no-TUI callers get a resolver that always denies (safe default).
    request = PermissionRequest(tool_name="noop", args="{}")
    decision = asyncio.run(app.deny_permission_resolver(request))

    assert decision.outcome is PermissionOutcome.DENY


def _quiet_console() -> Console:
    """A throwaway console that swallows the resolver's affordance print."""
    import io

    return Console(file=io.StringIO(), force_terminal=False)


def _make_resolver(channel, *, permissions_file):
    """Build the interactive permission resolver wired to a fresh gate + a tmp rules file."""
    gate = PermissionGate()
    resolver = app._make_permission_resolver(
        channel, _quiet_console(), gate=gate, permissions_file=permissions_file
    )
    return resolver, gate


async def test_interactive_resolver_awaits_the_channel_then_parses_the_answer(tmp_path):
    # The interactive resolver collects the verdict from the single decision channel (no
    # second prompt): the next resolved line is parsed into the allow/deny decision.
    channel = DecisionChannel()
    resolver, _ = _make_resolver(channel, permissions_file=tmp_path / "settings.json")
    request = PermissionRequest(tool_name="noop", args="{}")

    task = asyncio.ensure_future(resolver(request))
    await asyncio.sleep(0)  # let the resolver register the pending decision
    assert channel.pending is True

    channel.resolve("y")
    decision = await task
    assert decision.outcome is PermissionOutcome.ALLOW


async def test_interactive_resolver_denies_when_the_decision_is_cancelled(tmp_path):
    # Turn aborted / REPL shutting down cancels the pending request; the resolver denies
    # (the safe default) instead of hanging.
    channel = DecisionChannel()
    resolver, _ = _make_resolver(channel, permissions_file=tmp_path / "settings.json")
    request = PermissionRequest(tool_name="noop", args="{}")

    task = asyncio.ensure_future(resolver(request))
    await asyncio.sleep(0)
    channel.cancel()

    decision = await task
    assert decision.outcome is PermissionOutcome.DENY
    assert decision.reason


async def test_always_answer_persists_an_allow_rule_and_reloads_the_gate(tmp_path):
    # `a`/`always` allows AND persists a matching allow rule, then reloads the gate so the next
    # identical call auto-allows (ADR-0003 §4, task 018).
    import json

    perms = tmp_path / "settings.json"
    channel = DecisionChannel()
    resolver, gate = _make_resolver(channel, permissions_file=perms)
    request = PermissionRequest(
        tool_name="bash", args='{"command": "npm run test:unit"}', subject="npm run test:unit"
    )

    task = asyncio.ensure_future(resolver(request))
    await asyncio.sleep(0)
    channel.resolve("a")
    decision = await task

    assert decision.outcome is PermissionOutcome.ALLOW
    # The rule was persisted to the user settings file.
    data = json.loads(perms.read_text(encoding="utf-8"))
    assert "bash(npm run test:unit)" in data["permissions"]["allow"]
    # The gate reloaded the rule: the next identical call auto-allows (no ASK).
    assert gate.check(request).outcome is PermissionOutcome.ALLOW


async def test_plain_yes_does_not_persist_a_rule(tmp_path):
    # `y`/`yes` is allow-once: nothing is written and the gate stays mode-only.
    perms = tmp_path / "settings.json"
    channel = DecisionChannel()
    resolver, gate = _make_resolver(channel, permissions_file=perms)
    request = PermissionRequest(
        tool_name="bash", args='{"command": "npm run test:unit"}', subject="npm run test:unit"
    )

    task = asyncio.ensure_future(resolver(request))
    await asyncio.sleep(0)
    channel.resolve("y")
    decision = await task

    assert decision.outcome is PermissionOutcome.ALLOW
    assert not perms.exists()  # nothing persisted
    assert gate.check(request).outcome is PermissionOutcome.ASK  # still mode-only


async def test_always_answer_write_failure_falls_back_to_allow_once(tmp_path, mocker):
    # A persist write failure is non-fatal: the resolver still allows once and does not raise.
    perms = tmp_path / "settings.json"
    channel = DecisionChannel()
    resolver, gate = _make_resolver(channel, permissions_file=perms)
    request = PermissionRequest(tool_name="bash", args="{}", subject="rm -rf x")
    mocker.patch.object(app.rules, "persist_allow_rule", side_effect=OSError("read-only"))

    task = asyncio.ensure_future(resolver(request))
    await asyncio.sleep(0)
    channel.resolve("always")
    decision = await task

    assert decision.outcome is PermissionOutcome.ALLOW  # still allowed once
    assert gate.check(request).outcome is PermissionOutcome.ASK  # rule NOT loaded


async def test_ask_user_resolver_returns_the_typed_line_verbatim():
    # The ask_user resolver awaits the SAME single decision channel and returns the raw line as
    # the free-text answer (no y/N parsing, unlike the permission resolver).
    channel = DecisionChannel()
    resolver = app._make_user_question_resolver(channel, _quiet_console())

    task = asyncio.ensure_future(resolver("which file?"))
    await asyncio.sleep(0)  # let the resolver register the pending decision
    assert channel.pending is True

    channel.resolve("src/main.py")
    answer = await task
    assert answer == "src/main.py"


async def test_ask_user_resolver_propagates_cancellation():
    # A cancelled request (abort / shutdown) must propagate CancelledError out of the resolver
    # so decode.tools.askuser.ask_user maps it to a clean ModelRetry (never a hang).
    channel = DecisionChannel()
    resolver = app._make_user_question_resolver(channel, _quiet_console())

    task = asyncio.ensure_future(resolver("still there?"))
    await asyncio.sleep(0)
    channel.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# --- control-surface parsers (ADR-0003 §9, task 022) — pure, mirror is_quit_command -----------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/agent build", "build"),
        ("  /agent   plan  ", "plan"),
        ("/agent code-reviewer", "code-reviewer"),
        ("/agent", ""),  # the command with no name (a usage error for the handler)
        ("/agentx", None),  # not the command
        ("hello", None),
        ("/mode plan", None),  # a different command
    ],
)
def test_parse_agent_command(line, expected):
    assert app.parse_agent_command(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/mode plan", "plan"),
        ("  /mode   bypass  ", "bypass"),
        ("/mode", ""),  # the command with no mode (a usage error for the handler)
        ("/modex", None),
        ("hello", None),
        ("/agent build", None),  # a different command
    ],
)
def test_parse_mode_command(line, expected):
    assert app.parse_mode_command(line) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("default", PermissionMode.DEFAULT),
        ("plan", PermissionMode.PLAN),
        ("edit", PermissionMode.EDIT),
        ("bypass", PermissionMode.BYPASS),
        ("  PLAN  ", PermissionMode.PLAN),  # whitespace + case insensitive
        ("nope", None),
        ("", None),
    ],
)
def test_parse_mode_name(name, expected):
    assert app.parse_mode_name(name) is expected


def test_next_mode_cycles_default_edit_plan_bypass_default():
    # ADR-0003 §9: Shift+Tab cycles default -> edit -> plan -> bypass -> default.
    assert app.next_mode(PermissionMode.DEFAULT) is PermissionMode.EDIT
    assert app.next_mode(PermissionMode.EDIT) is PermissionMode.PLAN
    assert app.next_mode(PermissionMode.PLAN) is PermissionMode.BYPASS
    assert app.next_mode(PermissionMode.BYPASS) is PermissionMode.DEFAULT


def test_mode_switch_confirmation_names_the_mode():
    line = app.mode_switch_confirmation("edit")

    assert "edit" in line
    assert "Decode" in line  # rendered through the same Decode-prefixed prose path


def test_agent_switch_confirmation_names_the_agent_and_mode():
    line = app.agent_switch_confirmation("plan", "plan")

    assert "plan" in line
    assert "Decode" in line


# --- the live bottom toolbar (reads agent + mode each render) ---------------------------------


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny(reason="test default deny")


async def _no_user_resolver(question: str) -> str:
    raise RuntimeError("no interactive user in this test")


def _deps(gate: PermissionGate) -> AgentDeps:
    return AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=gate,
        resolve_permission=_deny_resolver,
        resolve_user_question=_no_user_resolver,
    )


def _handler(deps: AgentDeps, mocker) -> AgentTurnHandler:
    """A real turn handler (no network) so the footer reads its actual ``last_input_tokens``.

    ``__init__`` only stores the agent (the footer never invokes it), so a ``Mock`` agent is
    enough; the compaction seam stays ``None`` (the cascade is off).
    """
    return AgentTurnHandler(mocker.Mock(), deps=deps)


def test_bottom_toolbar_reads_the_live_agent_and_mode(mocker):
    # The footer must reflect a mode change after Shift+Tab / /mode, so the toolbar reads the
    # gate + deps live each render (not a snapshot taken when the session was built).
    gate = PermissionGate()
    deps = _deps(gate)
    deps.active_agent = load_agent("build")
    handler = _handler(deps, mocker)

    before = app._bottom_toolbar(deps, gate, handler).value
    assert "agent:build" in before
    assert "mode:default" in before

    gate.set_mode(PermissionMode.EDIT)
    after = app._bottom_toolbar(deps, gate, handler).value
    assert "mode:edit" in after  # the live mode change is reflected on the next render


def test_bottom_toolbar_shows_an_empty_green_gauge_before_the_first_turn(mocker):
    # ADR-0006 §9 / task 047: before any leg runs the real ``last_input_tokens`` property is 0, so
    # the footer fill gauge renders an empty circle at 0% in green (well below the warn line).
    gate = PermissionGate()
    deps = _deps(gate)
    deps.active_agent = load_agent("build")
    handler = _handler(deps, mocker)
    assert handler.last_input_tokens == 0  # the public property's default before any turn

    value = app._bottom_toolbar(deps, gate, handler).value

    assert "○ 0%" in value
    assert 'fg="green"' in value


def test_bottom_toolbar_gauge_reads_the_public_property_and_colors_by_fill(mocker):
    # The footer reads ``handler.last_input_tokens`` (the PUBLIC property) over the single source of
    # truth window setting; a near-full window crosses the danger line and turns the gauge red. A
    # Mock returning a plain int proves we read ``.last_input_tokens`` (a private-attr read would
    # hand back a Mock and blow up on the division).
    gate = PermissionGate()
    deps = _deps(gate)
    deps.active_agent = load_agent("build")
    window = settings.compaction_context_window_tokens
    handler = mocker.Mock()
    handler.last_input_tokens = int(window * 0.9)  # 90% full -> full glyph, red (>= 0.80)

    value = app._bottom_toolbar(deps, gate, handler).value

    assert "● 90%" in value
    assert 'fg="red"' in value


# --- the /mode and /agent handlers (mutate gate/deps, render one confirmation line) -----------


def test_handle_mode_command_sets_the_gate_and_confirms():
    gate = PermissionGate()
    lines: list[str] = []

    app._handle_mode_command("plan", gate=gate, emit=lines.append)

    assert gate.mode is PermissionMode.PLAN
    assert any("plan" in line for line in lines)


def test_handle_mode_command_unknown_mode_is_a_friendly_inline_line():
    gate = PermissionGate()
    lines: list[str] = []

    app._handle_mode_command("nope", gate=gate, emit=lines.append)

    assert gate.mode is PermissionMode.DEFAULT  # unchanged
    assert any("nope" in line for line in lines)  # names the bad mode, no crash


def test_handle_mode_command_missing_name_shows_usage():
    gate = PermissionGate()
    lines: list[str] = []

    app._handle_mode_command("", gate=gate, emit=lines.append)

    assert gate.mode is PermissionMode.DEFAULT
    assert any("/mode" in line for line in lines)


def test_handle_agent_command_selects_the_agent_and_confirms():
    gate = PermissionGate()
    deps = _deps(gate)
    lines: list[str] = []

    app._handle_agent_command("plan", deps=deps, gate=gate, emit=lines.append)

    assert deps.active_agent.name == "plan"
    assert gate.mode is PermissionMode.PLAN  # selecting plan resets the mode
    assert any("plan" in line for line in lines)


def test_handle_agent_command_unknown_agent_is_a_friendly_inline_line():
    gate = PermissionGate()
    deps = _deps(gate)
    deps.active_agent = load_agent("build")
    lines: list[str] = []

    app._handle_agent_command("nope", deps=deps, gate=gate, emit=lines.append)

    assert deps.active_agent.name == "build"  # unchanged — the session stays alive
    assert gate.mode is PermissionMode.DEFAULT
    assert any("nope" in line for line in lines)


def test_handle_agent_command_missing_name_shows_usage():
    gate = PermissionGate()
    deps = _deps(gate)
    lines: list[str] = []

    app._handle_agent_command("", deps=deps, gate=gate, emit=lines.append)

    assert any("/agent" in line for line in lines)


# --- the user-facing /<skill-name> command (ADR-0004 §5, task 028) ----------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/commit", ("commit", "")),  # bare name -> empty trailing
        ("/commit fix the bug", ("commit", "fix the bug")),  # name + trailing
        ("  /commit   ship it  ", ("commit", "ship it")),  # name + trailing, both stripped
        ("hello", None),  # not a slash line -> fall through to runner.submit
        ("/", None),  # a bare slash is no name at all
        # NOTE: reserved commands (``/quit`` / ``/agent`` / ``/mode``) now *parse* as a name here
        # (e.g. ``/mode plan`` -> ``("mode", "plan")``). The parser no longer special-cases them —
        # the ``run_app`` loop matches and ``continue``s on those before the skill branch runs, so a
        # reserved command never reaches this function (see the loop-precedence test below).
    ],
)
def test_parse_skill_command(line, expected):
    assert app.parse_skill_command(line) == expected


def _write_skill(skills_dir: Path, name: str, body: str = "do the thing") -> None:
    """Drop a minimal valid project skill ``<skills_dir>/<name>/SKILL.md`` (frontmatter + body)."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: a test skill\n---\n{body}\n", encoding="utf-8"
    )


def test_handle_skill_command_returns_the_known_skill_body(tmp_path):
    # A known skill resolves to its full body (the built-in `commit` ships with the package).
    from decode.skills.loader import load_skills

    lines: list[str] = []
    result = app._handle_skill_command("commit", "", cwd=tmp_path, emit=lines.append)

    assert result == load_skills(tmp_path)["commit"].body
    assert "Conventional Commits" in result  # the body, not the literal `/commit`
    assert not lines  # a match emits nothing — it returns the turn input


def test_handle_skill_command_appends_trailing_text(tmp_path):
    # Trailing text after the name is appended to the body separated by a blank line.
    from decode.skills.loader import load_skills

    body = load_skills(tmp_path)["commit"].body
    result = app._handle_skill_command("commit", "ship it", cwd=tmp_path, emit=lambda _l: None)

    assert result == f"{body}\n\nship it"


def test_handle_skill_command_unknown_emits_available_skills_and_returns_none(tmp_path):
    # An unknown name returns no turn input and emits a friendly line listing the sorted skills.
    lines: list[str] = []
    result = app._handle_skill_command("nope", "", cwd=tmp_path, emit=lines.append)

    assert result is None  # no turn submitted
    assert len(lines) == 1
    message = lines[0]
    assert "nope" in message  # names the bad command
    assert "commit" in message and "review-diff" in message  # discovery: lists available skills
    assert message.index("commit") < message.index("review-diff")  # sorted


def test_reserved_command_is_not_shadowed_by_a_same_named_skill(tmp_path):
    # ADR-0004 §3,5: a project skill named `mode` is reachable via the dispatcher (load_skills), but
    # `/mode plan` never reaches the skill branch — the loop matches `parse_mode_command` first and
    # `continue`s, so the `/mode` handler wins. Precedence is the loop's job, not the parser's (the
    # full loop-precedence path is also covered e2e by ``test_run_app_mode_slash_*``).
    from decode.skills.loader import load_skills

    _write_skill(tmp_path / ".decode" / "skills", "mode")

    assert "mode" in load_skills(tmp_path)  # still reachable via the skill dispatcher
    assert app.parse_mode_command("/mode plan") == "plan"  # the loop's /mode branch matches first


# --- the manual /compact command (ADR-0006 §7, task 045) --------------------------------------


async def test_handle_compact_command_idle_true_compacts_and_emits_no_extra_line(mocker):
    # Idle + something to compact: run handler.compact() (which already emitted ContextCompacted
    # on True) and add NO extra line — the rendered compaction event is the only feedback.
    handler = mocker.Mock()
    handler.compact = mocker.AsyncMock(return_value=True)
    runner = mocker.Mock()
    runner.phase = app.Phase.IDLE
    lines: list[str] = []

    await app._handle_compact_command(handler, runner, emit=lines.append)

    handler.compact.assert_awaited_once_with()
    assert lines == []  # True → the handler's ContextCompacted event is the feedback


async def test_handle_compact_command_idle_false_renders_nothing_to_compact(mocker):
    # Idle but nothing to compact: handler.compact() returns False → the friendly line.
    handler = mocker.Mock()
    handler.compact = mocker.AsyncMock(return_value=False)
    runner = mocker.Mock()
    runner.phase = app.Phase.IDLE
    lines: list[str] = []

    await app._handle_compact_command(handler, runner, emit=lines.append)

    handler.compact.assert_awaited_once_with()
    assert lines == ["Decode - nothing to compact yet."]


@pytest.mark.parametrize("phase", [app.Phase.DISPATCHING, app.Phase.RUNNING])
async def test_handle_compact_command_busy_renders_busy_and_never_compacts(mocker, phase):
    # Busy (DISPATCHING or RUNNING): never compact mid-turn — emit the busy line, leave the turn be.
    handler = mocker.Mock()
    handler.compact = mocker.AsyncMock()
    runner = mocker.Mock()
    runner.phase = phase
    lines: list[str] = []

    await app._handle_compact_command(handler, runner, emit=lines.append)

    handler.compact.assert_not_awaited()  # no mid-turn compaction
    assert lines == ["Decode - busy; try /compact again once the turn finishes."]


def test_compact_reserved_command_is_not_shadowed_by_a_same_named_skill(tmp_path):
    # ADR-0006 §7: a project skill named `compact` is reachable via the dispatcher (load_skills),
    # but `/compact` never reaches the skill branch — the loop matches `is_compact_command` first
    # and `continue`s, so the reserved command wins (precedence is the loop's job, like `/quit`).
    from decode.skills.loader import load_skills

    _write_skill(tmp_path / ".decode" / "skills", "compact")

    assert "compact" in load_skills(tmp_path)  # still reachable via the skill dispatcher
    assert app.is_compact_command("/compact") is True  # the loop's /compact branch matches first


# --- the /<skill-name> resource trailer (ADR-0004 §1,§5; task 033) ----------------------------


def _add_resource_to_skill(skills_dir: Path, name: str, relpath: str = "references/x.md") -> None:
    """Drop a sibling resource under a project skill's dir, making it resource-bearing."""
    target = skills_dir / name / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("bundled", encoding="utf-8")


def test_handle_skill_command_resource_bearing_skill_injects_body_plus_trailer(tmp_path):
    # A resource-bearing project skill injects body + the shared payload's resource trailer (the
    # cwd-relative dir the model can ``read`` from) — the same helper the dispatcher uses.
    from decode.skills.loader import load_skills
    from decode.skills.payload import format_skill_payload

    skills_dir = tmp_path / ".decode" / "skills"
    _write_skill(skills_dir, "deploy", body="Ship it to staging first.")
    _add_resource_to_skill(skills_dir, "deploy")

    result = app._handle_skill_command("deploy", "", cwd=tmp_path, emit=lambda _l: None)

    found = load_skills(tmp_path)["deploy"]
    assert result == format_skill_payload(found, cwd=tmp_path)
    assert result.startswith("Ship it to staging first.")
    assert ".decode/skills/deploy" in result  # the trailer names the cwd-relative dir


def test_handle_skill_command_resource_bearing_skill_appends_trailing_after_trailer(tmp_path):
    # Trailing user text rides after a blank line, AFTER the body+trailer payload.
    from decode.skills.loader import load_skills
    from decode.skills.payload import format_skill_payload

    skills_dir = tmp_path / ".decode" / "skills"
    _write_skill(skills_dir, "deploy", body="Ship it to staging first.")
    _add_resource_to_skill(skills_dir, "deploy")

    result = app._handle_skill_command("deploy", "to prod", cwd=tmp_path, emit=lambda _l: None)

    payload = format_skill_payload(load_skills(tmp_path)["deploy"], cwd=tmp_path)
    assert result == f"{payload}\n\nto prod"


def test_handle_skill_command_builtin_injects_body_without_a_trailer(tmp_path):
    # A built-in (`commit`) is SKILL.md-only → body only, no trailer.
    from decode.skills.loader import load_skills

    result = app._handle_skill_command("commit", "", cwd=tmp_path, emit=lambda _l: None)

    assert result == load_skills(tmp_path)["commit"].body  # body verbatim, no trailer


async def test_dispatcher_and_tui_produce_identical_payloads_for_the_same_skill(tmp_path):
    # ADR-0004 §5: one helper, two entry points, no divergence. The model's ``skill(name)``
    # dispatcher and the user's ``/<name>`` TUI command return the IDENTICAL payload for a
    # resource-bearing project skill (and for a resourceless built-in).
    from pydantic_ai import RunContext

    from decode.permissions.gate import PermissionGate
    from decode.tools import skills as skills_tool

    async def _deny(_request):  # pragma: no cover - never invoked (skill is ungated)
        from decode.entities.permissions import PermissionDecision as _PD

        return _PD.deny()

    async def _no_ask(_question):  # pragma: no cover - never invoked
        raise AssertionError("the skill payload path must not ask the user a question")

    skills_dir = tmp_path / ".decode" / "skills"
    _write_skill(skills_dir, "deploy", body="Ship it to staging first.")
    _add_resource_to_skill(skills_dir, "deploy")

    deps = AgentDeps(
        cwd=tmp_path,
        emit=lambda _e: None,  # type: ignore[arg-type]
        gate=PermissionGate(),
        resolve_permission=_deny,
        resolve_user_question=_no_ask,
    )
    ctx = RunContext(deps=deps, model=None, usage=None, tool_call_approved=False)  # type: ignore[arg-type]

    for name in ("deploy", "commit"):
        dispatcher_payload = await skills_tool.skill(ctx, name)
        tui_payload = app._handle_skill_command(name, "", cwd=tmp_path, emit=lambda _l: None)
        assert dispatcher_payload == tui_payload
