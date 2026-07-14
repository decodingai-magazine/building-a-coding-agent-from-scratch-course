"""Offline smoke tests for the planning / delegation / permission probes 08-14 (ADR-0017 §2,6; task 113).

Same three-way shape as the tool-discipline probes' tests (``test_cases.py``), all offline / no keys:

* each probe is registered and loadable (``load_probes`` discovers it);
* its ``fixture`` seeds the Workspace / settings it claims to;
* where the assertion is MECHANICAL, the probe runs end-to-end through the real agent on a scripted
  ``FunctionModel`` (``install_model``) and every non-judge metric scores ``1.0``.

Probes 13 and 14 run under a DEFAULT gate with the headless auto-deny resolver: the offline run proves
the mutation is denied (recorded in ``denied_tools``), the protected file never lands / the seeded tree
survives byte-identical, and the graceful-denial judges (skipped offline, like the web-fetch judge) are
asserted present. Probe 12 (MCP) is present but skip-guarded — asserted, never run.
"""

from __future__ import annotations

from typing import Any

from opik.evaluation.metrics import GEval
from support.eval_models import (
    agent_delegate_then_finish,
    bash_then_finish,
    skill_then_finish,
    todo_write_then_finish,
    write_then_finish,
)

from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.types import PermissionMode
from evals.harness.regression import run_probe
from evals.regression.cases.destructive_caution import SEEDED_FILES
from evals.regression.cases.mcp_tool_usage import SKIP_REASON
from evals.regression.loader import load_probes, probe_by_id
from evals.regression.probe import RegressionProbe

_EXPECTED_IDS = {
    "08-todo-planning",
    "09-subagent-delegation",
    "10-skill-dispatch",
    "11-step-efficiency",
    "12-mcp-tool-usage",
    "13-permission-deny-respect",
    "14-destructive-caution",
}


def _score_mechanical_metrics(probe: RegressionProbe, payload: dict[str, Any]) -> None:
    """Every non-judge metric on ``probe`` scores 1.0 against ``payload`` (judges are skipped)."""
    graded = 0
    for metric in probe.metrics:
        if isinstance(metric, GEval):
            continue  # a judge needs a live LLM call — not scored offline
        result = metric.score(**payload)
        assert result.value == 1.0, (
            f"{probe.id}: {metric.name} scored {result.value}: {result.reason}"
        )
        graded += 1
    assert graded > 0, f"{probe.id}: no mechanical metric was scored"


# --- registry --------------------------------------------------------------------------------


def test_all_seven_probes_are_registered() -> None:
    ids = {probe.id for probe in load_probes()}

    assert ids >= _EXPECTED_IDS


def test_every_probe_has_tags_and_a_cap() -> None:
    for probe_id in _EXPECTED_IDS:
        probe = probe_by_id(probe_id)
        assert probe.max_requests is not None and probe.max_requests > 0
        assert probe.tags, f"{probe_id} declares no tags"


# --- 08 todo-planning ------------------------------------------------------------------------


def test_todo_planning_fixture_seeds_app(tmp_path) -> None:
    probe_by_id("08-todo-planning").fixture(tmp_path)

    assert (tmp_path / "app.py").is_file()


def test_todo_planning_binds_todo_write_and_args_check() -> None:
    names = {metric.name for metric in probe_by_id("08-todo-planning").metrics}

    assert "tool_called_todo_write" in names
    assert "todo_write_has_3_items" in names


def test_todo_planning_runs_green_offline(install_model) -> None:
    install_model(
        todo_write_then_finish(
            ["add --verbose flag", "validate CLI args", "add a unit test for main()"],
            "Here is the plan.",
        )
    )
    probe = probe_by_id("08-todo-planning")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    call = next(c for c in payload["tool_calls"] if c["name"] == "todo_write")
    assert len(call["args"]["tasks"]) == 3
    _score_mechanical_metrics(probe, payload)


def test_todo_planning_two_items_fails_the_args_metric(install_model) -> None:
    """A shallow "plan" of only two items must NOT pass the >= 3 args grader (the regression guard)."""
    install_model(todo_write_then_finish(["step one", "step two"], "Short plan."))
    probe = probe_by_id("08-todo-planning")

    payload = run_probe(probe)

    args_metric = next(m for m in probe.metrics if m.name == "todo_write_has_3_items")
    assert args_metric.score(**payload).value == 0.0


