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
(:func:`is_quit_command`, :func:`is_compact_command`, :func:`footer_hint`, :class:`InputIntent`,
:func:`parse_permission_answer`, the control-surface parsers
:func:`parse_agent_command` / :func:`parse_mode_command` / :func:`parse_mode_name` /
:func:`parse_skill_command`, and the Shift+Tab :func:`next_mode` cycle) are pure and unit-tested.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
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
from decode.harness.runner import Phase, Runner
from decode.memory.extract import extract_on_exit
from decode.permissions import rules
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.services.lsp.service import shutdown_all as shutdown_lsp_servers
from decode.skills.loader import load_skills
from decode.skills.payload import format_skill_payload
from decode.tools.bash import close_executor, warm_executor
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
# The manual full-compaction command (ADR-0006 §7): forces a full compaction now, wired like
# ``/quit`` and idle-only. Reserved among the slash commands (matched before ``parse_skill_command``)
# so a project skill named ``compact`` can never shadow it.
_COMPACT_COMMAND = "/compact"
# The conversation-wipe command: compaction-to-zero, wired exactly like ``/compact`` (idle-only,
# reserved before the skill branch). Summarize-then-wipe: the pre-clear history feeds the same
# MEMORY.md write-back the quit path runs, THEN the handler resets and a clear marker rides the
# session log so ``--resume`` replays to the post-clear state.
_CLEAR_COMMAND = "/clear"
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


def is_compact_command(line: str) -> bool:
    """True when ``line`` is the ``/compact`` command (ignoring surrounding whitespace).

    Pure (mirrors :func:`is_quit_command`): exact match after a strip — ``"/compact"`` and
    ``"  /compact  "`` are the command; ``"/compactx"`` / ``"compact"`` / ``"/quit"`` are not.
    """
    return line.strip() == _COMPACT_COMMAND


def is_clear_command(line: str) -> bool:
    """True when ``line`` is the ``/clear`` command (ignoring surrounding whitespace).

    Pure (mirrors :func:`is_compact_command`): exact match after a strip — ``"/clear"`` and
    ``"  /clear  "`` are the command; ``"/clearx"`` / ``"clear"`` / ``"/compact"`` are not.
    """
    return line.strip() == _CLEAR_COMMAND


def footer_hint(agent: str, mode: str) -> str:
    """The bottom-toolbar hint: the live agent + mode, then the interaction keys (plain text).

    Kept pure and string-returning so it is unit-testable; :func:`_bottom_toolbar` wraps it for
    prompt_toolkit and supplies the **live** ``agent`` / ``mode`` each render (ADR-0003 §9), so the
    footer updates after a ``/agent`` / ``/mode`` switch or a Shift+Tab cycle. Lists steer (plain
    Enter), follow-up (Alt+Enter), abort (Esc), the Shift+Tab mode cycle, and the slash commands.
    """
    return (
        f"agent:{agent} mode:{mode} | Enter steer | Alt+Enter follow-up | "
        "Esc abort | Shift+Tab mode | /agent /mode /compact /clear /quit"
    )


def startup_banner(provider: str, model: str, sandbox_mode: str) -> str:
    """The one-line startup banner: provider:model (+ the sandbox mode when one is active).

    Pure and string-returning (mirrors :func:`footer_hint`) so it is unit-testable. ``none`` —
    the plain REPL — renders **byte-identical** to before sandboxing existed; ``docker`` /
    ``modal`` insert a ``sandbox:<mode>`` segment so the user can SEE the session's ``bash``
    commands run in a sandbox (the mode is fixed per session — ADR-0011 §1 — so the banner, not
    the live footer, is its home).
    """
    if sandbox_mode == "none":
        return f"Decode - {provider}:{model} - type a line; /quit exits."
    return f"Decode - {provider}:{model} - sandbox:{sandbox_mode} - type a line; /quit exits."


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


