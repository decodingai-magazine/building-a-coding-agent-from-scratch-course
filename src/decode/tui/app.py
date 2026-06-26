"""The interactive REPL: a persistent input line + append-style Rich output.

ADR-0002 §6: input is a concurrent ``prompt_async()`` wrapped in ``patch_stdout()`` so any
output written while the user is typing scrolls *above* the prompt instead of corrupting
it (the prompt stays pinned). Output is append-style -- no full-screen renderer -- and the
harness streams :mod:`decode.entities.events` to :func:`decode.tui.render.render_event`.

Input routing (ADR-0002 §4-5), via :meth:`decode.harness.runner.Runner.submit`. There is
**one** input surface (the main ``prompt_async()``); it has two modes:

* **normal** — the default. idle ``Enter`` -> starts a new turn; plain ``Enter`` while busy
  -> **steering** (injected at the next model-request boundary); ``Alt+Enter`` -> **follow-up**
  (drained only at the would-stop boundary); ``Esc`` -> cooperative **abort**.
* **awaiting-decision** — when a turn pauses to ask the human something (the permission gate
  in task 005, ``AskUser`` in task 011), a requester awaits a line on the
  :class:`~decode.harness.decisions.DecisionChannel`. The next submitted line fulfils that
  request (parsed by :func:`parse_permission_answer`) instead of steering / starting a turn.
  This is the **one general mid-turn HITL channel** — opening a second ``prompt_async()`` on
  the live session is illegal (prompt_toolkit guards ``Application._is_running``) and would
  deadlock the REPL, so the decision rides the single input surface.

The harness keeps the turn off the input coroutine (it runs as its own task), so the REPL
stays responsive while a turn streams. ``Ctrl-D`` or typing ``/quit`` exits cleanly.

The interactive loop reads real stdin, so its plumbing is exercised by the
``run_app`` regression test (a piped prompt_toolkit input); the decidable pieces
(:func:`is_quit_command`, :func:`footer_hint`, :class:`InputIntent`,
:func:`parse_permission_answer`, the control-surface parsers
:func:`parse_agent_command` / :func:`parse_mode_command` / :func:`parse_mode_name` /
:func:`parse_skill_command`, and the Shift+Tab :func:`next_mode` cycle) are pure and unit-tested.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from pydantic_ai.messages import ModelMessage
from rich.console import Console
from rich.text import Text

from decode.agent.deps import AgentDeps, PermissionResolver, UserQuestionResolver
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.agents.select import select_agent
from decode.config.settings import settings
from decode.context.session_log import SessionLog, load, load_latest, resolve_session
from decode.entities import events
from decode.entities.permissions import (
    PermissionDecision,
    PermissionOutcome,
    PermissionRequest,
)
from decode.harness.decisions import DecisionChannel
from decode.harness.runner import Runner
from decode.memory.extract import extract_on_exit
from decode.permissions import rules
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.skills.loader import load_skills
from decode.tui import render

logger = logging.getLogger(__name__)

# The flag value the bare ``--resume`` (no argument) carries: resume the latest session.
_RESUME_LATEST = "latest"

# The startup Agent persona when none is given (ADR-0003 §7,9): the full-tool build agent.
_DEFAULT_AGENT = "build"

_QUIT_COMMAND = "/quit"
# The mid-session control slash commands (ADR-0003 §9): switch the active agent / mode. Parsed on
# the single input surface alongside ``/quit`` (never a second ``prompt_async``).
_AGENT_COMMAND = "/agent"
_MODE_COMMAND = "/mode"
# The order Shift+Tab cycles the gate mode through (ADR-0003 §9): default -> edit -> plan ->
# bypass -> back to default. A tuple so :func:`next_mode` is a pure index step.
_MODE_CYCLE: tuple[PermissionMode, ...] = (
    PermissionMode.DEFAULT,
    PermissionMode.EDIT,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
)
_PROMPT = "> "
# The capital-D label printed once before a turn's first streamed answer chunk (Fix 2). The
# trailing space separates it from the answer; it is added in the event sink (the deltas stream,
# so the once-per-turn prefix cannot live in the pure renderer).
_ASSISTANT_PREFIX = "Decode "
# A minimal inline affordance shown when a decision is pending; the full request was already
# rendered once by the loop's PermissionRequested event (single render path — no re-print). ``a``
# is "always" — allow AND persist a rule so the next identical call auto-allows (task 018).
_PERMISSION_AFFORDANCE = "allow this tool call? [y/N/a=always]"
# The matching affordance for an ask_user question: the full question was already rendered once
# by the tool's AskUserRequested event, so this is only the "type your answer" cue (any line is
# the answer — no parsing, unlike the permission y/N).
_ASK_USER_AFFORDANCE = "type your answer:"
# Answers that mean "always": allow AND persist a matching allow rule (task 018 / ADR-0003 §4).
_ALWAYS_ANSWERS = frozenset({"a", "always"})
# Answers that count as "yes" (allow); everything else denies (the safe default — ADR-0002 §3).
# ``always`` answers also allow, so they are a subset of the affirmative set.
_AFFIRMATIVE_ANSWERS = frozenset({"y", "yes", "allow"}) | _ALWAYS_ANSWERS


class InputIntent(enum.Enum):
    """What the user signalled with the line they just submitted (ADR-0002 §4-5).

    ``STEER`` is plain ``Enter`` (start a turn when idle, steer when busy); ``FOLLOW_UP`` is
    ``Alt+Enter``; ``ABORT`` is ``Esc``. The runner maps ``STEER``/``FOLLOW_UP`` onto the two
    queues and ``ABORT`` onto the cooperative-abort flag.
    """

    STEER = "steer"
    FOLLOW_UP = "follow-up"
    ABORT = "abort"


def is_quit_command(line: str) -> bool:
    """True when ``line`` is the ``/quit`` command (ignoring surrounding whitespace)."""
    return line.strip() == _QUIT_COMMAND


def footer_hint(agent: str, mode: str) -> str:
    """The bottom-toolbar hint: the live agent + mode, then the interaction keys (plain text).

    Kept pure and string-returning so it is unit-testable; :func:`_bottom_toolbar` wraps it for
    prompt_toolkit and supplies the **live** ``agent`` / ``mode`` each render (ADR-0003 §9), so the
    footer updates after a ``/agent`` / ``/mode`` switch or a Shift+Tab cycle. Lists steer (plain
    Enter), follow-up (Alt+Enter), abort (Esc), the Shift+Tab mode cycle, and the slash commands.
    """
    return (
        f"agent:{agent} mode:{mode} | Enter steer | Alt+Enter follow-up | "
        "Esc abort | Shift+Tab mode | /agent /mode /quit"
    )


def parse_agent_command(line: str) -> str | None:
    """Return the name argument of a ``/agent <name>`` line, or ``None`` if not that command.

    Pure (mirrors :func:`is_quit_command`): ``"/agent build"`` → ``"build"``; bare ``"/agent"`` →
    ``""`` (the command with no name — the handler turns that into a usage line); anything that is
    not the ``/agent`` command (``"hello"``, ``"/agentx"``, ``"/mode plan"``) → ``None`` so the
    main loop falls through to its normal routing.
    """
    return _parse_slash_arg(line, _AGENT_COMMAND)


def parse_mode_command(line: str) -> str | None:
    """Return the mode argument of a ``/mode <name>`` line, or ``None`` if not that command.

    Pure (mirrors :func:`parse_agent_command`): ``"/mode plan"`` → ``"plan"``; bare ``"/mode"`` →
    ``""``; not the command → ``None``.
    """
    return _parse_slash_arg(line, _MODE_COMMAND)


def _parse_slash_arg(line: str, command: str) -> str | None:
    """Split a ``<command> <arg>`` slash line, returning the (stripped) arg or ``None``.

    ``None`` means the line is not ``command`` at all (fall through to normal routing); ``""`` means
    ``command`` was typed with no argument (a usage error for the caller); otherwise the trailing
    argument, stripped.
    """
    stripped = line.strip()
    if stripped == command:
        return ""
    prefix = f"{command} "
    if stripped.startswith(prefix):
        return stripped[len(prefix) :].strip()
    return None


def parse_skill_command(line: str) -> tuple[str, str] | None:
    """Split a ``/<skill-name> [trailing]`` line into ``(name, trailing)``, or ``None`` (§5).

    The user-facing second entry point into a skill body (ADR-0004 §5), alongside the model's
    ``skill`` dispatcher (task 026) — both resolve through the same ``load_skills(cwd)``. Pure
    (mirrors :func:`parse_agent_command`): ``"/commit"`` → ``("commit", "")``; ``"/commit fix the
    bug"`` → ``("commit", "fix the bug")`` (name + trailing text, both stripped). A non-slash line
    (``"hello"``) → ``None`` so the main loop falls through to its normal ``runner.submit`` routing.
    A bare ``"/"`` (no name) → ``None``. A reserved slash command (``/quit`` / ``/agent …`` /
    ``/mode …``) parses here too, but never reaches this branch: the ``run_app`` loop matches and
    ``continue``s on those *before* the skill branch, so precedence is the loop's job, not this
    parser's (a same-named skill stays reachable via the dispatcher).
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    name, _, trailing = stripped[1:].partition(" ")
    name = name.strip()
    if not name:
        return None
    return name, trailing.strip()


