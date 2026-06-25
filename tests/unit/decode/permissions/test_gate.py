"""Unit tests for :class:`decode.permissions.gate.PermissionGate`.

ADR-0003 §1,3,4: the gate is a **real decision**. ``check(request)`` evaluates, in precedence
order, **deny rule → allow rule → mode decision → ask** and returns ALLOW / ASK / DENY. The mode
is mutable via :meth:`PermissionGate.set_mode`; the user rule set is loaded from
``.decode/settings.json`` and reloadable via :meth:`PermissionGate.set_user_rules`. The gate is
still policy-only: it never owns the terminal UI; turning an ASK into a human verdict is the
resolver's job.
"""

import pytest

from decode.entities.permissions import PermissionOutcome, PermissionRequest
from decode.permissions import rules
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode, ToolKind


@pytest.fixture
def gate() -> PermissionGate:
    return PermissionGate()


def _request(kind: ToolKind) -> PermissionRequest:
    return PermissionRequest(tool_name="t", args="", kind=kind)


def _rule_set(*, allow: list[str] | None = None, deny: list[str] | None = None) -> rules.RuleSet:
    return rules.RuleSet(
        allow=[rules.parse_rule(r) for r in (allow or [])],
        deny=[rules.parse_rule(r) for r in (deny or [])],
    )


def test_gate_defaults_to_default_mode(gate):
    assert gate.mode is PermissionMode.DEFAULT


# --- read-only requests auto-ALLOW under every mode (ADR-0003 §1) ---------------------------


@pytest.mark.parametrize(
    "mode",
    [PermissionMode.DEFAULT, PermissionMode.PLAN, PermissionMode.EDIT, PermissionMode.BYPASS],
)
def test_read_only_request_allows_under_every_mode(gate, mode):
    gate.set_mode(mode)

    decision = gate.check(_request(ToolKind.READ_ONLY))

    assert decision.outcome is PermissionOutcome.ALLOW
    assert decision.mode is mode


# --- file-edit requests: ASK (default), ALLOW (edit), DENY (plan), ALLOW (bypass) -----------


def test_file_edit_asks_under_default(gate):
    decision = gate.check(_request(ToolKind.FILE_EDIT))

    assert decision.outcome is PermissionOutcome.ASK
    assert decision.mode is PermissionMode.DEFAULT


def test_file_edit_allows_under_edit(gate):
    gate.set_mode(PermissionMode.EDIT)

    decision = gate.check(_request(ToolKind.FILE_EDIT))

    assert decision.outcome is PermissionOutcome.ALLOW


def test_file_edit_denies_under_plan_with_exit_plan_mode_reason(gate):
    gate.set_mode(PermissionMode.PLAN)

    decision = gate.check(_request(ToolKind.FILE_EDIT))

    assert decision.outcome is PermissionOutcome.DENY
    assert decision.reason is not None
    assert "exit_plan_mode" in decision.reason


def test_file_edit_allows_under_bypass(gate):
    gate.set_mode(PermissionMode.BYPASS)

    decision = gate.check(_request(ToolKind.FILE_EDIT))

    assert decision.outcome is PermissionOutcome.ALLOW


# --- other (bash) requests: ASK (default), ASK (edit), DENY (plan), ALLOW (bypass) ----------


def test_other_asks_under_default(gate):
    decision = gate.check(_request(ToolKind.OTHER))

    assert decision.outcome is PermissionOutcome.ASK


def test_other_asks_under_edit(gate):
    # Edit mode auto-allows file edits but NOT other mutating tools (bash) — those still ask.
    gate.set_mode(PermissionMode.EDIT)

    decision = gate.check(_request(ToolKind.OTHER))

    assert decision.outcome is PermissionOutcome.ASK


def test_other_denies_under_plan_with_exit_plan_mode_reason(gate):
    gate.set_mode(PermissionMode.PLAN)

    decision = gate.check(_request(ToolKind.OTHER))

    assert decision.outcome is PermissionOutcome.DENY
    assert decision.reason is not None
    assert "exit_plan_mode" in decision.reason


def test_other_allows_under_bypass(gate):
    gate.set_mode(PermissionMode.BYPASS)

    decision = gate.check(_request(ToolKind.OTHER))

    assert decision.outcome is PermissionOutcome.ALLOW


# --- the mode is mutable (ADR-0003 §3) ------------------------------------------------------


def test_set_mode_makes_the_mode_mutable(gate):
    gate.set_mode(PermissionMode.PLAN)

    assert gate.mode is PermissionMode.PLAN
    # A read-only call is still allowed; a bash call is now denied (plan is read-only).
    assert gate.check(_request(ToolKind.READ_ONLY)).outcome is PermissionOutcome.ALLOW
    assert gate.check(_request(ToolKind.OTHER)).outcome is PermissionOutcome.DENY