class SlashCompleter(Completer):
    """Autocomplete the slash commands + project skills as the user types ``/`` (like Claude Code).

    Built once per session from ``load_skills(cwd)`` so the menu lists every reachable ``/<skill>``
    (its ``description`` as the meta) alongside the four built-in commands the ``run_app`` loop
    matches before the skill branch. Fires **only** while the line-before-cursor is a single ``/``
    token (no space yet) — so normal prose, and the ``<arg>`` after ``/agent``/``/mode``, never
    trigger it. ``prompt_toolkit`` renders the menu; this just supplies the candidates.
    """

    def __init__(self, cwd: Path) -> None:
        self._meta = {
            _AGENT_COMMAND: "switch the active agent (/agent <name>)",
            _MODE_COMMAND: "switch the permission mode (/mode <name>)",
            _COMPACT_COMMAND: "compact the conversation now",
            _CLEAR_COMMAND: "clear the conversation (summarizes to memory first)",
            _QUIT_COMMAND: "exit decode",
        }
        self._meta.update(
            {f"/{name}": skill.description for name, skill in load_skills(cwd).items()}
        )

    def get_completions(self, document, complete_event):  # type: ignore[no-untyped-def]
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for command in sorted(self._meta):
            if command.startswith(text):
                yield Completion(
                    command, start_position=-len(text), display_meta=self._meta[command]
                )


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


# The two inline lines the ``/compact`` command renders itself (the success path renders nothing —
# the handler's ``ContextCompacted`` event is the feedback). ``_COMPACT_NOTHING`` is the idle no-op;
# ``_COMPACT_BUSY`` is shown when a turn is in flight (we never compact mid-turn).
_COMPACT_NOTHING = "Decode - nothing to compact yet."
_COMPACT_BUSY = "Decode - busy; try /compact again once the turn finishes."


async def _handle_compact_command(
    handler: AgentTurnHandler,
    runner: Runner,
    *,
    emit: Callable[[str], None],
) -> None:
    """Force a full compaction now — the manual ``/compact`` command (ADR-0006 §7).

    Idle-only, wired like ``/quit``: if a turn is in flight we never compact mid-turn (the handler
    owns the live ``message_history``), so we emit the busy line and leave the turn untouched. When
    idle, run the handler's full-compaction tier — an explicit user request compacts regardless of
    the window-relative thresholds or ``compaction_enabled``. ``True`` means it already emitted
    :class:`~decode.entities.events.ContextCompacted` (history replaced with ``[summary, *tail]``),
    so we add no extra line; ``False`` means there was nothing to compact and we say so.
    """
    if runner.phase is not Phase.IDLE:
        emit(_COMPACT_BUSY)
        return
    if not await handler.compact():
        emit(_COMPACT_NOTHING)


# The inline lines the ``/clear`` command renders (mirroring the ``/compact`` pair, plus a
# confirmation — unlike ``/compact`` there is no event to render, so the command says what it did).
_CLEAR_DONE = "Decode - conversation cleared."
_CLEAR_NOTHING = "Decode - nothing to clear yet."
_CLEAR_BUSY = "Decode - busy; try /clear again once the turn finishes."


