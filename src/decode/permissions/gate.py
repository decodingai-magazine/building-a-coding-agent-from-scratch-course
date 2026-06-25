"""The permission gate: the allow/ask/deny **policy** object (ADR-0003 §1,3,4).

:class:`PermissionGate` answers one question — given a
:class:`~decode.entities.permissions.PermissionRequest`, should the tool run? In Milestone 2 the
gate is a **real decision**: :meth:`PermissionGate.check` evaluates, in precedence order,
**deny rule → allow rule → mode decision → ask** and returns ALLOW / ASK / DENY (ADR-0003 §4). The
mode is mutable via :meth:`set_mode`; the user rule set (loaded from ``.decode/settings.json``) is
held on the gate and reloadable via :meth:`set_user_rules` (the always-allow flow persists a rule
then reloads so the next identical call auto-allows).

Two rule **sources** drive the gate: the **user** ``.decode/settings.json`` rules (loaded at
startup, reloadable via :meth:`set_user_rules`) and the **active agent**'s catalog rules (loaded
when an agent is selected, replaceable via :meth:`set_agent_rules`). The gate evaluates them as a
**union** — a deny from *either* source beats an allow from *either* — by walking every source's
deny list before any allow list. This is what makes the code-reviewer's catalog ``allow:
["bash(git *)"]`` auto-allow ``git diff`` while a user ``deny`` can still tighten an agent allow.

The gate is **policy only** — it does *not* prompt the user or own the terminal UI. On an ``ASK``
the loop turns the verdict into the human's terminal allow/deny via the resolver on
:class:`~decode.agent.deps.AgentDeps`; an ``ALLOW`` / ``DENY`` the gate decides directly runs (or
refuses) the tool with no prompt.
"""

from __future__ import annotations

import logging

from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.rules import Rule, RuleSet
from decode.permissions.types import PermissionMode, ToolKind

logger = logging.getLogger(__name__)

# The reason returned when a mutating tool is denied in plan mode — it tells the model to present
# its plan and call ``exit_plan_mode`` rather than try to act.
_PLAN_DENY_REASON = "Plan mode is read-only — present your plan and call exit_plan_mode."


class PermissionGate:
    """Decide allow/ask/deny for a tool call by rule + mode x kind (ADR-0003 §1,3,4).

    Holds the active :class:`~decode.permissions.types.PermissionMode` (mutable via
    :meth:`set_mode`; gate default ``DEFAULT``), a user :class:`~decode.permissions.rules.RuleSet`
    (mutable via :meth:`set_user_rules`; empty by default), and the active-agent rule set (mutable
    via :meth:`set_agent_rules`; empty until an agent is selected). One instance is shared for a
    whole session.
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.DEFAULT,
        *,
        user_rules: RuleSet | None = None,
    ) -> None:
        self._mode = mode
        self._user_rules = user_rules if user_rules is not None else RuleSet()
        self._agent_rules = RuleSet()

    @property
    def mode(self) -> PermissionMode:
        """The mode the gate evaluates under (``DEFAULT`` at startup)."""
        return self._mode

    def set_mode(self, mode: PermissionMode) -> None:
        """Switch the active mode (the TUI / orchestration tools mutate it mid-session)."""
        logger.debug("gate mode %s -> %s", self._mode.value, mode.value)
        self._mode = mode

    def set_user_rules(self, user_rules: RuleSet) -> None:
        """Replace the user rule set (the always-allow flow reloads it after persisting a rule)."""
        logger.debug(
            "gate user rules: %d allow, %d deny", len(user_rules.allow), len(user_rules.deny)
        )
        self._user_rules = user_rules

    def set_agent_rules(self, agent_rules: RuleSet) -> None:
        """Replace the active-agent rule set (selecting an agent loads its catalog rules; §4,7).

        Called by the agent-selection helper when an agent is selected: it loads that agent's
        ``allow`` / ``deny`` catalog rules so e.g. the code-reviewer's ``bash(git *)`` takes effect
        for the run, merged (union) with the user rules. Replacing in place means the prior agent's
        rules never linger after a switch.
        """
        logger.debug(
            "gate agent rules: %d allow, %d deny", len(agent_rules.allow), len(agent_rules.deny)
        )
        self._agent_rules = agent_rules

    def check(self, request: PermissionRequest) -> PermissionDecision:
        """Return the gate's verdict for ``request`` (ADR-0003 §4): deny → allow → mode → ask.

        1. A **deny** rule (any source) → DENY, with a reason citing the rule.
        2. An **allow** rule (any source) → ALLOW.
        3. Otherwise the **mode x kind** decision (the task-017 floor): BYPASS allows; read-only
           allows under every mode; PLAN denies mutations; EDIT allows file edits; DEFAULT/EDIT
           ASK for other mutations.
        """
        decision = self._decide_with_rules(request)
        logger.debug(
            "gate.check tool=%s kind=%s subject=%r mode=%s -> %s",
            request.tool_name,
            request.kind.value,
            request.subject,
            self._mode.value,
            decision.outcome.value,
        )
        return decision

    def _decide_with_rules(self, request: PermissionRequest) -> PermissionDecision:
        """Walk deny → allow across every rule source, then fall through to the mode (ADR-0003 §4).

        Every source's **deny** list is checked before any **allow** list, so a deny from one
        source beats an allow from another (the union semantics ADR-0003 §4 relies on): the user
        ``.decode/settings.json`` rules and the active agent's catalog rules.
        """
        sources = self._rule_sources()
        for rule_set in sources:
            denied = rule_set.matching_deny(request)
            if denied is not None:
                return PermissionDecision.deny(mode=self._mode, reason=_deny_reason(denied))
        for rule_set in sources:
            if rule_set.matching_allow(request) is not None:
                return PermissionDecision.allow(mode=self._mode)
        return self._decide_by_mode(request.kind)

    def _rule_sources(self) -> tuple[RuleSet, ...]:
        """The rule sources evaluated as a union: the user set and the active-agent set (§4,7)."""
        return (self._user_rules, self._agent_rules)

    def _decide_by_mode(self, kind: ToolKind) -> PermissionDecision:
        """The pure mode x kind decision (the task-017 floor, below the rule layer)."""
        mode = self._mode
        if mode is PermissionMode.BYPASS:
            return PermissionDecision.allow(mode=mode)
        if kind is ToolKind.READ_ONLY:
            return PermissionDecision.allow(mode=mode)
        # A mutating tool (FILE_EDIT or OTHER) below this point.
        if mode is PermissionMode.PLAN:
            return PermissionDecision.deny(mode=mode, reason=_PLAN_DENY_REASON)
        if mode is PermissionMode.EDIT and kind is ToolKind.FILE_EDIT:
            return PermissionDecision.allow(mode=mode)
        # DEFAULT (any mutation) and EDIT (non-file-edit, i.e. bash) ask the human.
        return PermissionDecision.ask(mode=mode)


def _deny_reason(rule: Rule) -> str:
    """The human-/model-facing reason for a deny-rule hit, citing the matched rule."""
    cited = rule.tool_name if rule.pattern is None else f"{rule.tool_name}({rule.pattern})"
    return f"Denied by permission rule {cited}."