def parse_mode_name(name: str) -> PermissionMode | None:
    """Map a typed mode name to a :class:`PermissionMode`, or ``None`` if it is not one (§1,9).

    Case-insensitive and whitespace-tolerant (``"  PLAN  "`` → ``PLAN``). Pure so it is
    unit-testable; ``None`` lets the caller render a friendly "unknown mode" line instead of crashing.
    """
    try:
        return PermissionMode(name.strip().lower())
    except ValueError:
        return None


def next_mode(mode: PermissionMode) -> PermissionMode:
    """The next mode in the Shift+Tab ring: default → edit → plan → bypass → default (§9).

    Pure (a single index step around :data:`_MODE_CYCLE`) so the cycle is unit-testable; the
    keybind just calls it, sets the gate, and renders the new mode.
    """
    index = _MODE_CYCLE.index(mode)
    return _MODE_CYCLE[(index + 1) % len(_MODE_CYCLE)]


def mode_switch_confirmation(mode: str) -> str:
    """The one-line confirmation rendered after a mode switch (Shift+Tab / ``/mode``; §9)."""
    return f"Decode - mode: {mode}."


def agent_switch_confirmation(name: str, mode: str) -> str:
    """The one-line confirmation rendered after an agent switch (``/agent``; §9).

    Names the new agent and the mode it reset the gate to (selecting an agent resets the mode to
    that agent's default — ADR-0003 §7).
    """
    return f"Decode - agent: {name} (mode: {mode})."