async def _handle_clear_command(
    handler: AgentTurnHandler,
    runner: Runner,
    *,
    cwd: Path,
    emit: Callable[[str], None],
) -> None:
    """Wipe the conversation now — the ``/clear`` command (compaction-to-zero).

    Idle-only, exactly like ``/compact`` (the handler owns the live ``message_history``; wiping
    mid-turn would corrupt the leg mutating it): busy → one line, turn untouched. Idle with an
    empty history → nothing to wipe, say so. Otherwise **summarize, then wipe**: the pre-clear
    history first feeds the same non-fatal MEMORY.md write-back the quit path runs (``/clear`` is
    a soft session boundary — every segment still contributes to cross-session memory; a missing
    key / failure no-ops), then :meth:`~decode.agent.loop.AgentTurnHandler.clear` resets the
    handler and rides a ``clear`` marker into the session log so ``--resume`` replays to the
    post-clear state. One confirmation line renders.
    """
    if runner.phase is not Phase.IDLE:
        emit(_CLEAR_BUSY)
        return
    if not handler.message_history:
        emit(_CLEAR_NOTHING)
        return
    await extract_on_exit(handler.message_history, cwd)
    handler.clear()
    emit(_CLEAR_DONE)


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
    model's ``skill`` dispatcher uses) and formats the result through the **same**
    :func:`decode.skills.payload.format_skill_payload` helper the dispatcher uses, so both entry
    points inject an identical payload — the skill ``body`` plus a resource trailer when (and only
    when) the skill ships bundled resources. On a match, returns that payload as the turn input (the
    caller submits it through the existing ``runner.submit`` pipeline); a non-empty ``trailing`` is
    appended after a blank line (``f"{payload}\\n\\n{trailing}"``). On no match, ``emit`` a friendly
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
    logger.debug("/%s resolved to skill payload (source=%s)", name, found.source)
    payload = format_skill_payload(found, cwd=cwd)
    if trailing:
        return f"{payload}\n\n{trailing}"
    return payload


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


# Footer animation cadence: prompt_toolkit re-renders the bottom toolbar every this many seconds
# (PromptSession ``refresh_interval``) so the "working…" spinner animates while a turn runs — the
# footer otherwise only repaints on keystrokes. Also the spinner's tick unit: one frame per refresh.
_FOOTER_REFRESH_S = 0.1


