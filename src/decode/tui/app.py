"""The interactive REPL: a persistent input line + append-style Rich output.

A concurrent ``prompt_async()`` wrapped in ``patch_stdout()`` keeps the prompt pinned while
output scrolls above it. There is ONE input surface with two modes: normal (idle Enter starts
a turn; Enter while busy steers; Alt+Enter queues a follow-up; Esc aborts) and
awaiting-decision (a pending permission / ``ask_user`` request on the ``DecisionChannel``
consumes the next line). Opening a second ``prompt_async()`` on the live session would
deadlock the REPL, so every mid-turn HITL exchange rides this single surface. The pure
helpers (parsers, intents) are unit-tested; the loop itself is covered by the ``run_app``
regression test. See ADR-0002 §4-6, ADR-0003 §9.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from pydantic_ai.messages import ModelMessage
from rich.console import Console
from rich.text import Text

from decode import observability
from decode.agent.context_window import resolve_context_window
from decode.agent.deps import AgentDeps, PermissionResolver, UserQuestionResolver
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.agents.select import select_agent
from decode.config.settings import settings
from decode.context.compaction import CompactOutcome
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
from decode.tools.bash import close_executor, export_executor, warm_executor
from decode.tui import render

if TYPE_CHECKING:
    # Typing only: a runtime import would break the ``none`` path's sandbox laziness (ADR-0012 §9).
    from decode.sandbox.handback import ShipResult

logger = logging.getLogger(__name__)

# The flag value the bare ``--resume`` (no argument) carries: resume the latest session.
_RESUME_LATEST = "latest"

# The startup Agent persona when none is given (ADR-0003 §7,9).
_DEFAULT_AGENT = "build"

_QUIT_COMMAND = "/quit"
# Mid-session control slash commands (ADR-0003 §9), parsed on the single input surface.
_AGENT_COMMAND = "/agent"
_MODE_COMMAND = "/mode"
# Reserved slash commands (matched before ``parse_skill_command`` so a same-named project skill
# can never shadow them), all idle-only: manual full compaction (ADR-0006 §7), conversation wipe
# (summarize-to-memory then clear), and the git hand-back (ADR-0012 §8).
_COMPACT_COMMAND = "/compact"
_CLEAR_COMMAND = "/clear"
_SHIP_COMMAND = "/ship"
# The Shift+Tab mode cycle order (ADR-0003 §9); a tuple so :func:`next_mode` is a pure index step.
_MODE_CYCLE: tuple[PermissionMode, ...] = (
    PermissionMode.DEFAULT,
    PermissionMode.EDIT,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
)
_PROMPT = "> "
# Printed once before a turn's first streamed answer chunk — deltas stream, so the once-per-turn
# prefix lives in the event sink, not the pure renderer.
_ASSISTANT_PREFIX = "Decode "
# Minimal inline affordances: the full permission request / ask_user question was already rendered
# once by its event (single render path). ``a`` = allow AND persist a rule so the next identical
# call auto-allows.
_PERMISSION_AFFORDANCE = "allow this tool call? [y/N/a=always]"
_ASK_USER_AFFORDANCE = "type your answer:"
# Answers that mean "always": allow AND persist a matching allow rule (ADR-0003 §4).
_ALWAYS_ANSWERS = frozenset({"a", "always"})
# Anything outside this set denies — the safe default (ADR-0002 §3).
_AFFIRMATIVE_ANSWERS = frozenset({"y", "yes", "allow"}) | _ALWAYS_ANSWERS


class InputIntent(enum.Enum):
    """What the user signalled with the submitted line (ADR-0002 §4-5).

    Plain ``Enter`` → STEER (new turn when idle, steer when busy); ``Alt+Enter`` → FOLLOW_UP;
    ``Esc`` → ABORT.
    """

    STEER = "steer"
    FOLLOW_UP = "follow-up"
    ABORT = "abort"


def is_quit_command(line: str) -> bool:
    """True when ``line`` is the ``/quit`` command (ignoring surrounding whitespace)."""
    return line.strip() == _QUIT_COMMAND


def is_compact_command(line: str) -> bool:
    """True when ``line`` is the ``/compact`` command (ignoring surrounding whitespace)."""
    return line.strip() == _COMPACT_COMMAND


def is_clear_command(line: str) -> bool:
    """True when ``line`` is the ``/clear`` command (ignoring surrounding whitespace)."""
    return line.strip() == _CLEAR_COMMAND


def is_ship_command(line: str) -> bool:
    """True when ``line`` is the ``/ship`` command (ignoring surrounding whitespace)."""
    return line.strip() == _SHIP_COMMAND


def footer_hint(agent: str, mode: str, *, verbose: bool) -> str:
    """The bottom-toolbar hint: the live agent + mode + verbose state, then the interaction keys.

    Pure and string-returning; :func:`_bottom_toolbar` supplies the live ``agent`` / ``mode`` /
    ``verbose`` each render (ADR-0003 §9) so the footer updates after a switch. The footer is the ONLY
    place Ctrl+O is discoverable, so it names both the key and the current state.
    """
    return (
        f"agent:{agent} mode:{mode} verbose:{'on' if verbose else 'off'} | Enter steer | "
        "Alt+Enter follow-up | Esc abort | Shift+Tab mode | Ctrl+O verbose | "
        "/agent /mode /compact /clear /ship /quit"
    )


def startup_banner(provider: str, model: str, sandbox_mode: str) -> str:
    """The one-line startup banner: provider:model (+ the sandbox mode when one is active).

    ``none`` renders byte-identical to before sandboxing existed; the mode is fixed per session
    (ADR-0011 §1), so the banner — not the live footer — is its home.
    """
    if sandbox_mode == "none":
        return f"Decode - {provider}:{model} - type a line; /quit exits."
    return f"Decode - {provider}:{model} - sandbox:{sandbox_mode} - type a line; /quit exits."


def parse_agent_command(line: str) -> str | None:
    """Return the name argument of a ``/agent <name>`` line, or ``None`` if not that command.

    ``""`` means the command was typed with no name (the handler shows usage); ``None`` lets the
    main loop fall through to normal routing.
    """
    return _parse_slash_arg(line, _AGENT_COMMAND)


def parse_mode_command(line: str) -> str | None:
    """Return the mode argument of a ``/mode <name>`` line, or ``None`` if not that command."""
    return _parse_slash_arg(line, _MODE_COMMAND)


def _parse_slash_arg(line: str, command: str) -> str | None:
    """Split a ``<command> <arg>`` slash line: ``None`` = not this command, ``""`` = no argument."""
    stripped = line.strip()
    if stripped == command:
        return ""
    prefix = f"{command} "
    if stripped.startswith(prefix):
        return stripped[len(prefix) :].strip()
    return None


def parse_skill_command(line: str) -> tuple[str, str] | None:
    """Split a ``/<skill-name> [trailing]`` line into ``(name, trailing)``, or ``None`` (ADR-0004 §5).

    A non-slash line or a bare ``"/"`` → ``None`` (fall through to normal routing). Reserved slash
    commands parse here too, but the ``run_app`` loop matches them *before* the skill branch —
    precedence is the loop's job, not this parser's.
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
    """Autocomplete the slash commands + project skills as the user types ``/``.

    Built once per session from ``load_skills(cwd)``. Fires **only** while the line-before-cursor
    is a single ``/`` token (no space yet), so normal prose and command arguments never trigger it.
    """

    def __init__(self, cwd: Path) -> None:
        self._meta = {
            _AGENT_COMMAND: "switch the active agent (/agent <name>)",
            _MODE_COMMAND: "switch the permission mode (/mode <name>)",
            _COMPACT_COMMAND: "compact the conversation now",
            _CLEAR_COMMAND: "clear the conversation (summarizes to memory first)",
            _SHIP_COMMAND: "ship the sandbox workspace back as a git branch",
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

    Case-insensitive and whitespace-tolerant; ``None`` lets the caller render a friendly line.
    """
    try:
        return PermissionMode(name.strip().lower())
    except ValueError:
        return None


def next_mode(mode: PermissionMode) -> PermissionMode:
    """The next mode in the Shift+Tab ring: default → edit → plan → bypass → default (§9)."""
    index = _MODE_CYCLE.index(mode)
    return _MODE_CYCLE[(index + 1) % len(_MODE_CYCLE)]


def mode_switch_confirmation(mode: str) -> str:
    """The one-line confirmation rendered after a mode switch (Shift+Tab / ``/mode``; §9)."""
    return f"Decode - mode: {mode}."


def verbose_switch_confirmation(enabled: bool) -> str:
    """The one-line confirmation rendered after a Ctrl+O verbose toggle (ADR-0013 §8 amendment)."""
    if enabled:
        return "Decode - verbose: on (subagent activity shown)."
    return "Decode - verbose: off."


def agent_switch_confirmation(name: str, mode: str) -> str:
    """The one-line confirmation after an agent switch — names the agent and the reset mode (§7,9)."""
    return f"Decode - agent: {name} (mode: {mode})."


# The inline usage lines shown when ``/agent`` / ``/mode`` are typed with no argument.
_AGENT_USAGE = "Decode - usage: /agent <name> (build / plan / code-reviewer)."
_MODE_USAGE = "Decode - usage: /mode <name> (default / plan / edit / bypass)."


def _handle_agent_command(
    name: str,
    *,
    deps: AgentDeps,
    gate: PermissionGate,
    emit: Callable[[str], None],
) -> None:
    """Apply a ``/agent <name>`` switch: select the agent, render one confirmation (§7,9).

    Empty ``name`` → usage line; unknown name → friendly inline error (``select_agent`` leaves
    ``deps`` / ``gate`` untouched on failure) — never a crash.
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
    """Apply a ``/mode <name>`` switch: set the gate mode, render one confirmation (§3,9)."""
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


# Inline lines for ``/compact`` (the COMPACTED path renders nothing — the ContextCompacted event
# is the feedback). One distinct line per Compaction Outcome (ADR-0018 §3).
_COMPACT_NOTHING = "Decode - nothing to compact yet."
_COMPACT_SUMMARIZER_FAILED = "Decode - compaction summarizer failed; see .decode/logs/decode.log."
_COMPACT_BUSY = "Decode - busy; try /compact again once the turn finishes."


async def _handle_compact_command(
    handler: AgentTurnHandler,
    runner: Runner,
    *,
    emit: Callable[[str], None],
) -> None:
    """Force a full compaction now — the manual ``/compact`` command (ADR-0006 §7, ADR-0018 §3).

    Idle-only (the handler owns the live ``message_history`` — never compact mid-turn). An explicit
    request compacts regardless of the thresholds or ``compaction_enabled``. Each
    :class:`CompactOutcome` gets a distinct line: ``COMPACTED`` renders nothing (its
    ``ContextCompacted`` event is the feedback); ``NOTHING_TO_COMPACT`` the friendly line;
    ``SUMMARIZER_FAILED`` a line naming ``.decode/logs/decode.log`` so the user can act.
    """
    if runner.phase is not Phase.IDLE:
        emit(_COMPACT_BUSY)
        return
    outcome = await handler.compact()
    if outcome is CompactOutcome.NOTHING_TO_COMPACT:
        emit(_COMPACT_NOTHING)
    elif outcome is CompactOutcome.SUMMARIZER_FAILED:
        emit(_COMPACT_SUMMARIZER_FAILED)


# Inline lines for ``/clear`` (unlike ``/compact`` there is no event to render, so it confirms).
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

    Idle-only, like ``/compact``. **Summarize, then wipe**: the pre-clear history feeds the same
    non-fatal MEMORY.md write-back the quit path runs, then ``handler.clear()`` resets and rides a
    ``clear`` marker into the session log so ``--resume`` replays to the post-clear state.
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


# Inline lines for ``/ship``; the success/skip/failure text comes from the ShipResult's own message.
_SHIP_BUSY = "Decode - busy; try /ship again once the turn finishes."
_SHIP_NO_WORKSPACE = "Decode - no sandbox workspace to ship."


def _ship_outcome_line(result: ShipResult) -> str:
    """Prefix a ShipResult's own friendly message with ``Decode - `` (ADR-0012 §8).

    Duck-typed on ``.message`` so no runtime sandbox import is needed on the ``none`` path (§9).
    """
    return f"Decode - {result.message}"


async def _handle_ship_command(
    runner: Runner,
    *,
    harness_home: Path,
    repo: str | None,
    session_id: str,
    emit: Callable[[str], None],
) -> None:
    """Ship the sandbox Workspace back as a ``decode/<session-id>`` branch — the ``/ship`` command (§8).

    Idle-only; a friendly no-op in ``none`` mode / no-repo (no export, no sandbox import).
    Otherwise: export the executor FIRST (the modal sweep so ``/workspace`` is host-visible for
    the host-side git; a docker no-op), then run the host-side ``ship_workspace`` and print the
    outcome. The hand-back import stays lazy so the ``none`` path pulls in no sandbox module (§9).
    """
    if runner.phase is not Phase.IDLE:
        emit(_SHIP_BUSY)
        return
    if settings.sandbox_mode == "none" or repo is None:
        emit(_SHIP_NO_WORKSPACE)
        return
    # Modal sweep first so the host-side git below sees the final /workspace state (ADR-0012 §5,8).
    await export_executor()
    # Lazy import: the ``none`` path never touches ``decode.sandbox`` (§9).
    from decode.sandbox.handback import ship_workspace

    result = ship_workspace(harness_home, repo=repo, session_id=session_id)
    emit(_ship_outcome_line(result))


def _ship_on_exit(
    harness_home: Path,
    *,
    repo: str | None,
    session_id: str,
    emit: Callable[[str], None],
) -> None:
    """Ship the Workspace back on REPL exit — best-effort, non-fatal, silent no-op otherwise (§8).

    Runs after ``close_executor`` (which already ran the modal export sweep), so no export here.
    Silent no-op in ``none`` mode / no-repo (returning before the import keeps the ``none`` path
    sandbox-free — §9) and on a skip (``branch=None``). Any error is logged, never raised, so it
    can never block exit or mask the ``Decode - bye.`` line.
    """
    if settings.sandbox_mode == "none" or repo is None:
        return
    from decode.sandbox.handback import ship_workspace

    try:
        result = ship_workspace(harness_home, repo=repo, session_id=session_id)
    except Exception:
        logger.warning("sandbox hand-back on exit failed; continuing shutdown", exc_info=True)
        return
    if result.branch is not None:
        emit(_ship_outcome_line(result))


# Friendly inline line for an unrecognised ``/<skill-name>`` (ADR-0004 §5); the available-skills
# list doubles as discovery.
_SKILL_NO_MATCH = "Decode - unknown command '/{name}'; available skills: {skills}."


def _handle_skill_command(
    name: str, trailing: str, *, cwd: Path, emit: Callable[[str], None]
) -> str | None:
    """Resolve a ``/<name>`` skill into the turn input, or ``emit`` a discovery line (ADR-0004 §5).

    Resolves through the SAME ``load_skills`` + ``format_skill_payload`` the model's ``skill``
    dispatcher uses, so both entry points inject an identical payload. A non-empty ``trailing`` is
    appended after a blank line; no match → a discovery line and ``None`` (no turn runs). ``name``
    is used ONLY as a dict key — never interpolated into a filesystem path or shell command
    (ADR-0004 §3,7).
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

    ``y``/``yes``/``allow`` and ``a``/``always`` (case-insensitive) allow; **anything else
    denies** — the safe default. A denial carries a human-facing reason fed back to the model.
    """
    if answer.strip().lower() in _AFFIRMATIVE_ANSWERS:
        return PermissionDecision.allow()
    return PermissionDecision.deny(reason="The user denied this tool call.")


def is_always_answer(answer: str) -> bool:
    """Whether ``answer`` is the "always" allow — allow AND persist a matching rule (§4)."""
    return answer.strip().lower() in _ALWAYS_ANSWERS


async def deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """The safe headless default: deny every tool call when no terminal can ask (ADR-0002 §3)."""
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

    Shows only a minimal affordance (the full request was already rendered by the
    ``PermissionRequested`` event) and awaits the next submitted line on the channel — never a
    second ``prompt_async()`` (that would deadlock the REPL). ``a``/``always`` also persists an
    allow rule and reloads the gate (a write failure degrades to allow-once). A cancelled
    request denies — the safe default.
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

    A write failure is non-fatal: logged, and the turn proceeds as a plain allow-once.
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

    Shows only the "type your answer" affordance and awaits the next line on the SAME channel the
    permission resolver uses — never a second ``prompt_async()``. No parsing: the raw line IS the
    answer. A cancelled request propagates ``asyncio.CancelledError``, which the ``ask_user`` tool
    maps to a model-readable ``ModelRetry`` so the turn winds down instead of hanging.
    """

    async def resolver(question: str) -> str:
        console.print(render.render_event(events.AssistantTextDelta(text=_ASK_USER_AFFORDANCE)))
        return await channel.request()

    return resolver


# Footer re-render cadence (PromptSession ``refresh_interval``) — animates the "working…" spinner
# (the footer otherwise only repaints on keystrokes); also the spinner's tick unit.
_FOOTER_REFRESH_S = 0.1


def _bottom_toolbar(
    deps: AgentDeps,
    gate: PermissionGate,
    handler: AgentTurnHandler,
    runner: Runner,
    decisions: DecisionChannel,
) -> HTML:
    """The footer as prompt_toolkit formatted text: a busy spinner + context fill gauge + live hint.

    Called on every render with live state, so the footer updates immediately after an agent/mode
    switch. The spinner shows only while the turn is ACTIVELY working — hidden when idle and when
    the turn is paused on the DecisionChannel awaiting a human (else it would read "working…"
    while the user is the one being asked to type). The gauge (ADR-0006 §9) reads the handler's
    public ``last_input_tokens`` over the run's resolved window (``deps.context_window_tokens``,
    task 123 — falling back to the configured one when nothing resolved it), so the gauge and the
    compaction trigger can never disagree about the denominator; ``warn_at`` / ``danger_at`` are the
    fill lines derived from the same reserve fractions the compaction cascade fires on.
    """
    window = deps.context_window_tokens or settings.compaction_context_window_tokens
    fraction = handler.last_input_tokens / window if window > 0 else 0.0
    warn_at = 1 - settings.microcompaction_reserve_fraction
    danger_at = 1 - settings.compaction_reserve_fraction
    label, color = render.context_gauge(fraction, warn_at=warn_at, danger_at=danger_at)
    hint = footer_hint(deps.active_agent.name, gate.mode.value, verbose=deps.verbose.enabled)
    if runner.phase is Phase.IDLE or decisions.pending:
        spinner = ""
    else:
        frame = render.spinner_frame(int(time.monotonic() / _FOOTER_REFRESH_S))
        spinner = f'<style fg="cyan">{frame} working…</style> '
    return HTML(f'{spinner}<style fg="{color}">{label}</style> <b>{hint}</b>')


def _build_key_bindings(
    *, on_cycle_mode: Callable[[], None], on_toggle_verbose: Callable[[], None]
) -> KeyBindings:
    """Register follow-up (Alt+Enter), abort (Esc), mode-cycle (Shift+Tab), verbose (Ctrl+O).

    Alt+Enter / Esc accept the prompt with an explicit ``(intent, text)`` result. Shift+Tab and
    Ctrl+O do NOT submit: they mutate live state (the gate mode / the verbose flag) and invalidate
    the app so the toolbar redraws, leaving the typed buffer intact — a half-typed prompt survives
    a toggle (ADR-0003 §9; ADR-0013 §8 amendment).
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

    @bindings.add("c-o")
    def _toggle_verbose(event: KeyPressEvent) -> None:
        on_toggle_verbose()
        event.app.invalidate()  # redraw the bottom toolbar with the new verbose state

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
    """Replay the requested session into a seed ``message_history`` (ADR-0002 §9).

    ``None`` → fresh; ``"latest"`` → the most recent session; else the session matching that id.
    Nothing to resume → one friendly line and a fresh conversation — never a crash.
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

    Owns the one piece of state the pure renderer cannot: the once-per-turn ``Decode `` prefix,
    printed before a turn's first answer delta and re-armed on each ``TurnStarted``.
    """
    # Streamed deltas are LINE-BUFFERED: ``patch_stdout()`` redraws output above the live prompt
    # and corrupts partial-line writes, so we accumulate and print only COMPLETE lines (split on
    # the model's own ``\n``), flushing the partial tail when the turn ends or any non-streamed
    # event interrupts.
    state = {"need_prefix": False, "buffer": "", "style": render.CONVERSATION_BG}

    def _emit_line(text: str) -> None:
        style = state["style"]
        if state["need_prefix"] and style == render.CONVERSATION_BG:
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
        # Non-streamed event: flush the buffered partial line, arm a new turn's prefix on
        # TurnStarted, then render the event on its own line.
        _flush()
        if isinstance(event, events.TurnStarted):
            state["need_prefix"] = True
        console.print(render.render_event(event))

    return on_event