# The inline usage lines shown when ``/agent`` / ``/mode`` are typed with no argument.
_AGENT_USAGE = "Decode - usage: /agent <name> (build / plan / explore / code-reviewer)."
_MODE_USAGE = "Decode - usage: /mode <name> (default / plan / edit / bypass)."


def _handle_agent_command(
    name: str,
    *,
    deps: AgentDeps,
    gate: PermissionGate,
    emit: Callable[[str], None],
) -> None:
    """Apply a ``/agent <name>`` switch: select the agent, render one confirmation (§7,9).

    Runs the task-020 :func:`~decode.agents.select.select_agent` helper (sets ``deps.active_agent``,
    resets the gate to the agent's default mode, loads its catalog rules). An empty ``name`` shows a
    usage line; an unknown name renders a friendly inline error (``select_agent`` leaves ``deps`` /
    ``gate`` untouched on failure) and the REPL stays alive — never a crash.
    """
    if not name:
        emit(_AGENT_USAGE)
        return
    try:
        agent_def = select_agent(name, deps=deps, gate=gate)
    except ValueError as exc:
        logger.debug("/agent %r rejected: %s", name, exc)
        emit(f"Decode - {exc}")
        return
    logger.debug("/agent switched to %s (mode=%s)", agent_def.name, gate.mode.value)
    emit(agent_switch_confirmation(agent_def.name, gate.mode.value))


def _handle_mode_command(
    name: str,
    *,
    gate: PermissionGate,
    emit: Callable[[str], None],
) -> None:
    """Apply a ``/mode <name>`` switch: set the gate mode, render one confirmation (§3,9).

    An empty ``name`` shows a usage line; an unknown mode renders a friendly inline error (listing
    the valid modes) and leaves the gate untouched — never a crash.
    """
    if not name:
        emit(_MODE_USAGE)
        return
    mode = parse_mode_name(name)
    if mode is None:
        valid = ", ".join(m.value for m in PermissionMode)
        logger.debug("/mode %r rejected (unknown)", name)
        emit(f"Decode - unknown mode {name!r}; valid modes: {valid}.")
        return
    gate.set_mode(mode)
    logger.debug("/mode switched to %s", mode.value)
    emit(mode_switch_confirmation(mode.value))


