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
:func:`parse_permission_answer`) are pure and unit-tested.
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
from decode.tui import render

logger = logging.getLogger(__name__)

# The flag value the bare ``--resume`` (no argument) carries: resume the latest session.
_RESUME_LATEST = "latest"

_QUIT_COMMAND = "/quit"
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


def footer_hint() -> str:
    """The bottom-toolbar hint listing the interaction keys (plain text).

    Kept pure and string-returning so it is unit-testable; :func:`_bottom_toolbar` wraps it
    for prompt_toolkit. Mentions steer (plain Enter), follow-up (Alt+Enter), abort (Esc),
    and how to quit.
    """
    return "Enter steer | Alt+Enter follow-up | Esc abort | Ctrl-D or /quit to exit"


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


def _bottom_toolbar() -> HTML:
    """The footer hint as prompt_toolkit formatted text."""
    return HTML(f"<b>{footer_hint()}</b>")


def _build_key_bindings() -> KeyBindings:
    """Register the follow-up (Alt+Enter) and abort (Esc) keybindings.

    Each accepts the prompt with an explicit ``(intent, text)`` result so the loop can route
    it. ``Alt+Enter`` arrives as the ``escape, enter`` sequence in prompt_toolkit.
    """
    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _follow_up(event: KeyPressEvent) -> None:
        event.app.exit(result=(InputIntent.FOLLOW_UP, event.app.current_buffer.text))

    @bindings.add("escape")
    def _abort(event: KeyPressEvent) -> None:
        event.app.exit(result=(InputIntent.ABORT, event.app.current_buffer.text))

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


async def run_app(console: Console | None = None, *, resume: str | None = None) -> None:
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

    The single input loop has two modes (see module docstring): when the
    :class:`~decode.harness.decisions.DecisionChannel` is *awaiting a decision*, the next line
    fulfils the pending mid-turn request; otherwise it routes to the runner normally.
    """
    console = console or Console()

    # The harness streams events into this sink; it renders append-style above the pinned prompt
    # and owns the once-per-turn ``Decode `` answer prefix (Fix 2 — the pure renderer can't).
    _on_event = _make_event_sink(console)

    session: PromptSession[object] = PromptSession(
        key_bindings=_build_key_bindings(),
        bottom_toolbar=_bottom_toolbar,
    )

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
