"""Offline smoke tests for the tool-discipline regression probes 01-07 (ADR-0017 §2,6; task 112).

Every probe is exercised three ways, all offline / no keys:

* it is registered and loadable (``load_probes`` discovers all seven);
* its ``fixture`` seeds the Workspace it claims to (files land where the prompt expects them);
* where the assertion is MECHANICAL, the probe runs end-to-end through the real agent on a scripted
  ``FunctionModel`` (``install_model``) and every non-judge metric scores ``1.0`` — proving the metric
  bindings actually grade the behavior the probe describes.

The G-Eval judges (probes 04, 05) are constructed and asserted present, but never scored here: scoring
one opens a live LLM round-trip (that is the real-model spot-run's job, ADR-0017 §9). Probe 06 DOES run
end-to-end offline — the ``lsp`` tool drives a real ``ty`` server (a dev-group binary, no keys/network),
skip-guarded on ``ty`` being on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from opik.evaluation.metrics import GEval
from support.eval_models import (
    edit_then_finish,
    enter_plan_mode_then_finish,
    grep_then_finish,
    lsp_diagnostics_then_finish,
    read_then_finish,
    web_fetch_then_finish,
)

from decode.services.lsp import service as lsp_service
from evals.harness.regression import run_probe
from evals.regression.cases.diff_minimality import _MODULE, _MODULE_BODY
from evals.regression.cases.plan_mode_discipline import _APP, _APP_BODY
from evals.regression.loader import load_probes, probe_by_id
from evals.regression.probe import RegressionProbe

_EXPECTED_IDS = {
    "01-read-vs-cat",
    "02-grep-vs-bash",
    "03-edit-precision",
    "04-diff-minimality",
    "05-web-fetch-discipline",
    "06-lsp-diagnostics",
    "07-plan-mode-discipline",
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


def test_every_probe_has_honest_cap_and_tags() -> None:
    for probe_id in _EXPECTED_IDS:
        probe = probe_by_id(probe_id)
        assert probe.max_requests is not None and probe.max_requests > 0
        assert probe.tags, f"{probe_id} declares no tags"


# --- 01 read-vs-cat --------------------------------------------------------------------------


def test_read_vs_cat_fixture_seeds_notes(tmp_path: Path) -> None:
    probe_by_id("01-read-vs-cat").fixture(tmp_path)

    assert (tmp_path / "notes.txt").is_file()


def test_read_vs_cat_binds_read_and_not_bash() -> None:
    probe = probe_by_id("01-read-vs-cat")

    names = {metric.name for metric in probe.metrics}
    assert "tool_called_read" in names
    assert "tool_not_called_bash" in names


def test_read_vs_cat_runs_green_offline(install_model) -> None:
    install_model(read_then_finish("notes.txt", "It says the release train departs at 06:45."))
    probe = probe_by_id("01-read-vs-cat")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    _score_mechanical_metrics(probe, payload)


# --- 02 grep-vs-bash -------------------------------------------------------------------------


def test_grep_vs_bash_fixture_seeds_source_tree(tmp_path: Path) -> None:
    probe_by_id("02-grep-vs-bash").fixture(tmp_path)

    config = (tmp_path / "src" / "app" / "config.py").read_text(encoding="utf-8")
    assert "def parse_config" in config


def test_grep_vs_bash_runs_green_offline(install_model) -> None:
    install_model(grep_then_finish("parse_config", "It's defined in src/app/config.py."))
    probe = probe_by_id("02-grep-vs-bash")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert any(call["name"] == "grep" for call in payload["tool_calls"])
    _score_mechanical_metrics(probe, payload)


# --- 03 edit-precision -----------------------------------------------------------------------


def test_edit_precision_fixture_seeds_config(tmp_path: Path) -> None:
    probe_by_id("03-edit-precision").fixture(tmp_path)

    assert "PORT = 8000" in (tmp_path / "config.py").read_text(encoding="utf-8")


def test_edit_precision_single_line_change_runs_green_offline(install_model) -> None:
    install_model(edit_then_finish("config.py", "PORT = 8000", "PORT = 9000", "Port set to 9000."))
    probe = probe_by_id("03-edit-precision")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert payload["file_state"]["config.py"].count("PORT = 9000") == 1
    _score_mechanical_metrics(probe, payload)


# --- 04 diff-minimality ----------------------------------------------------------------------


def test_diff_minimality_fixture_seeds_module(tmp_path: Path) -> None:
    probe_by_id("04-diff-minimality").fixture(tmp_path)

    assert "def _helper" in (tmp_path / _MODULE).read_text(encoding="utf-8")


def test_diff_minimality_carries_a_geval_judge() -> None:
    probe = probe_by_id("04-diff-minimality")

    assert any(isinstance(metric, GEval) for metric in probe.metrics)


def test_diff_minimality_small_rename_runs_green_offline(install_model) -> None:
    renamed = _MODULE_BODY.replace("_helper", "_doubled")
    install_model(edit_then_finish(_MODULE, _MODULE_BODY, renamed, "Renamed _helper to _doubled."))
    probe = probe_by_id("04-diff-minimality")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert "_doubled" in payload["file_state"][_MODULE]
    _score_mechanical_metrics(probe, payload)  # FileDiffLinesMetric within threshold; judge skipped


# --- 05 web-fetch-discipline -----------------------------------------------------------------


def test_web_fetch_probe_has_a_context_and_judge() -> None:
    probe = probe_by_id("05-web-fetch-discipline")

    assert probe.context is not None
    assert any(isinstance(metric, GEval) for metric in probe.metrics)


def test_web_fetch_runs_green_against_the_local_server_offline(install_model) -> None:
    """The fetch hits the local http.server fixture, never the real network (the web probe's AC)."""
    probe = probe_by_id("05-web-fetch-discipline")
    url = probe.prompt.split()[1]  # "Fetch <url> and ..."
    install_model(
        web_fetch_then_finish(url, "The Widget API rate limit is 240 requests per minute.")
    )

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert any(call["name"] == "web_fetch" for call in payload["tool_calls"])
    _score_mechanical_metrics(probe, payload)  # ToolCalled(web_fetch) + MaxSteps; judge skipped


# --- 06 lsp-diagnostics ----------------------------------------------------------------------


def test_lsp_diagnostics_fixture_seeds_a_type_error(tmp_path: Path) -> None:
    probe_by_id("06-lsp-diagnostics").fixture(tmp_path)

    source = (tmp_path / "broken.py").read_text(encoding="utf-8")
    assert 'add("not", "numbers")' in source  # the deliberate type error


def test_lsp_diagnostics_binds_lsp_and_output_check() -> None:
    probe = probe_by_id("06-lsp-diagnostics")

    names = {metric.name for metric in probe.metrics}
    assert "tool_called_lsp" in names
    assert "output_contains_broken.py" in names


@pytest.mark.skipif(
    shutil.which("ty") is None, reason="the `ty` language server binary is not on PATH"
)
def test_lsp_diagnostics_runs_green_offline(install_model) -> None:
    """The ``lsp`` tool drives a REAL ``ty`` server on the seeded file — offline, no keys or network.

    The eval driver reaps the spawned ``ty`` subprocess in-loop (``run_agent_once``'s ``finally``), so
    no server leaks past the run; this asserts the service cache is empty afterward too.
    """
    install_model(
        lsp_diagnostics_then_finish(
            "broken.py", "broken.py has a type error: a str is passed to the int parameter of add."
        )
    )
    probe = probe_by_id("06-lsp-diagnostics")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert any(call["name"] == "lsp" for call in payload["tool_calls"])
    assert not lsp_service._CLIENTS, "the driver must reap the ty server (no leaked subprocess)"
    _score_mechanical_metrics(probe, payload)


# --- 07 plan-mode-discipline -----------------------------------------------------------------


def test_plan_mode_fixture_seeds_app(tmp_path: Path) -> None:
    probe_by_id("07-plan-mode-discipline").fixture(tmp_path)

    assert (tmp_path / "app.py").is_file()


def test_plan_mode_binds_enter_plan_mode_and_not_succeeded() -> None:
    probe = probe_by_id("07-plan-mode-discipline")

    names = {metric.name for metric in probe.metrics}
    assert "tool_called_enter_plan_mode" in names
    assert "tool_not_succeeded_write" in names
    assert "tool_not_succeeded_edit" in names


def test_plan_mode_runs_green_offline(install_model) -> None:
    install_model(
        enter_plan_mode_then_finish("Plan: 1) add a --verbose flag via argparse. 2) wire it.")
    )
    probe = probe_by_id("07-plan-mode-discipline")

    payload = run_probe(probe)

    assert payload["agent_error"] is None
    assert any(call["name"] == "enter_plan_mode" for call in payload["tool_calls"])
    assert payload["file_state"][_APP] == _APP_BODY  # the seeded module was left untouched
    _score_mechanical_metrics(probe, payload)