# The friendly inline line for an unrecognised ``/<skill-name>`` (ADR-0004 §5). Mirrors the
# ``/agent`` / ``/mode`` unknown-argument style (a single ``Decode - …`` line); the available-skills
# list doubles as discovery. The catalog is never empty — the built-ins always ship.
_SKILL_NO_MATCH = "Decode - unknown command '/{name}'; available skills: {skills}."


def _handle_skill_command(
    name: str, trailing: str, *, cwd: Path, emit: Callable[[str], None]
) -> str | None:
    """Resolve a ``/<name>`` skill into the turn input, or ``emit`` a discovery line (§5).

    The user-facing entry point into a skill body (ADR-0004 §5): resolves ``name`` against the
    merged catalog (:func:`decode.skills.loader.load_skills` for ``cwd`` — the **same** loader the
    model's ``skill`` dispatcher uses). On a match, returns the skill ``body`` as the turn input
    (the caller submits it through the existing ``runner.submit`` pipeline); a non-empty ``trailing``
    is appended after a blank line (``f"{body}\\n\\n{trailing}"``). On no match, ``emit`` a friendly
    one-line message listing the available (sorted) skill names — discovery — and return ``None`` so
    no turn runs. ``name`` is used **only** as a dict key (never interpolated into a filesystem path
    or shell command — ADR-0004 §3,7), so a bad name just yields the available-skills line.
    """
    catalog = load_skills(cwd)
    found = catalog.get(name)
    if found is None:
        emit(_SKILL_NO_MATCH.format(name=name, skills=", ".join(sorted(catalog))))
        logger.debug("/%s is not a known skill (available: %s)", name, sorted(catalog))
        return None
    logger.debug("/%s resolved to skill body (source=%s)", name, found.source)
    if trailing:
        return f"{found.body}\n\n{trailing}"
    return found.body


def parse_permission_answer(answer: str) -> PermissionDecision:
    """Map a typed allow/deny answer to a :class:`PermissionDecision` (ADR-0002 §3, ADR-0003 §4).

    ``y`` / ``yes`` / ``allow`` and ``a`` / ``always`` (case-insensitive) all allow; **anything
    else denies** — the safe default. ``a``/``always`` *also* persists a rule (see
    :func:`is_always_answer`), but the verdict itself is just allow. A denial carries a
    human-facing reason that is fed back to the model. Kept pure (string in, decision out) so it
    is unit-testable; the interactive prompt that reads the answer is not.
    """
    if answer.strip().lower() in _AFFIRMATIVE_ANSWERS:
        return PermissionDecision.allow()
    return PermissionDecision.deny(reason="The user denied this tool call.")


def is_always_answer(answer: str) -> bool:
    """Whether ``answer`` is the "always" allow (``a`` / ``always``, case-insensitive; §4).

    An "always" answer allows the call **and** signals the resolver to persist a matching allow
    rule to the user ``.decode/settings.json`` so the next identical call auto-allows. ``y``/``yes``
    is allow-once and returns ``False`` here. Pure so it is unit-testable.
    """
    return answer.strip().lower() in _ALWAYS_ANSWERS


async def deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """The safe headless default: deny every tool call (ADR-0002 §3).

    Used when there is no interactive terminal to ask. Denying (rather than allowing) is the
    safe default — an unattended run never executes a gated side effect.
    """
    logger.debug("headless permission resolver denying tool=%s", request.tool_name)
    return PermissionDecision.deny(reason="No interactive terminal to approve this tool call.")


def _make_permission_resolver(
    channel: DecisionChannel,
    console: Console,
    *,
    gate: PermissionGate,
    permissions_file: Path,
) -> PermissionResolver:
    """Build the interactive allow/deny resolver bound to the decision ``channel`` (ADR-0003 §4).

    The full request was already rendered once by the loop's ``PermissionRequested`` event
    (single render path), so the resolver only shows a minimal inline ``allow/deny?``
    affordance and then **awaits the next submitted line on the channel** — it never opens a
    second ``prompt_async()`` on the live session (that would deadlock the REPL). The answer
    is parsed by :func:`parse_permission_answer`. An ``a``/``always`` answer additionally
    **persists** a matching allow rule to the user ``permissions_file`` and **reloads** the
    gate's user rules so the next identical call auto-allows; a persist write failure is
    non-fatal (logged, falls back to allow-once). If the request is cancelled (turn aborted /
    REPL shutting down) it denies — the safe default. Not unit-tested directly; the
    end-to-end ``run_app`` regression test drives the real channel + main loop, and the
    decidable parsing lives in :func:`parse_permission_answer`.
    """

    async def resolver(request: PermissionRequest) -> PermissionDecision:
        console.print(render.render_event(events.AssistantTextDelta(text=_PERMISSION_AFFORDANCE)))
        try:
            answer = await channel.request()
        except asyncio.CancelledError:
            return PermissionDecision.deny(reason="The user dismissed the approval prompt.")
        decision = parse_permission_answer(answer)
        if decision.outcome is PermissionOutcome.ALLOW and is_always_answer(answer):
            _persist_always_rule(gate, permissions_file, request)
        return decision

    return resolver