def _apply_startup_mode(mode: str | None, gate: PermissionGate) -> None:
    """Override the gate mode from the optional ``--mode`` startup flag (ADR-0003 §9).

    ``None`` keeps the agent's default; an unknown value (the CLI validates first) is logged
    and ignored rather than crashing the launch.
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
    repo: str | None = None,
    local: bool = False,
) -> None:
    """Run the REPL until ``Ctrl-D`` or ``/quit``, routing input into the harness.

    ``console`` is injectable so tests can capture output. ``resume`` replays a prior session
    (``"latest"`` or an id) to seed the handler; every run opens a NEW append-only session log
    (ADR-0002 §9). ``agent`` is the startup persona and ``mode`` the optional permission-mode
    override, applied AFTER ``select_agent`` so ``--mode`` wins over the agent default
    (ADR-0003 §7,9). ``repo`` / ``local`` drive the sandbox Workspace clone-at-launch
    (ADR-0012 §3); the CLI guards ``--repo`` in ``none`` mode, so the plain REPL stays
    byte-identical. All control surfaces (slash commands, Shift+Tab, mid-turn decisions) ride
    the ONE input surface (see module docstring).
    """
    console = console or Console()

    # Opik tracing (ADR-0014 §4-5): configure ONCE, before the agent is built, so the global
    # ``instrument_pydantic_ai`` covers every downstream Agent. No key → silent no-op, byte-identical.
    # The one startup console line is emitted near the banner below, where ``emit_line`` exists.
    opik_tracing_active = observability.init_tracing()

    # ``agent`` is rebound below to the built Pydantic AI Agent, so save the persona name first
    # (the persona rides ``deps.active_agent``, not the Agent object — ADR-0003 §7).
    agent_name = agent

    # The harness streams events into this sink (append-style; owns the once-per-turn prefix).
    _on_event = _make_event_sink(console)

    # Both resolvers await on the SAME single-flight decision channel — the one mid-turn HITL
    # surface — so a permission ask and an ask_user question can never collide.
    decisions = DecisionChannel()
    agent = build_agent()

    # Harness Home is the launch cwd (ADR-0012 §6): every harness artifact — permission file,
    # session log, MEMORY.md, skills — anchors here even when the tool scope moves into a Workspace.
    harness_home = Path.cwd()
    permissions_file = harness_home / settings.permissions_file

    # A missing/malformed rules file is non-fatal (empty rules → mode-only). ADR-0003 §4.
    gate = PermissionGate(user_rules=rules.load_rule_set(permissions_file))

    # In a sandbox mode ``deps.cwd`` (the whole tool scope) becomes the isolated Workspace
    # (ADR-0012 §3,6). Only the PATH is resolved here; the clone + eager warm-up run in the sandbox
    # block below, where ``emit_line`` exists to show progress (the path is stable, so cloning after
    # ``deps`` is built is fine). ``none`` keeps ``cwd == harness_home``; the import stays lazy (§9).
    tool_scope = harness_home
    if settings.sandbox_mode != "none":
        from decode.sandbox.workspace import workspace_dir

        tool_scope = workspace_dir(harness_home)

    deps = AgentDeps(
        cwd=tool_scope,
        harness_home=harness_home,
        emit=_on_event,
        gate=gate,
        resolve_permission=_make_permission_resolver(
            decisions, console, gate=gate, permissions_file=permissions_file
        ),
        resolve_user_question=_make_user_question_resolver(decisions, console),
        # The REPL has no ``--model`` flag, so this is the configured active model; the probe it may
        # run was already paid for (and memoised) by the cli's startup notice.
        context_window_tokens=resolve_context_window(),
    )
    # Startup persona (ADR-0003 §7,9): sets ``deps.active_agent``, resets the gate to the agent's
    # default mode, loads its catalog rules. The CLI validated the name already.
    select_agent(agent_name, deps=deps, gate=gate)
    # Applied AFTER selection so an explicit ``--mode`` wins over the agent default (ADR-0003 §9).
    _apply_startup_mode(mode, gate)

    # One confirmation sink through the existing event/render path (no second render surface),
    # plus the Shift+Tab mode-cycle closure the keybind calls.
    def emit_line(text: str) -> None:
        console.print(render.render_event(events.AssistantTextDelta(text=text)))

    def cycle_mode() -> None:
        new_mode = next_mode(gate.mode)
        gate.set_mode(new_mode)
        emit_line(mode_switch_confirmation(new_mode.value))

    def toggle_verbose() -> None:
        # Mutates the flag ON ``deps`` (never a captured copy), so a fan-out already in flight sees
        # the new state on its very next child event (ADR-0013 §8 amendment).
        emit_line(verbose_switch_confirmation(deps.verbose.toggle()))

    session: PromptSession[object] = PromptSession(
        key_bindings=_build_key_bindings(
            on_cycle_mode=cycle_mode, on_toggle_verbose=toggle_verbose
        ),
        # Skills are a harness artifact → complete from Harness Home, not the Workspace (§6).
        completer=SlashCompleter(harness_home),
        complete_while_typing=True,
        # Re-render the footer on a timer so the spinner animates while a turn runs.
        refresh_interval=_FOOTER_REFRESH_S,
        # ``handler`` / ``runner`` are bound a few lines below; the lambda runs only during the
        # prompt loop (after they exist), so the late-bound reference is safe.
        bottom_toolbar=lambda: _bottom_toolbar(deps, gate, handler, runner, decisions),
    )
    # Persistence (ADR-0002 §9): replay a prior session if asked, then open a fresh append-only log.
    resumed_history = _load_resume_history(resume, console)
    # The session log's header records Harness Home, not the Workspace ``deps.cwd`` (ADR-0012 §6).
    session_log = SessionLog.create(settings.sessions_dir, cwd=harness_home)

    # The handler owns the cross-turn ``message_history`` the on-exit write-back summarizes; one
    # per session (§1). ``compaction_model=agent.model`` arms the two-tier compaction cascade
    # (ADR-0006 §3-7) on the ACTIVE provider's own built model, so the summarizer rides the Provider
    # Seam and works on gemini/openrouter/modal alike — zero extra construction, guaranteed same
    # provider (ADR-0018 §5).
    handler = AgentTurnHandler(
        agent,
        deps=deps,
        session_log=session_log,
        # Also the Opik Thread id grouping the session's per-turn traces (ADR-0014 §4).
        session_id=session_log.session_id,
        message_history=resumed_history,
        compaction_model=agent.model,
    )
    runner = Runner(handler, on_event=_on_event)

    # Eager sandbox block (ADR-0011 §4; ADR-0012 §3): clone + warm up NOW, visibly, instead of
    # invisibly mid-first-turn. Every progress line prints BEFORE its (slow) await — a cold image
    # pull / large clone would otherwise hang silently. ``none`` skips the block — byte-identical.
    if settings.sandbox_mode != "none":
        from decode.sandbox.workspace import prepare_workspace_or_empty

        # (1) Clone host-side; the progress line prints only when a clone will actually happen (a
        # non-empty Workspace is reused, never re-cloned). Failure degrades to an empty Workspace.
        if repo is not None and not any(deps.cwd.iterdir()):
            emit_line(f"Decode - cloning {repo} into the workspace…")
        _workspace, clone_error = prepare_workspace_or_empty(harness_home, repo=repo, local=local)
        if clone_error is not None:
            emit_line(
                f"Decode: could not clone {repo} ({clone_error}); starting with an empty workspace."
            )

        # (2) Warm the executor against ``deps.cwd`` passed VERBATIM — re-deriving it would
        # double-nest ``.decode/sandbox``. Modal also uploads the Workspace here (own progress
        # line). A warm-up failure degrades to the lazy path (the first op retries).
        emit_line(f"Decode - starting {settings.sandbox_mode} sandbox ({settings.sandbox_image})…")
        if settings.sandbox_mode == "modal":
            emit_line("Decode - uploading the workspace to the modal sandbox…")
        try:
            await warm_executor(deps.cwd)
        except Exception as exc:
            logger.warning("sandbox warm-up failed; degrading to lazy start", exc_info=True)
            emit_line(
                f"Decode: sandbox startup failed ({exc}); will retry on the first bash command."
            )

    # One tracing line near the banner when Opik is active (ADR-0014 §1,4); no-op when off.
    if opik_tracing_active:
        emit_line(f"Decode - Opik tracing on (project '{settings.opik_project_name}').")

    # The active model id per provider (same mapping as factory._build_model's branches).
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

            # Awaiting-decision mode: the next line answers the pending mid-turn request
            # (permission y/N or ask_user free text — the awaiting resolver does any parsing).
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

            # Control slash commands (ADR-0003 §9), consumed here — never a second prompt.
            agent_arg = parse_agent_command(text)
            if agent_arg is not None:
                _handle_agent_command(agent_arg, deps=deps, gate=gate, emit=emit_line)
                continue

            mode_arg = parse_mode_command(text)
            if mode_arg is not None:
                _handle_mode_command(mode_arg, gate=gate, emit=emit_line)
                continue

            # Reserved before the skill branch so a ``compact`` skill can never shadow it.
            if is_compact_command(text):
                await _handle_compact_command(handler, runner, emit=emit_line)
                continue

            # Reserved like ``/compact``: summarize-then-wipe when idle, a busy line mid-turn.
            if is_clear_command(text):
                # ``/clear``'s summarize-to-MEMORY.md write-back is a harness artifact → Harness Home.
                await _handle_clear_command(handler, runner, cwd=harness_home, emit=emit_line)
                continue

            # Reserved like ``/compact`` / ``/clear``; ``session_log.session_id`` names the branch.
            if is_ship_command(text):
                await _handle_ship_command(
                    runner,
                    harness_home=harness_home,
                    repo=repo,
                    session_id=session_log.session_id,
                    emit=emit_line,
                )
                continue

            # The user-facing skill entry point (ADR-0004 §5), parsed AFTER the reserved commands
            # so a same-named built-in always wins; an unknown ``/<x>`` gets the discovery line.
            skill_cmd = parse_skill_command(text)
            if skill_cmd is not None:
                name, trailing = skill_cmd
                # Skills are a harness artifact → resolve from Harness Home, not the Workspace (§6).
                turn_input = _handle_skill_command(name, trailing, cwd=harness_home, emit=emit_line)
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

    # On-exit memory write-back (ADR-0002 §8): summarize the session into MEMORY.md — a harness
    # artifact, so Harness Home, not ``deps.cwd`` (ADR-0012 §6). Non-fatal: never blocks exit.
    await extract_on_exit(handler.message_history, harness_home)

    # On-exit LSP teardown (ADR-0007 §6): best-effort + idempotent; never blocks exit or masks
    # the ``Decode - bye.`` line.
    try:
        await shutdown_lsp_servers()
    except Exception:
        logger.warning("lsp shutdown on exit failed; continuing shutdown", exc_info=True)

    # On-exit sandbox teardown (ADR-0011 §4): a no-op in ``none`` mode; best-effort like the rest.
    try:
        await close_executor()
    except Exception:
        logger.warning("sandbox teardown on exit failed; continuing shutdown", exc_info=True)

    # On-exit git hand-back (ADR-0012 §8): runs after ``close_executor`` (the modal export sweep);
    # best-effort, silent no-op in ``none`` / no-repo / unchanged.
    _ship_on_exit(harness_home, repo=repo, session_id=session_log.session_id, emit=emit_line)

    console.print(render.render_event(events.AssistantTextDelta(text="Decode - bye.")))