def _bottom_toolbar(
    deps: AgentDeps,
    gate: PermissionGate,
    handler: AgentTurnHandler,
    runner: Runner,
    decisions: DecisionChannel,
) -> HTML:
    """The footer as prompt_toolkit formatted text: a busy spinner + context fill gauge + live hint.

    Called by prompt_toolkit on every render with the session's ``deps`` + ``gate`` + ``handler`` +
    ``runner`` + ``decisions``, so the footer reflects the current ``deps.active_agent`` /
    ``gate.mode`` (updating immediately after a ``/agent`` / ``/mode`` switch or Shift+Tab cycle) and
    the live context fill.

    While a turn is **actively working** the footer leads with an animated braille spinner +
    ``working…`` so the user knows to wait — an indeterminate busy indicator, not a 0→1 bar. The
    frame advances each refresh; the PromptSession's ``refresh_interval`` (``_FOOTER_REFRESH_S``)
    drives the animation while the turn runs as a background task. "Actively working" means
    ``runner.phase`` is not :data:`~decode.harness.runner.Phase.IDLE` **and** the turn is not paused
    awaiting a human decision: during an ``ask_user`` question or a permission prompt the turn task
    is suspended on the :class:`~decode.harness.decisions.DecisionChannel` (``decisions.pending``),
    where the agent is waiting on the *user* — so the spinner is hidden (else it would read
    "working…" while the user is the one being asked to type). When idle or awaiting the human the
    spinner is absent and the footer is just the gauge + hint.

    The gauge (ADR-0006 §9, task 047) reads the handler's **public** ``last_input_tokens`` property
    (never the private attr) over the single source of truth ``compaction_context_window_tokens``,
    and colors itself by the same reserve fractions the compaction cascade fires on — ``warn_at`` /
    ``danger_at`` are the *fill* lines ``1 - microcompaction_reserve_fraction`` (0.60 default) and
    ``1 - compaction_reserve_fraction`` (0.80 default). Before the first turn ``last_input_tokens``
    is ``0`` → the gauge renders ``○ 0%`` in green.
    """
    window = settings.compaction_context_window_tokens
    fraction = handler.last_input_tokens / window if window > 0 else 0.0
    warn_at = 1 - settings.microcompaction_reserve_fraction
    danger_at = 1 - settings.compaction_reserve_fraction
    label, color = render.context_gauge(fraction, warn_at=warn_at, danger_at=danger_at)
    hint = footer_hint(deps.active_agent.name, gate.mode.value)
    # Show the spinner only while the turn is ACTIVELY working — not when idle, and not while it is
    # paused awaiting a human decision (ask_user / permission), where the user must type, not wait.
    if runner.phase is Phase.IDLE or decisions.pending:
        spinner = ""
    else:
        frame = render.spinner_frame(int(time.monotonic() / _FOOTER_REFRESH_S))
        spinner = f'<style fg="cyan">{frame} working…</style> '
    return HTML(f'{spinner}<style fg="{color}">{label}</style> <b>{hint}</b>')


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
    # Streamed answer/thinking deltas are LINE-BUFFERED. prompt_toolkit's ``patch_stdout()`` redraws
    # output above the live prompt and corrupts *partial*-line writes (each redraw overwrites the
    # start of the line, leaving only tail fragments), so we accumulate deltas and print only
    # COMPLETE lines — split on the model's own ``\n`` — flushing the partial tail when the turn ends
    # or any non-streamed event interrupts. A complete line wider than the terminal is wrapped by
    # Rich, so a new visual line happens only at the width or a model newline, never once per chunk.
    state = {"need_prefix": False, "buffer": "", "style": render.CONVERSATION_BG}

    def _emit_line(text: str) -> None:
        style = state["style"]
        if state["need_prefix"] and style == render.CONVERSATION_BG:
            # The `Decode ` label (Fix 2) leads the first answer line of the turn.
            console.print(Text(_ASSISTANT_PREFIX + text, style=style))
            state["need_prefix"] = False
        else:
            console.print(Text(text, style=style))

    def _flush() -> None:
        if state["buffer"]:
            _emit_line(state["buffer"])
            state["buffer"] = ""

    def _stream(text: str, style: str) -> None:
        if state["buffer"] and state["style"] != style:
            _flush()  # a different stream kind (thinking vs answer) ended — close its line
        state["style"] = style
        state["buffer"] += text
        while "\n" in state["buffer"]:
            line, state["buffer"] = state["buffer"].split("\n", 1)
            _emit_line(line)

    def on_event(event: events.Event) -> None:
        if isinstance(event, events.AssistantTextDelta):
            _stream(event.text, render.CONVERSATION_BG)
            return
        if isinstance(event, events.ThinkingDelta):
            _stream(event.text, "dim italic")
            return
        # Non-streamed event (the echoed user line, tool panels, errors, ``[done]``): flush the
        # buffered partial line first (carrying its prefix), arm a new turn's prefix on TurnStarted,
        # then render the event on its own line.
        _flush()
        if isinstance(event, events.TurnStarted):
            state["need_prefix"] = True
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
        completer=SlashCompleter(deps.cwd),
        complete_while_typing=True,
        # Re-render the footer on a timer so the "working…" spinner animates while a turn runs in
        # the background (without this the footer only repaints on keystrokes).
        refresh_interval=_FOOTER_REFRESH_S,
        # ``handler`` / ``runner`` are bound a few lines below; the toolbar lambda is invoked only
        # during the prompt loop (after they exist), so the late-bound reference is safe and lets the
        # footer read the live ``handler.last_input_tokens`` and ``runner.phase`` each render.
        bottom_toolbar=lambda: _bottom_toolbar(deps, gate, handler, runner, decisions),
    )
    # Persistence (ADR-0002 §9): replay a prior session if asked, then open a fresh append-only
    # JSONL log this run writes its turns to. The replayed history seeds the handler so the
    # conversation continues; the new log starts after the replayed prefix (already-persisted).
    resumed_history = _load_resume_history(resume, console)
    session_log = SessionLog.create(settings.sessions_dir, cwd=deps.cwd)

    # Hold the handler directly: it owns the cross-turn ``message_history`` the on-exit memory
    # write-back summarizes (the runner keeps it private). One handler per session (§1). Wiring
    # ``compaction_model_or_settings=settings`` arms the window-relative two-tier compaction
    # cascade (ADR-0006 §3-7): the summarizer is built from the same Settings as the main model.
    handler = AgentTurnHandler(
        agent,
        deps=deps,
        session_log=session_log,
        message_history=resumed_history,
        compaction_model_or_settings=settings,
    )
    runner = Runner(handler, on_event=_on_event)

    # Eager sandbox warm-up (ADR-0011 §4): bring the docker/modal sandbox up NOW — visibly —
    # instead of invisibly mid-first-turn (the container shows in ``docker ps`` from launch and the
    # first bash skips the start latency). The progress line prints BEFORE the await because a cold
    # image pull is slow — a silent hang here would be the exact confusion this fixes. A failure
    # degrades to the lazy path (the memo is kept, so the first ``bash`` retries and its rendered
    # infra-failure result carries any persistent problem to the model); the config-level failures
    # were already caught by the CLI preflight. ``none`` skips the whole block — byte-identical.
    if settings.sandbox_mode != "none":
        emit_line(f"Decode - starting {settings.sandbox_mode} sandbox ({settings.sandbox_image})…")
        try:
            await warm_executor(deps.cwd)
        except Exception as exc:
            logger.warning("sandbox warm-up failed; degrading to lazy start", exc_info=True)
            emit_line(
                f"Decode: sandbox startup failed ({exc}); will retry on the first bash command."
            )

    # Which provider/model this session is talking to (the active model id lives in a per-provider
    # settings field — same mapping as factory._build_model's branches).
    active_model = {
        "gemini": settings.gemini_model,
        "openrouter": settings.openrouter_model,
        "modal": settings.modal_endpoint_model,
    }[settings.llm_provider]
    console.print(
        render.render_event(
            events.AssistantTextDelta(
                text=startup_banner(settings.llm_provider, active_model, settings.sandbox_mode)
            )
        )
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

            # The manual full-compaction command (ADR-0006 §7), reserved among the slash commands
            # (before the skill branch) so a ``compact`` skill can never shadow it: forces a full
            # compaction now when idle, or reports busy mid-turn — never opening a second prompt.
            if is_compact_command(text):
                await _handle_compact_command(handler, runner, emit=emit_line)
                continue

            # The conversation-wipe command, reserved like ``/compact`` (before the skill branch)
            # so a ``clear`` skill can never shadow it: summarize-then-wipe when idle, a busy line
            # mid-turn — never opening a second prompt.
            if is_clear_command(text):
                await _handle_clear_command(handler, runner, cwd=deps.cwd, emit=emit_line)
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

    # On-exit LSP teardown (ADR-0007 §6): shut down every Language Server spawned this session so no
    # ``ty server`` child orphans. A cheap no-op when none was spawned (lazy — the common case) and
    # idempotent. Best-effort like the memory write-back: any failure is logged and swallowed so it
    # can never block exit or mask the ``Decode - bye.`` line.
    try:
        await shutdown_lsp_servers()
    except Exception:
        logger.warning("lsp shutdown on exit failed; continuing shutdown", exc_info=True)

    # On-exit sandbox teardown (ADR-0011 §4): reap the session's Docker container / Modal sandbox if
    # ``SANDBOX_MODE`` selected one this session. A cheap no-op in ``none`` mode (``LocalExecutor`` has
    # no teardown) and when no ``bash`` ran. Best-effort like the LSP + memory steps above: any failure
    # is logged and swallowed so it can never block exit or mask the ``Decode - bye.`` line.
    try:
        await close_executor()
    except Exception:
        logger.warning("sandbox teardown on exit failed; continuing shutdown", exc_info=True)

    console.print(render.render_event(events.AssistantTextDelta(text="Decode - bye.")))