def _persist_always_rule(
    gate: PermissionGate, permissions_file: Path, request: PermissionRequest
) -> None:
    """Persist an allow rule for ``request`` and reload the gate's user rules (ADR-0003 §4).

    Called when the human answers ``a``/``always``: write a matching allow rule to the user
    ``permissions_file`` and reload it onto the gate so the next identical call auto-allows. A
    write failure (e.g. a read-only dir) is **non-fatal** — it is logged and the turn proceeds as
    a plain allow-once, never breaking the turn (ADR-0003 §4 / Consequences).
    """
    try:
        rules.persist_allow_rule(permissions_file, request)
    except OSError:
        logger.warning("failed to persist always-allow rule; allowing once", exc_info=True)
        return
    gate.set_user_rules(rules.load_rule_set(permissions_file))


def _make_user_question_resolver(
    channel: DecisionChannel, console: Console
) -> UserQuestionResolver:
    """Build the interactive ``ask_user`` resolver bound to the decision ``channel`` (§2,7).

    The full question was already rendered once by the tool's ``AskUserRequested`` event (single
    render path), so the resolver only shows a minimal "type your answer" affordance and then
    **awaits the next submitted line on the same channel** the permission resolver uses — it
    never opens a second ``prompt_async()`` (that would deadlock the REPL). Unlike the permission
    resolver there is no parsing: the raw typed line *is* the answer, returned straight to the
    model as the ``ask_user`` tool result. A cancelled request (turn aborted / REPL shutting
    down) propagates :class:`asyncio.CancelledError` out of the channel, which
    :func:`decode.tools.askuser.ask_user` maps to a model-readable ``ModelRetry`` — so the turn
    winds down cleanly instead of hanging. Not unit-tested directly; the end-to-end ``run_app``
    regression test drives the real channel + main loop.
    """

    async def resolver(question: str) -> str:
        console.print(render.render_event(events.AssistantTextDelta(text=_ASK_USER_AFFORDANCE)))
        return await channel.request()

    return resolver


def _bottom_toolbar(deps: AgentDeps, gate: PermissionGate) -> HTML:
    """The footer hint as prompt_toolkit formatted text, reading the **live** agent + mode (§9).

    Called by prompt_toolkit on every render with the session's ``deps`` + ``gate``, so the footer
    reflects the current ``deps.active_agent`` and ``gate.mode`` — it updates immediately after a
    ``/agent`` / ``/mode`` switch or a Shift+Tab cycle (it reads them live, never a snapshot).
    """
    return HTML(f"<b>{footer_hint(deps.active_agent.name, gate.mode.value)}</b>")


def _build_key_bindings(*, on_cycle_mode: Callable[[], None]) -> KeyBindings:
    """Register the follow-up (Alt+Enter), abort (Esc), and mode-cycle (Shift+Tab) keybindings.

    Alt+Enter / Esc accept the prompt with an explicit ``(intent, text)`` result so the loop can
    route it (``Alt+Enter`` arrives as the ``escape, enter`` sequence). **Shift+Tab** (``s-tab`` /
    ``Keys.BackTab``, ADR-0003 §9) is different: it does *not* submit a line — it calls
    ``on_cycle_mode`` (which cycles the gate mode and renders the new one) and invalidates the app
    so the bottom toolbar redraws with the new mode, leaving the typed buffer intact. The three
    bindings are distinct keys (``s-tab`` never collides with ``escape`` / ``escape enter``).
    """
    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _follow_up(event: KeyPressEvent) -> None:
        event.app.exit(result=(InputIntent.FOLLOW_UP, event.app.current_buffer.text))

    @bindings.add("escape")
    def _abort(event: KeyPressEvent) -> None:
        event.app.exit(result=(InputIntent.ABORT, event.app.current_buffer.text))

    @bindings.add("s-tab")
    def _cycle_mode(event: KeyPressEvent) -> None:
        on_cycle_mode()
        event.app.invalidate()  # redraw the bottom toolbar with the new mode

    return bindings


