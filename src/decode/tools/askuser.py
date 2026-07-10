"""The blocking ``ask_user`` tool — the model asks the human a free-form question.

The one blocking tool: the TUI surfaces the question and the human's typed line becomes the tool
result. Not gated — it IS the human-interaction tool, so gating it would double-prompt. It awaits
the answer on ``ctx.deps.resolve_user_question``, the single mid-turn decision channel (never a
second prompt); headless / cancelled requests map to a model-readable
:class:`pydantic_ai.ModelRetry` so the turn never hangs. See ADR-0002 §2,3,7.
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