# --- rule layer: deny → allow → mode → ask (ADR-0003 §4) -------------------------------------


def _bash(subject: str) -> PermissionRequest:
    return PermissionRequest(tool_name="bash", args="", kind=ToolKind.OTHER, subject=subject)


def test_with_no_rules_the_gate_is_mode_only(gate):
    # A gate with an empty rule set behaves exactly as task 017 (mode-only floor).
    assert gate.check(_bash("rm -rf x")).outcome is PermissionOutcome.ASK


def test_deny_rule_beats_bypass_mode(gate):
    # A deny rule beats everything — even bypass (ADR-0003 §4 acceptance).
    gate.set_mode(PermissionMode.BYPASS)
    gate.set_user_rules(_rule_set(deny=["bash(rm *)"]))

    decision = gate.check(_bash("rm -rf x"))

    assert decision.outcome is PermissionOutcome.DENY
    assert decision.reason is not None
    assert "rm *" in decision.reason  # the reason cites the rule


def test_allow_rule_beats_the_mode_ask(gate):
    # An allow rule turns an otherwise-ASK bash command into ALLOW under default mode.
    gate.set_user_rules(_rule_set(allow=["bash(npm run test:*)"]))

    assert gate.check(_bash("npm run test:unit")).outcome is PermissionOutcome.ALLOW
    # A non-matching command still falls through to the mode (which ASKs).
    assert gate.check(_bash("npm run build")).outcome is PermissionOutcome.ASK


def test_bare_allow_rule_allows_any_call_of_that_tool(gate):
    gate.set_user_rules(_rule_set(allow=["bash"]))

    assert gate.check(_bash("anything at all")).outcome is PermissionOutcome.ALLOW


def test_deny_beats_allow_when_both_match(gate):
    # Precedence proof: a subject matching BOTH an allow and a deny rule → DENY (deny first).
    gate.set_user_rules(_rule_set(allow=["bash(rm *)"], deny=["bash(rm *)"]))

    assert gate.check(_bash("rm -rf x")).outcome is PermissionOutcome.DENY


def test_set_user_rules_reloads_in_place(gate):
    # The always-allow flow persists a rule then reloads — a later identical call auto-allows.
    assert gate.check(_bash("npm run test:unit")).outcome is PermissionOutcome.ASK

    gate.set_user_rules(_rule_set(allow=["bash(npm run test:*)"]))

    assert gate.check(_bash("npm run test:unit")).outcome is PermissionOutcome.ALLOW


# --- the active-agent rule source (ADR-0003 §4,7; task 020) ----------------------------------


def test_agent_rules_default_empty_so_the_gate_is_mode_only(gate):
    # With no agent rules loaded the gate behaves exactly as user-rules-only (mode-only floor).
    assert gate.check(_bash("git diff")).outcome is PermissionOutcome.ASK


def test_agent_allow_rule_auto_allows_a_matching_call(gate):
    # The code-reviewer's catalog rule `bash(git *)` rides the agent source: `git diff` allows...
    gate.set_agent_rules(_rule_set(allow=["bash(git *)"]))

    assert gate.check(_bash("git diff")).outcome is PermissionOutcome.ALLOW
    # ...while a non-`git` bash call still falls through to the mode (which ASKs).
    assert gate.check(_bash("rm x")).outcome is PermissionOutcome.ASK


def test_agent_deny_rule_beats_a_user_allow_rule(gate):
    # Union semantics: a deny from EITHER source beats an allow from EITHER source.
    gate.set_user_rules(_rule_set(allow=["bash(rm *)"]))
    gate.set_agent_rules(_rule_set(deny=["bash(rm *)"]))

    assert gate.check(_bash("rm -rf x")).outcome is PermissionOutcome.DENY


def test_user_deny_rule_beats_an_agent_allow_rule(gate):
    # The other direction: a user deny tightens an agent allow (a user can always say no).
    gate.set_user_rules(_rule_set(deny=["bash(git push)"]))
    gate.set_agent_rules(_rule_set(allow=["bash(git *)"]))

    assert gate.check(_bash("git push")).outcome is PermissionOutcome.DENY
    # A sibling git command not denied by the user still auto-allows via the agent rule.
    assert gate.check(_bash("git diff")).outcome is PermissionOutcome.ALLOW


def test_set_agent_rules_replaces_in_place_on_agent_switch(gate):
    # Selecting a new agent replaces the agent rule source (the prior agent's rules don't linger).
    gate.set_agent_rules(_rule_set(allow=["bash(git *)"]))
    assert gate.check(_bash("git diff")).outcome is PermissionOutcome.ALLOW

    gate.set_agent_rules(_rule_set())  # switch to an agent with no rules

    assert gate.check(_bash("git diff")).outcome is PermissionOutcome.ASK