def _interpret(submitted: object) -> tuple[InputIntent, str]:
    """Normalize ``prompt_async`` results into an ``(intent, text)`` pair.

    A plain ``Enter`` returns the line as a ``str`` (intent ``STEER``); the keybindings
    return an explicit ``(intent, text)`` tuple.
    """
    if isinstance(submitted, tuple):
        intent, text = submitted
        return intent, text
    return InputIntent.STEER, str(submitted)


def _load_resume_history(resume: str | None, console: Console) -> list[ModelMessage]:
    """Replay the requested session into a seed ``message_history`` (ADR-0002 §9, task 014).

    ``None`` → no resume, a fresh conversation (empty history). ``"latest"`` (the bare
    ``--resume`` flag) → the most recent session under ``settings.sessions_dir``; any other
    value → the session whose filename embeds that id / filename. When there is nothing to
    resume the user is told so **once** (a friendly line, not an error) and a fresh
    conversation starts — a resume should never crash the REPL. Returns the replayed messages
    (possibly empty).
    """
    if resume is None:
        return []

    if resume == _RESUME_LATEST:
        history = load_latest(settings.sessions_dir)
        if history is None:
            console.print(
                render.render_event(
                    events.AssistantTextDelta(text="Decode - no prior session to resume.")
                )
            )
            return []
        logger.debug("resumed latest session with %d message(s)", len(history))
        return history

    path = resolve_session(settings.sessions_dir, resume)
    if path is None:
        console.print(
            render.render_event(
                events.AssistantTextDelta(text=f"Decode - no session matching {resume!r}.")
            )
        )
        return []
    history = load(path)
    logger.debug("resumed session %s with %d message(s)", path.name, len(history))
    return history


def _make_event_sink(console: Console) -> Callable[[events.Event], None]:
    """Build the harness event sink that renders events append-style above the pinned prompt.

    Beyond rendering each event via the pure :func:`decode.tui.render.render_event`, the sink
    owns the one piece of state the pure renderer cannot: the once-per-turn ``Decode `` prefix
    (Fix 2). Assistant answer text **streams** as many :class:`~decode.entities.events.AssistantTextDelta`
    events, so the capital-D label must be printed **once**, just before the first delta of a
    turn — and the small ``need_prefix`` flag is **reset on each**
    :class:`~decode.entities.events.TurnStarted` so every turn gets exactly one prefix. Keeping
    this flag here (not in the renderer) is what lets the render functions stay pure and
    stateless.
    """
    state = {"need_prefix": False}

    def on_event(event: events.Event) -> None:
        if isinstance(event, events.TurnStarted):
            state["need_prefix"] = True
        elif isinstance(event, events.AssistantTextDelta) and state["need_prefix"]:
            # Print the `Decode ` label once (on the conversation background, no newline) so it
            # reads as the lead-in to the streamed answer that immediately follows.
            console.print(Text(_ASSISTANT_PREFIX, style=render.CONVERSATION_BG), end="")
            state["need_prefix"] = False
        console.print(render.render_event(event))

    return on_event


def _apply_startup_mode(mode: str | None, gate: PermissionGate) -> None:
    """Override the gate mode from the optional ``--mode`` startup flag (ADR-0003 §9).

    ``None`` keeps the selected agent's default mode. An unknown value (the CLI validates first, so
    this is belt-and-suspenders) is logged and ignored rather than crashing the launch.
    """
    if mode is None:
        return
    parsed = parse_mode_name(mode)
    if parsed is None:
        logger.warning("ignoring unknown startup mode %r", mode)
        return
    gate.set_mode(parsed)


