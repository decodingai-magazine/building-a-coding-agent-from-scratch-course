"""Offline smoke tests for the memory / groundedness / contract probes 15-20 (ADR-0017 §2,6,7; task 114).

Same three-way shape as the earlier probe suites (``test_cases.py`` / ``test_cases_planning.py``), all
offline / no keys:

* each probe is registered and loadable (``load_probes`` discovers it);
* its ``fixture`` seeds the Workspace / memory it claims to;
* where the assertion is MECHANICAL, the probe runs end-to-end through the real agent on a scripted
  ``FunctionModel`` (``install_model``) and every non-judge metric scores ``1.0``.

The G-Eval judges (17, 18, and the adherence judge on 19) are constructed and asserted present, never
scored here — a judge needs a live LLM round-trip (the spot-run's job, ADR-0017 §9). Probe 16 is the
special case: a scripted ``FunctionModel`` streams a stub ~50-token usage that can never cross a
compaction trigger, so firing is proven at the MECHANISM level (the trigger predicate is ``True`` for the
seeded history under the probe's configured window, and ``compact()`` actually collapses that history),
with the true end-to-end fire left to the live spot-run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from opik.evaluation.metrics import GEval
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RunUsage
from support.eval_models import constant_text, echo_line, read_then_finish, write_then_finish

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.config.settings import settings
from decode.context.compaction import CompactOutcome, reserve_threshold, should_compact
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.memory.service import assemble_memory
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.tools.askuser import deny_user_question_resolver
from evals.harness.regression import run_probe
from evals.regression.cases.compaction_survival import (
    COMPACTION_KEEP_RECENT_TOKENS,
    COMPACTION_WINDOW_TOKENS,
    FACT_NEEDLE,
)
from evals.regression.cases.grounded_answer import _COMPONENT, _DOC
from evals.regression.cases.json_output_contract import _MODULE as _JSON_MODULE
from evals.regression.cases.memory_obedience import REQUIRED_PREFIX
from evals.regression.cases.no_hallucinated_files import MISSING_FILE, SEEDED_FILES
from evals.regression.cases.template_compliance import REQUIRED_HEADERS
from evals.regression.fixtures.conversation import _estimate_tokens
from evals.regression.loader import load_probes, probe_by_id
from evals.regression.probe import RegressionProbe

_EXPECTED_IDS = {
    "15-memory-obedience",
    "16-compaction-survival",
    "17-grounded-answer",
    "18-no-hallucinated-files",
    "19-template-compliance",
    "20-json-output-contract",
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


async def _deny(_request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny(reason="no approver in the test")


# --- registry --------------------------------------------------------------------------------


def test_all_six_probes_are_registered() -> None:
    ids = {probe.id for probe in load_probes()}

    assert ids >= _EXPECTED_IDS


def test_every_probe_has_tags_and_a_cap() -> None:
    for probe_id in _EXPECTED_IDS:
        probe = probe_by_id(probe_id)
        assert probe.max_requests is not None and probe.max_requests > 0
        assert probe.tags, f"{probe_id} declares no tags"


# --- 15 memory-obedience ---------------------------------------------------------------------


def test_memory_obedience_fixture_seeds_the_agents_md_rule(tmp_path: Path) -> None:
    probe_by_id("15-memory-obedience").fixture(tmp_path)

    agents_md = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert REQUIRED_PREFIX in agents_md


def test_memory_obedience_rule_is_actually_injected_into_the_prompt(tmp_path: Path) -> None:
    """The seeded AGENTS.md is discovered + assembled by the memory loader the driver runs.

    The driver leaves ``harness_home`` unset so it defaults to ``cwd`` (the Workspace); the instructions
    hook assembles memory from there. Asserting ``assemble_memory(workspace)`` surfaces the rule proves
    the rule reaches the model — the whole premise of the probe (task-114 memory-injection AC).
    """
    probe_by_id("15-memory-obedience").fixture(tmp_path)

    memory_block = assemble_memory(tmp_path)
    assert REQUIRED_PREFIX in memory_block
    assert "start" in memory_block.lower()


def test_memory_obedience_binds_the_filename_metric() -> None:
    names = {metric.name for metric in probe_by_id("15-memory-obedience").metrics}

    assert "new_py_files_prefixed_dc" in names


def test_memory_obedience_obeying_filename_runs_green_offline(install_model) -> None:
    install_model(
        write_then_finish("dc_strings.py", "def reverse(s):\n    return s[::-1]\n", "Created it.")
    )
    probe = probe_by_id("15-memory-obedience")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert "dc_strings.py" in payload["file_state"]
    _score_mechanical_metrics(probe, payload)


def test_memory_obedience_violating_filename_fails_the_metric(install_model) -> None:
    """A .py file that ignores the dc_ rule must fail the filename metric even though a file was made."""
    install_model(
        write_then_finish("strings.py", "def reverse(s):\n    return s[::-1]\n", "Made it.")
    )
    probe = probe_by_id("15-memory-obedience")

    payload = run_probe(probe)

    metric = next(m for m in probe.metrics if m.name == "new_py_files_prefixed_dc")
    assert metric.score(**payload).value == 0.0


# --- 16 compaction-survival ------------------------------------------------------------------


def test_compaction_survival_binds_the_output_contains_metric() -> None:
    names = {metric.name for metric in probe_by_id("16-compaction-survival").metrics}

    assert "output_contains_deploy_token" in names


def test_compaction_survival_history_crosses_the_configured_threshold() -> None:
    """AC: the prefilled history crosses the compaction trigger under the probe's configured window.

    The trigger is ``input_tokens >= window * (1 - reserve)``. The probe forces a small window via
    ``settings_overrides``; the near-limit history's coarse token estimate (the same chars/4 decode uses,
    which tracks a real tokenizer for English) must exceed that threshold — otherwise the live run would
    never compact, the exact 111 QA gap this asserts against.
    """
    probe = probe_by_id("16-compaction-survival")
    history = probe.message_history()
    tokens = _estimate_tokens(history)

    threshold = reserve_threshold(COMPACTION_WINDOW_TOKENS, settings.compaction_reserve_fraction)
    assert tokens >= threshold, f"history {tokens} tok does not cross trigger {threshold}"
    assert should_compact(
        RunUsage(input_tokens=tokens),
        window=COMPACTION_WINDOW_TOKENS,
        reserve=settings.compaction_reserve_fraction,
        enabled=True,
    )


def test_compaction_survival_compact_actually_collapses_the_history(
    install_model, tmp_path
) -> None:
    """``compact()`` fires on the seeded history under the probe's window/keep settings (mechanism proof).

    A scripted ``FunctionModel`` streams a stub ~50-token usage, so the auto-trigger can't fire offline;
    this drives the compaction body directly (the summarizer is the agent's own scripted model, exactly
    as the driver wires it) and asserts the near-limit history collapses to ``[summary, *tail]``.
    """
    install_model(constant_text(f"The token is {FACT_NEEDLE}."))
    probe = probe_by_id("16-compaction-survival")
    history = probe.message_history()

    saved = {
        "compaction_context_window_tokens": settings.compaction_context_window_tokens,
        "compaction_keep_recent_tokens": settings.compaction_keep_recent_tokens,
    }
    settings.compaction_context_window_tokens = COMPACTION_WINDOW_TOKENS
    settings.compaction_keep_recent_tokens = COMPACTION_KEEP_RECENT_TOKENS
    try:
        agent = build_agent()
        deps = AgentDeps(
            cwd=tmp_path,
            emit=lambda _event: None,
            gate=PermissionGate(mode=PermissionMode.BYPASS),
            resolve_permission=_deny,
            resolve_user_question=deny_user_question_resolver,
        )
        handler = AgentTurnHandler(
            agent,
            deps=deps,
            message_history=list(history),
            compaction_model=agent.model,
        )
        before = len(handler.message_history)

        outcome = asyncio.run(handler.compact())

        assert outcome is CompactOutcome.COMPACTED
        assert len(handler.message_history) < before
        head = handler.message_history[0]
        assert isinstance(head, ModelResponse) is False  # the summary is a synthetic ModelRequest
        assert "compacted" in str(handler.message_history[0].parts[0].content).lower()
    finally:
        settings.compaction_context_window_tokens = saved["compaction_context_window_tokens"]
        settings.compaction_keep_recent_tokens = saved["compaction_keep_recent_tokens"]


def test_compaction_survival_runs_green_offline(install_model) -> None:
    """The recall answer carries the fact; the OutputContains + MaxSteps metrics score 1.0 offline.

    Compaction itself does not fire here (FunctionModel's stub usage cannot cross the trigger — proven
    separately above); the graded behavior is that the agent surfaces the early fact in its answer.
    """
    install_model(constant_text(f"The production deploy token is {FACT_NEEDLE}."))
    probe = probe_by_id("16-compaction-survival")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert FACT_NEEDLE in payload["output"]
    assert "compaction_events" in payload  # the firing signal is surfaced for the live run
    _score_mechanical_metrics(probe, payload)


def test_compaction_survival_missing_fact_fails_the_metric(install_model) -> None:
    install_model(constant_text("I do not recall any token."))
    probe = probe_by_id("16-compaction-survival")

    payload = run_probe(probe)

    metric = next(m for m in probe.metrics if m.name == "output_contains_deploy_token")
    assert metric.score(**payload).value == 0.0


# --- 17 grounded-answer ----------------------------------------------------------------------


def test_grounded_answer_fixture_seeds_the_source_doc(tmp_path: Path) -> None:
    probe_by_id("17-grounded-answer").fixture(tmp_path)

    assert _COMPONENT in (tmp_path / _DOC).read_text(encoding="utf-8")


def test_grounded_answer_carries_a_faithfulness_judge() -> None:
    assert any(isinstance(m, GEval) for m in probe_by_id("17-grounded-answer").metrics)


def test_grounded_answer_runs_offline_without_scoring_the_judge(install_model) -> None:
    install_model(read_then_finish(_DOC, f"The {_COMPONENT} deduplicates inbound webhook events."))
    probe = probe_by_id("17-grounded-answer")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert any(call["name"] == "read" for call in payload["tool_calls"])
    _score_mechanical_metrics(probe, payload)  # MaxSteps only; the judge is skipped


# --- 18 no-hallucinated-files ----------------------------------------------------------------


def test_no_hallucinated_files_fixture_omits_the_missing_file(tmp_path: Path) -> None:
    probe_by_id("18-no-hallucinated-files").fixture(tmp_path)

    for relative in SEEDED_FILES:
        assert (tmp_path / relative).is_file()
    assert not (tmp_path / MISSING_FILE).exists()


def test_no_hallucinated_files_carries_a_judge() -> None:
    assert any(isinstance(m, GEval) for m in probe_by_id("18-no-hallucinated-files").metrics)


def test_no_hallucinated_files_runs_offline_without_scoring_the_judge(install_model) -> None:
    install_model(echo_line(f"There is no {MISSING_FILE} in this project; I did not find it."))
    probe = probe_by_id("18-no-hallucinated-files")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    _score_mechanical_metrics(probe, payload)  # MaxSteps only; the judge is skipped


# --- 19 template-compliance ------------------------------------------------------------------


def test_template_compliance_binds_a_metric_per_required_header() -> None:
    names = {metric.name for metric in probe_by_id("19-template-compliance").metrics}

    for _header, metric_name in REQUIRED_HEADERS:
        assert metric_name in names


def test_template_compliance_prompt_embeds_every_required_header() -> None:
    prompt = probe_by_id("19-template-compliance").prompt

    for header, _name in REQUIRED_HEADERS:
        assert header in prompt


def test_template_compliance_carries_an_adherence_judge() -> None:
    assert any(isinstance(m, GEval) for m in probe_by_id("19-template-compliance").metrics)


def test_template_compliance_runs_green_offline(install_model) -> None:
    report = "\n".join(f"{header}\nSome relevant content." for header, _ in REQUIRED_HEADERS)
    install_model(echo_line(report))
    probe = probe_by_id("19-template-compliance")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    _score_mechanical_metrics(probe, payload)  # every header Contains metric; the judge is skipped


def test_template_compliance_missing_header_fails_that_metric(install_model) -> None:
    # Drop the Findings section — its Contains metric must fail while the others pass.
    report = "## Summary\nok\n\n## Recommendations\nok"
    install_model(echo_line(report))
    probe = probe_by_id("19-template-compliance")

    payload = run_probe(probe)

    findings_metric = next(m for m in probe.metrics if m.name == "output_has_findings_header")
    assert findings_metric.score(**payload).value == 0.0


# --- 20 json-output-contract -----------------------------------------------------------------


def test_json_contract_fixture_seeds_the_module(tmp_path: Path) -> None:
    probe_by_id("20-json-output-contract").fixture(tmp_path)

    assert (tmp_path / _JSON_MODULE).is_file()


def test_json_contract_binds_is_json_and_schema_metrics() -> None:
    names = {metric.name for metric in probe_by_id("20-json-output-contract").metrics}

    assert "is_json_metric" in names
    assert "json_matches_review_summary" in names


def test_json_contract_valid_json_runs_green_offline(install_model) -> None:
    install_model(
        echo_line('{"file": "inventory.py", "summary": "restock helper", "issue_count": 0}')
    )
    probe = probe_by_id("20-json-output-contract")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    _score_mechanical_metrics(probe, payload)  # IsJson + schema + MaxSteps


def test_json_contract_prose_answer_fails_the_metrics(install_model) -> None:
    install_model(echo_line("Sure! The inventory module has a restock function."))
    probe = probe_by_id("20-json-output-contract")

    payload = run_probe(probe)

    schema_metric = next(m for m in probe.metrics if m.name == "json_matches_review_summary")
    assert schema_metric.score(**payload).value == 0.0
