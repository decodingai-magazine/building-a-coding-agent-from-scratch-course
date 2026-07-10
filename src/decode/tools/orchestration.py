"""The orchestration control tools — ``enter_plan_mode`` / ``exit_plan_mode`` (ADR-0003 §8).

Ungated session controls that flip the gate's mode: ``enter_plan_mode`` switches to ``PLAN``;
``exit_plan_mode`` is the plan-approval HITL — it asks the human via the same single Decision
Channel ``ask_user`` uses, switching to ``EDIT`` on approve and staying in ``PLAN`` on deny
(headless / cancelled approvals map to a :class:`pydantic_ai.ModelRetry`, never a hang).
``sleep``'s body lives in :mod:`decode.tools.sleep`; its name constant stays here because this
module owns the orchestration tool-name constants the agents-catalog loader validates against.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic_ai import ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.entities import events
from decode.permissions.types import PermissionMode
from decode.tools.askuser import NoInteractiveUserError

logger = logging.getLogger(__name__)

ENTER_PLAN_MODE_TOOL_NAME = "enter_plan_mode"
EXIT_PLAN_MODE_TOOL_NAME = "exit_plan_mode"
SLEEP_TOOL_NAME = "sleep"

# Unioned with the registry's tool names to form the allowlist-validation set.
ORCHESTRATION_TOOL_NAMES: frozenset[str] = frozenset(
    {ENTER_PLAN_MODE_TOOL_NAME, EXIT_PLAN_MODE_TOOL_NAME, SLEEP_TOOL_NAME}
)

# Acknowledgements + approval cue, as named constants so tests pin the exact contract.
_ENTERED_PLAN_MESSAGE = "Entered plan mode: read-only. Present your plan, then call exit_plan_mode."
_PLAN_APPROVED_MESSAGE = "Plan approved — entering edit mode."
_PLAN_DENIED_MESSAGE = "Plan not approved — refine it and call exit_plan_mode again."
_APPROVAL_CUE = "Approve this plan and start editing? [y/N]"
# Model-readable fallbacks when the human cannot answer; the gate stays in PLAN in both cases.
_NO_INTERACTIVE_USER_MESSAGE = (
    "No interactive user is attached to approve the plan, so plan mode was not exited. "
    "Refine the plan or proceed read-only."
)
_CANCELLED_MESSAGE = (
    "The plan approval was dismissed without an answer, so plan mode was not exited. "
    "Call exit_plan_mode again when ready."
)
# Answers (case-insensitive, stripped) that approve; anything else denies (safe [y/N] default).
_APPROVE_ANSWERS: frozenset[str] = frozenset({"y", "yes"})


async def enter_plan_mode(ctx: RunContext[AgentDeps]) -> str:
    """Switch the session to plan mode and acknowledge (ADR-0003 §8).

    Sets the gate to :attr:`~decode.permissions.types.PermissionMode.PLAN` so read-only tools
    still auto-allow but any mutation (``write`` / ``edit`` / ``bash``) is denied with a reason
    pointing the model at ``exit_plan_mode``. Ungated: never raises
    :class:`pydantic_ai.ApprovalRequired`, so it never reaches the permission gate and stays
    callable in any mode. Returns a short confirmation the model sees on its next leg.
    """
    ctx.deps.gate.set_mode(PermissionMode.PLAN)
    logger.debug("enter_plan_mode: gate switched to PLAN")
    return _ENTERED_PLAN_MESSAGE


async def exit_plan_mode(ctx: RunContext[AgentDeps], plan: str) -> str:
    """Present ``plan`` and ask the human to approve leaving plan mode (ADR-0003 §8).

    Surfaces the plan plus an approve/deny cue through the **same single Decision Channel**
    ``ask_user`` uses (``ctx.deps.resolve_user_question`` — never a second prompt) and parses the
    typed line as ``y``/``N``. On **approve** it switches the gate to
    :attr:`~decode.permissions.types.PermissionMode.EDIT` (so the agent can implement the
    just-approved plan) and returns the approved message; on **deny** it leaves the gate in
    ``PLAN`` and tells the model to refine and call ``exit_plan_mode`` again.

    Ungated (it IS the HITL tool — routing it through the permission gate would double-prompt): it
    never raises :class:`pydantic_ai.ApprovalRequired`. A headless run
    (:class:`~decode.tools.askuser.NoInteractiveUserError`) or a cancelled approval
    (:class:`asyncio.CancelledError`, e.g. the turn was aborted) maps to a model-readable
    :class:`pydantic_ai.ModelRetry` — never a hang — and leaves the mode untouched (stays ``PLAN``).
    """
    question = f"{plan}\n\n{_APPROVAL_CUE}"
    ctx.deps.emit(events.AskUserRequested(tool_call_id=ctx.tool_call_id, question=question))
    logger.debug("exit_plan_mode awaiting plan approval (%d chars)", len(plan))
    try:
        answer = await ctx.deps.resolve_user_question(question)
    except NoInteractiveUserError as exc:
        logger.debug("exit_plan_mode has no interactive user to approve the plan")
        raise ModelRetry(_NO_INTERACTIVE_USER_MESSAGE) from exc
    except asyncio.CancelledError as exc:
        logger.debug("exit_plan_mode approval cancelled")
        raise ModelRetry(_CANCELLED_MESSAGE) from exc

    if answer.strip().lower() in _APPROVE_ANSWERS:
        ctx.deps.gate.set_mode(PermissionMode.EDIT)
        logger.debug("exit_plan_mode approved: gate switched to EDIT")
        return _PLAN_APPROVED_MESSAGE
    logger.debug("exit_plan_mode denied: staying in PLAN")
    return _PLAN_DENIED_MESSAGE
