"""The blocking ``ask_user`` tool — the model asks the human a free-form question (ADR-0002 §2,7).

``ask_user`` is the **one blocking tool**: the model calls it with a ``question``, the TUI
surfaces that question, the human types a free-text answer, and that line becomes the tool's
return value (fed straight back to the model on its next leg). It is how the agent gets a
human decision it cannot make on its own — a missing requirement, a yes/no the user must make.

**Not gated (ADR-0002 §3).** Every *other* tool raises :class:`pydantic_ai.ApprovalRequired`
and is routed through the permission gate ("may I run this side effect?"). ``ask_user`` is the
human-interaction tool *itself* — gating it would ask the human "may I ask you something?"
before asking, double-prompting for no benefit. So it never raises ``ApprovalRequired`` and is
registered to **skip** the permission gate (see :mod:`decode.tools.registry`).

**One single input channel.** ``ask_user`` does **not** open its own prompt. It awaits the
human's answer through ``ctx.deps.resolve_user_question`` — the very same single mid-turn
decision channel the permission resolver uses (task 005's
:class:`~decode.harness.decisions.DecisionChannel`). Opening a second concurrent
``prompt_async()`` on the live session is illegal (prompt_toolkit guards a single running
``Application``) and would deadlock the REPL. The channel's single-flight invariant guarantees
a permission ask and an ``ask_user`` ask can never be pending at the same time, so they never
collide.

**Headless / no-TTY safety.** When no interactive user is attached (an unattended run, a closed
input), the resolver is the :func:`deny_user_question_resolver` default, which raises
:class:`NoInteractiveUserError`. ``ask_user`` maps that — and a cancelled request (turn aborted
/ REPL shutting down) — to a model-readable :class:`pydantic_ai.ModelRetry` so the model learns
"no human is here" and moves on, instead of the turn hanging forever on an answer that will
never come.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic_ai import ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.entities import events

logger = logging.getLogger(__name__)

ASK_USER_TOOL_NAME = "ask_user"

# The message fed back to the model when no human can answer (headless run / cancelled prompt).
_NO_INTERACTIVE_USER_MESSAGE = (
    "No interactive user is attached, so the question could not be answered. "
    "Proceed without asking the user, or make a reasonable assumption and state it."
)


class NoInteractiveUserError(RuntimeError):
    """Raised by the headless resolver: there is no interactive user to answer ``ask_user``.

    :func:`ask_user` catches it and re-raises a model-readable :class:`pydantic_ai.ModelRetry`
    so an unattended run never hangs waiting for an answer that will never arrive.
    """


async def deny_user_question_resolver(question: str) -> str:
    """The safe headless default: there is no interactive user, so refuse to answer.

    Used when there is no terminal to ask (an unattended / piped run). Raising (rather than
    returning a fake answer) is the honest default — the model is told plainly that no human is
    attached via the :class:`pydantic_ai.ModelRetry` :func:`ask_user` builds from this.
    """
    logger.debug("headless ask_user resolver refusing question=%r", question)
    raise NoInteractiveUserError(_NO_INTERACTIVE_USER_MESSAGE)


async def ask_user(ctx: RunContext[AgentDeps], question: str) -> str:
    """Ask the human ``question`` and return their typed answer (ADR-0002 §2,7).

    Emits an :class:`~decode.entities.events.AskUserRequested` event so the TUI renders the
    question, then awaits the human's free-text answer on ``ctx.deps.resolve_user_question`` —
    the single mid-turn decision channel (never a second prompt). The typed line is returned to
    the model as the tool result.

    Not gated: ``ask_user`` is the human-interaction tool itself, so it does **not** raise
    :class:`pydantic_ai.ApprovalRequired` and is registered to skip the permission gate.

    Raises a model-readable :class:`pydantic_ai.ModelRetry` (never hangs) when no interactive
    user is attached (:class:`NoInteractiveUserError`) or the pending request is cancelled
    (turn aborted / REPL shutting down), so the model can proceed without the answer.
    """
    ctx.deps.emit(events.AskUserRequested(tool_call_id=ctx.tool_call_id, question=question))
    logger.debug("ask_user awaiting an answer (question=%r)", question)
    try:
        answer = await ctx.deps.resolve_user_question(question)
    except NoInteractiveUserError as exc:
        logger.debug("ask_user has no interactive user (question=%r)", question)
        raise ModelRetry(str(exc)) from exc
    except asyncio.CancelledError as exc:
        logger.debug("ask_user request cancelled (question=%r)", question)
        raise ModelRetry(
            "The user dismissed the question without answering; proceed without it."
        ) from exc
    logger.debug("ask_user got an answer (%d chars)", len(answer))
    return answer