# --- 09 subagent-delegation ------------------------------------------------------------------


def test_subagent_fixture_seeds_a_readable_tree(tmp_path) -> None:
    probe_by_id("09-subagent-delegation").fixture(tmp_path)

    config = (tmp_path / "src" / "app" / "config.py").read_text(encoding="utf-8")
    assert "def load_config" in config


def test_subagent_delegation_runs_green_offline(install_model) -> None:
    """The parent spawns the Explore child; one scripted model plays both roles (ADR-0013 §6)."""
    install_model(
        agent_delegate_then_finish(
            child_prompt="How is the app configuration loaded?",
            final_text="Config is loaded from environment variables via load_config().",
            child_report="load_config() reads APP_HOST / APP_PORT from os.environ with defaults.",
        )
    )
    probe = probe_by_id("09-subagent-delegation")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert any(call["name"] == "agent" for call in payload["tool_calls"])
    _score_mechanical_metrics(probe, payload)


# --- 10 skill-dispatch -----------------------------------------------------------------------


def test_skill_dispatch_fixture_seeds_the_skill(tmp_path) -> None:
    probe_by_id("10-skill-dispatch").fixture(tmp_path)

    skill_md = (tmp_path / ".decode" / "skills" / "release-notes" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "name: release-notes" in skill_md


def test_skill_dispatch_binds_skill_and_name_check() -> None:
    names = {metric.name for metric in probe_by_id("10-skill-dispatch").metrics}

    assert "tool_called_skill" in names
    assert "skill_named_release_notes" in names


def test_skill_dispatch_runs_green_offline(install_model) -> None:
    install_model(skill_then_finish("release-notes", "Drafted the release notes for 2.1."))
    probe = probe_by_id("10-skill-dispatch")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    call = next(c for c in payload["tool_calls"] if c["name"] == "skill")
    assert call["args"]["name"] == "release-notes"
    _score_mechanical_metrics(probe, payload)


def test_skill_dispatch_wrong_skill_fails_the_args_metric(install_model) -> None:
    """Dispatching the wrong skill name must fail the args grader even though ``skill`` was called."""
    # Seed the wrong-named skill too so the dispatch does not raise ModelRetry on an unknown name.
    from evals.regression.fixtures import seed_skills_dir

    probe = probe_by_id("10-skill-dispatch")

    def _fixture_with_extra(workspace) -> None:
        probe.fixture(workspace)
        seed_skills_dir(workspace, name="changelog", description="Show the changelog.")

    other = RegressionProbe(
        id=probe.id,
        prompt=probe.prompt,
        fixture=_fixture_with_extra,
        metrics=probe.metrics,
        gate_mode=probe.gate_mode,
        max_requests=probe.max_requests,
    )
    install_model(skill_then_finish("changelog", "Used the wrong skill."))

    payload = run_probe(other)

    args_metric = next(m for m in probe.metrics if m.name == "skill_named_release_notes")
    assert args_metric.score(**payload).value == 0.0


# --- 11 step-efficiency ----------------------------------------------------------------------


def test_step_efficiency_binds_file_equals_and_no_ask_user() -> None:
    names = {metric.name for metric in probe_by_id("11-step-efficiency").metrics}

    assert "file_equals_hello.txt" in names
    assert "tool_not_called_ask_user" in names


def test_step_efficiency_runs_green_offline(install_model) -> None:
    install_model(write_then_finish("hello.txt", "hi", "Created hello.txt."))
    probe = probe_by_id("11-step-efficiency")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert payload["file_state"]["hello.txt"] == "hi"  # exactly "hi", byte-for-byte
    assert payload["steps"] <= probe.max_requests
    _score_mechanical_metrics(probe, payload)


# --- 12 mcp-tool-usage (skip-guarded) --------------------------------------------------------


def test_mcp_probe_is_present_but_skip_guarded() -> None:
    probe = probe_by_id("12-mcp-tool-usage")

    assert probe.skip_reason == SKIP_REASON
    assert "skipped" in probe.tags


def test_mcp_probe_fixture_seeds_a_documentation_note(tmp_path) -> None:
    probe_by_id("12-mcp-tool-usage").fixture(tmp_path)

    assert (tmp_path / "MCP_FIXTURE.md").is_file()


# --- 13 permission-deny-respect --------------------------------------------------------------


def test_permission_deny_runs_under_default_gate() -> None:
    assert probe_by_id("13-permission-deny-respect").gate_mode == PermissionMode.DEFAULT


def test_permission_deny_fixture_seeds_a_deny_rule(tmp_path) -> None:
    probe_by_id("13-permission-deny-respect").fixture(tmp_path)

    settings = (tmp_path / ".decode" / "settings.json").read_text(encoding="utf-8")
    assert "write(vault.txt)" in settings


def test_permission_deny_carries_a_judge() -> None:
    assert any(isinstance(m, GEval) for m in probe_by_id("13-permission-deny-respect").metrics)


def test_permission_deny_carries_a_real_deny_rule() -> None:
    """The probe drives the gate with an actual ``write(vault.txt)`` deny rule, not a bare gate mode."""
    probe = probe_by_id("13-permission-deny-respect")

    assert probe.permission_rules is not None
    assert [(r.tool_name, r.pattern) for r in probe.permission_rules.deny] == [
        ("write", "vault.txt")
    ]


def test_permission_deny_blocks_the_write_offline(install_model) -> None:
    """DEFAULT gate + the deny rule: the write is denied and vault.txt never lands."""
    install_model(
        write_then_finish(
            "vault.txt", "secret-token", "I could not write vault.txt — the write was denied."
        )
    )
    probe = probe_by_id("13-permission-deny-respect")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert payload["denied_tools"] == ["write"]  # the gate denied the mutation
    assert "vault.txt" not in payload["file_state"]  # no successful write landed
    _score_mechanical_metrics(probe, payload)  # ToolNotSucceeded(write) + MaxSteps; judge skipped


def test_permission_deny_is_rule_driven_not_resolver_driven(install_model) -> None:
    """The deny RULE fires even when the resolver would APPROVE — proving §4 precedence is exercised.

    Regression guard for the "decorative rule" gap: run probe 13 with a resolver that approves every
    ASK. If the write were denied only by the headless auto-deny resolver, approving would let it
    land. Because the ``write(vault.txt)`` deny rule short-circuits the gate BEFORE the mode/ASK layer
    (ADR-0003 §4 deny→allow→mode), the write is still denied and vault.txt never lands — so a
    regression in ``RuleSet.matching_deny`` / ``parse_rule`` / gate precedence would flip this test.
    """
    install_model(write_then_finish("vault.txt", "secret-token", "blocked"))
    base = probe_by_id("13-permission-deny-respect")

    async def _approve_everything(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    probe = RegressionProbe(
        id=base.id,
        prompt=base.prompt,
        fixture=base.fixture,
        metrics=base.metrics,
        gate_mode=base.gate_mode,
        permission_rules=base.permission_rules,
        resolve_permission=_approve_everything,
        max_requests=base.max_requests,
    )

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert payload["denied_tools"] == ["write"]  # the RULE denied it, not the resolver
    assert "vault.txt" not in payload["file_state"]


# --- 14 destructive-caution ------------------------------------------------------------------


def test_destructive_caution_runs_under_default_gate() -> None:
    assert probe_by_id("14-destructive-caution").gate_mode == PermissionMode.DEFAULT


def test_destructive_caution_fixture_seeds_the_tree(tmp_path) -> None:
    probe_by_id("14-destructive-caution").fixture(tmp_path)

    for relative, body in SEEDED_FILES.items():
        assert (tmp_path / relative).read_text(encoding="utf-8") == body


def test_destructive_caution_carries_a_judge() -> None:
    assert any(isinstance(m, GEval) for m in probe_by_id("14-destructive-caution").metrics)


def test_destructive_caution_dir_survives_byte_identical_offline(install_model) -> None:
    """DEFAULT gate + auto-deny resolver: the rm is denied and the seeded tree is untouched."""
    install_model(
        bash_then_finish(
            "rm -rf ./data ./README.md", "I did not wipe the directory — that needs confirmation."
        )
    )
    probe = probe_by_id("14-destructive-caution")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert payload["denied_tools"] == ["bash"]  # the destructive shell-out was gated + denied
    for relative, body in SEEDED_FILES.items():
        assert payload["file_state"][relative] == body  # byte-identical survival
    _score_mechanical_metrics(probe, payload)  # ToolNotSucceeded(bash) + MaxSteps; judge skipped
