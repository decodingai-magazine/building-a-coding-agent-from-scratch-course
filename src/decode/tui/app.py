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
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from decode.agent.deps import AgentDeps, PermissionResolver, UserQuestionResolver
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.decisions import DecisionChannel
from decode.harness.runner import Runner
from decode.permissions.gate import PermissionGate
from decode.tui import render

logger = logging.getLogger(__name__)

_QUIT_COMMAND = "/quit"
_PROMPT = "> "
# A minimal inline affordance shown when a decision is pending; the full request was already
# rendered once by the loop's PermissionRequested event (single render path — no re-print).
_PERMISSION_AFFORDANCE = "allow this tool call? [y/N]"
# The matching affordance for an ask_user question: the full question was already rendered once
# by the tool's AskUserRequested event, so this is only the "type your answer" cue (any line is
# the answer — no parsing, unlike the permission y/N).
_ASK_USER_AFFORDANCE = "type your answer:"
# Answers that count as "yes"; everything else denies (the safe default — ADR-0002 §3).
_AFFIRMATIVE_ANSWERS = frozenset({"y", "yes", "a", "allow"})


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
    """Map a typed allow/deny answer to a :class:`PermissionDecision` (ADR-0002 §3).

    ``y`` / ``yes`` / ``a`` / ``allow`` (case-insensitive) allow; **anything else denies** —
    the safe default. A denial carries a human-facing reason that is fed back to the model.
    Kept pure (string in, decision out) so it is unit-testable; the interactive prompt that
    reads the answer is not.
    """
    if answer.strip().lower() in _AFFIRMATIVE_ANSWERS:
        return PermissionDecision.allow()
    return PermissionDecision.deny(reason="The user denied this tool call.")


async def deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """The safe headless default: deny every tool call (ADR-0002 §3).

    Used when there is no interactive terminal to ask. Denying (rather than allowing) is the
    safe default — an unattended run never executes a gated side effect.
    """
    logger.debug("headless permission resolver denying tool=%s", request.tool_name)
    return PermissionDecision.deny(reason="No interactive terminal to approve this tool call.")


def _make_permission_resolver(channel: DecisionChannel, console: Console) -> PermissionResolver:
    """Build the interactive allow/deny resolver bound to the decision ``channel``.

    The full request was already rendered once by the loop's ``PermissionRequested`` event
    (single render path), so the resolver only shows a minimal inline ``allow/deny?``
    affordance and then **awaits the next submitted line on the channel** — it never opens a
    second ``prompt_async()`` on the live session (that would deadlock the REPL). The answer
    is parsed by :func:`parse_permission_answer`. If the request is cancelled (turn aborted /
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
        return parse_permission_answer(answer)

    return resolver


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


async def run_app(console: Console | None = None) -> None:
    """Run the REPL until ``Ctrl-D`` or ``/quit``, routing input into the harness.

    ``console`` is injectable so callers/tests can capture output; defaults to a real
    stdout-backed :class:`rich.console.Console`. The harness drives the real Pydantic AI
    loop behind the turn-handler seam; gated tool calls (task 005) pause the turn, surface a
    permission prompt via :func:`_make_permission_resolver`, and resume on the answer.

    The single input loop has two modes (see module docstring): when the
    :class:`~decode.harness.decisions.DecisionChannel` is *awaiting a decision*, the next line
    fulfils the pending mid-turn request; otherwise it routes to the runner normally.
    """
    console = console or Console()

    def _on_event(event: events.Event) -> None:
        # The harness streams events here; render append-style above the pinned prompt.
        console.print(render.render_event(event))

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
    deps = AgentDeps(
        cwd=Path.cwd(),
        emit=_on_event,
        gate=PermissionGate(),
        resolve_permission=_make_permission_resolver(decisions, console),
        resolve_user_question=_make_user_question_resolver(decisions, console),
    )
    runner = Runner(AgentTurnHandler(agent, deps=deps), on_event=_on_event)

    console.print(
        render.render_event(events.AssistantTextDelta(text="decode - type a line; /quit exits."))
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
    console.print(render.render_event(events.AssistantTextDelta(text="decode - bye.")))
