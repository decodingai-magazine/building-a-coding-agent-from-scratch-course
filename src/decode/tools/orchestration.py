"""The orchestration control tools — ``enter_plan_mode`` / ``exit_plan_mode`` (ADR-0003 §8).

These tools steer the *session* rather than touch the filesystem: they flip the gate's
:class:`~decode.permissions.types.PermissionMode`. They are **ungated** — like ``ask_user`` they
never raise :class:`pydantic_ai.ApprovalRequired`, so they never reach the permission gate (routing
a control signal through the gate would either block it in plan mode or double-prompt the human).

* :func:`enter_plan_mode` switches the gate to ``PLAN`` (read-only: any mutation is then denied
  with a reason pointing back here) and acknowledges.
* :func:`exit_plan_mode` is itself a **HITL tool**: it presents the plan and asks the human to
  approve leaving plan mode, *via the same single Decision Channel* ``ask_user`` uses
  (``deps.resolve_user_question`` — never a second prompt). On approve it switches to ``EDIT`` (so
  the agent can implement the just-approved plan); on deny it stays in ``PLAN`` and tells the model
  to refine and ask again. A headless run / a cancelled approval maps to a model-readable
  :class:`pydantic_ai.ModelRetry` (never a hang) and leaves the mode untouched.

The ``sleep`` control tool's body lives in :mod:`decode.tools.sleep`; its name constant stays here
(:data:`SLEEP_TOOL_NAME`) because this module is the one place the ``tools`` package owns the
orchestration tool-name constants the agents-catalog loader (task 019) validates ``tools``
allowlists against, regardless of task ordering.
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

# The orchestration tool names as a frozenset — the agents-catalog loader unions these with the
# registry's tool names to form the allowlist-validation set (the build/plan personas list these).
ORCHESTRATION_TOOL_NAMES: frozenset[str] = frozenset(
    {ENTER_PLAN_MODE_TOOL_NAME, EXIT_PLAN_MODE_TOOL_NAME, SLEEP_TOOL_NAME}
)

# The acknowledgements + approval cue the tools return / surface. Kept as named constants so the
# tests pin the exact contract and the strings live in one place.
_ENTERED_PLAN_MESSAGE = "Entered plan mode: read-only. Present your plan, then call exit_plan_mode."
_PLAN_APPROVED_MESSAGE = "Plan approved — entering edit mode."
_PLAN_DENIED_MESSAGE = "Plan not approved — refine it and call exit_plan_mode again."
_APPROVAL_CUE = "Approve this plan and start editing? [y/N]"
# The model-readable fallbacks when the human cannot answer the approval (headless / cancelled).
# The gate is left untouched in both cases (the session stays in plan mode).
_NO_INTERACTIVE_USER_MESSAGE = (
    "No interactive user is attached to approve the plan, so plan mode was not exited. "
    "Refine the plan or proceed read-only."
)
_CANCELLED_MESSAGE = (
    "The plan approval was dismissed without an answer, so plan mode was not exited. "
    "Call exit_plan_mode again when ready."
)
# Typed answers (case-insensitive, stripped) that approve leaving plan mode; anything else denies
# (the safe default behind the ``[y/N]`` cue).
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