async def run_app(
    console: Console | None = None,
    *,
    resume: str | None = None,
    agent: str = _DEFAULT_AGENT,
    mode: str | None = None,
) -> None:
    """Run the REPL until ``Ctrl-D`` or ``/quit``, routing input into the harness.

    ``console`` is injectable so callers/tests can capture output; defaults to a real
    stdout-backed :class:`rich.console.Console`. The harness drives the real Pydantic AI
    loop behind the turn-handler seam; gated tool calls (task 005) pause the turn, surface a
    permission prompt via :func:`_make_permission_resolver`, and resume on the answer.

    ``resume`` wires ``decode --resume`` (ADR-0002 §9): ``"latest"`` (the bare flag) replays the
    most recent session, a session id / filename replays that specific one, and ``None`` (the
    default) starts fresh. The replayed history seeds the turn handler so the conversation
    continues; if there is nothing to resume the user is told so and a fresh session starts.
    Every ``run_app`` opens a **new** session log file, so a resumed run continues the
    conversation into a fresh append-only log.

    ``agent`` is the startup Agent persona (ADR-0003 §7,9; default ``build``). Before the loop,
    :func:`~decode.agents.select.select_agent` sets it as ``deps.active_agent`` (so the factory's
    instructions hook + per-tool ``prepare=`` scope the prompt and tool set to it), resets the gate
    to the agent's default mode, and loads the agent's catalog rules. The CLI already validated the
    name, so selection here does not fail for a startup launch.

    ``mode`` is the optional startup permission mode (``--mode``; ADR-0003 §9). ``None`` keeps the
    selected agent's default mode; otherwise it **overrides** that default (applied *after*
    ``select_agent``, which resets the gate to the agent's default). The CLI validated it, so an
    unknown value here is logged and ignored (never crashes the launch).

    The single input loop has three control surfaces, all on the **one** input surface (ADR-0003
    §9): the ``/agent`` / ``/mode`` slash commands (parsed before submit) and the Shift+Tab mode
    cycle keybind, alongside the two awaiting-decision modes (see module docstring): when the
    :class:`~decode.harness.decisions.DecisionChannel` is *awaiting a decision*, the next line
    fulfils the pending mid-turn request; otherwise it routes to the runner normally.
    """
    console = console or Console()

    # Capture the startup persona name now: ``agent`` is rebound below to the built Pydantic AI
    # Agent, so the persona string must be saved before that shadows it (one Agent runs every
    # persona — ADR-0003 §7 — and the persona rides ``deps.active_agent``, not the Agent object).
    agent_name = agent

    # The harness streams events into this sink; it renders append-style above the pinned prompt
    # and owns the once-per-turn ``Decode `` answer prefix (Fix 2 — the pure renderer can't).
    _on_event = _make_event_sink(console)

    # The agent loop is the turn handler: build the Gemini agent, bind the event sink so
    # streamed deltas reach the renderer, the gate (policy) + the interactive allow/deny
    # resolver (ADR-0002 §3) and the interactive ask_user resolver (§2,7). Both resolvers await
    # on the SAME decision channel — the single mid-turn HITL surface — so a permission ask and
    # an ask_user question can never collide (the channel is single-flight). One handler per
    # session carries history (§1).
    decisions = DecisionChannel()
    agent = build_agent()
    # The gate loads the user's optional allow/deny rules from ``.decode/settings.json`` (ADR-0003
    # §4); a missing/malformed file is non-fatal (empty rules → mode-only). The interactive
    # ``a``/``always`` answer persists into and reloads this same file via the resolver below.
    gate = PermissionGate(user_rules=rules.load_rule_set(settings.permissions_file))
    deps = AgentDeps(
        cwd=Path.cwd(),
        emit=_on_event,
        gate=gate,
        resolve_permission=_make_permission_resolver(
            decisions, console, gate=gate, permissions_file=settings.permissions_file
        ),
        resolve_user_question=_make_user_question_resolver(decisions, console),
    )
    # Select the startup Agent persona (ADR-0003 §7,9): set ``deps.active_agent`` (the prompt + tool
    # allowlist the factory reads per turn), reset the gate to the agent's default mode, and load the
    # agent's catalog rules. The CLI validated the name already, so this does not fail at startup.
    select_agent(agent_name, deps=deps, gate=gate)
    # Apply the optional ``--mode`` override AFTER selection (which reset the gate to the agent's
    # default mode): an explicit ``--mode`` wins over the agent default (ADR-0003 §9). The CLI
    # already validated it; an unexpected value is logged and ignored (never crashes the launch).
    _apply_startup_mode(mode, gate)

    # The single input surface (ADR-0003 §9): a confirmation sink that renders one line through the
    # existing event/render path (no second render surface), plus the Shift+Tab mode-cycle closure
    # the keybind calls. The bottom toolbar reads ``deps`` / ``gate`` live each render, so the footer
    # updates the moment a switch lands.
    def emit_line(text: str) -> None:
        console.print(render.render_event(events.AssistantTextDelta(text=text)))

    def cycle_mode() -> None:
        new_mode = next_mode(gate.mode)
        gate.set_mode(new_mode)
        emit_line(mode_switch_confirmation(new_mode.value))

    session: PromptSession[object] = PromptSession(
        key_bindings=_build_key_bindings(on_cycle_mode=cycle_mode),
        bottom_toolbar=lambda: _bottom_toolbar(deps, gate),
    )
    # Persistence (ADR-0002 §9): replay a prior session if asked, then open a fresh append-only
    # JSONL log this run writes its turns to. The replayed history seeds the handler so the
    # conversation continues; the new log starts after the replayed prefix (already-persisted).
    resumed_history = _load_resume_history(resume, console)
    session_log = SessionLog.create(settings.sessions_dir, cwd=deps.cwd)

    # Hold the handler directly: it owns the cross-turn ``message_history`` the on-exit memory
    # write-back summarizes (the runner keeps it private). One handler per session (§1).
    handler = AgentTurnHandler(
        agent, deps=deps, session_log=session_log, message_history=resumed_history
    )
    runner = Runner(handler, on_event=_on_event)

    console.print(
        render.render_event(events.AssistantTextDelta(text="Decode - type a line; /quit exits."))
    )

    with patch_stdout(raw=True):
        while True:
            try:
                submitted = await session.prompt_async(_PROMPT)
            except EOFError:  # Ctrl-D
                break

            intent, text = _interpret(submitted)

            # Awaiting-decision mode: the next line answers whatever mid-turn request is pending
            # — a permission approval (parsed y/N inside the permission resolver) OR an ask_user
            # question (the raw line is the free-text answer inside the ask_user resolver) —
            # instead of steering / starting a turn. The main loop routes the raw line either
            # way; the awaiting resolver does any parsing, so the channel stays one input surface.
            if decisions.pending:
                logger.debug("decision pending: routing %r to the decision channel", text)
                decisions.resolve(text)
                continue

            if is_quit_command(text):
                break

            if intent is InputIntent.ABORT:
                logger.debug("abort intent: setting cooperative-abort flag")
                runner.abort()
                continue

            # Control slash commands (ADR-0003 §9), parsed on the single input surface *before*
            # submit: switch the active agent / mode and render one confirmation (or a friendly
            # inline line on a bad name). Never opens a second prompt — the line is consumed here.
            agent_arg = parse_agent_command(text)
            if agent_arg is not None:
                _handle_agent_command(agent_arg, deps=deps, gate=gate, emit=emit_line)
                continue

            mode_arg = parse_mode_command(text)
            if mode_arg is not None:
                _handle_mode_command(mode_arg, gate=gate, emit=emit_line)
                continue

            # The user-facing skill entry point (ADR-0004 §5), parsed AFTER the reserved
            # ``/agent`` / ``/mode`` checks so a same-named built-in command always wins. A
            # resolved skill injects its body as the turn input through the existing submit
            # pipeline; an unrecognised ``/<x>`` (neither reserved nor a known skill) is
            # intercepted with the available-skills discovery line and runs no turn.
            skill_cmd = parse_skill_command(text)
            if skill_cmd is not None:
                name, trailing = skill_cmd
                turn_input = _handle_skill_command(name, trailing, cwd=deps.cwd, emit=emit_line)
                if turn_input is not None:
                    await runner.submit(turn_input, intent)
                continue

            if not text.strip():
                continue

            logger.debug(
                "submit intent=%s phase=%s text=%r", intent.value, runner.phase.value, text
            )
            await runner.submit(text, intent)

    # Unblock any resolver still awaiting a decision so the in-flight turn can wind down
    # (it falls back to its safe default: deny), then wait for the runner to go idle.
    decisions.cancel()
    await runner.wait_idle()

    # On-exit memory write-back (ADR-0002 §8): one cheap Gemini call summarizes the session into
    # a dated line appended to the project-root MEMORY.md, picked up next session by
    # assemble_memory. The accumulated conversation lives on the handler; ``deps.cwd`` is the
    # project root. Fully non-fatal — extract_on_exit never raises, so it cannot block exit.
    await extract_on_exit(handler.message_history, deps.cwd)

    console.print(render.render_event(events.AssistantTextDelta(text="Decode - bye.")))
