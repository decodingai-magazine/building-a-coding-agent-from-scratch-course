"""Unit tests for the ``python -m evals`` CLI skeleton (ADR-0017 §1).

The subcommands are stubs until tasks 105/106; this proves the surface exists, ``--help`` works,
and — the load-bearing bit — building the CLI imports no ``opik`` at module scope (the Opik harness
is pulled in lazily by the tracks that need it, so ``--help`` never needs keys or a network).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from evals.run import cli

# The repo root: tests/unit/evals/test_run.py -> up 3 -> tests -> up 1 -> root. The top-level
# ``evals`` package is not installed (not in the wheel), so a subprocess must run from here to
# import it; ``decode`` is available from its editable install regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _subprocess_env() -> dict[str, str]:
    """Inherit the real env (venv, editable install) but silence file logging in the child."""
    return {**os.environ, "DECODE_LOG_FILE": ""}


def test_help_lists_the_eval_tracks():
    """``evals --help`` exits clean and names the track subcommands."""
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "benchmark" in result.output
    assert "regression" in result.output
    assert "suite" in result.output


def test_benchmark_subcommand_invokes_run_benchmark(mocker):
    """``evals benchmark`` forwards its filters to ``run_benchmark`` and reports the project (task 106)."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")

    result = CliRunner().invoke(cli, ["benchmark", "--task", "001-greeting", "--sandbox", "docker"])

    assert result.exit_code == 0, result.output
    _, kwargs = run_benchmark.call_args
    assert kwargs["task_id"] == "001-greeting"
    assert kwargs["sandbox"] == "docker"
    assert "decode-evals" in result.output


def test_benchmark_subcommand_forwards_trials_and_prints_the_summary(mocker):
    """``benchmark --trials 3`` forwards ``trials`` and prints the Rich aggregate table (ADR-0017 §8)."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")

    result = CliRunner().invoke(cli, ["benchmark", "--task", "001-greeting", "--trials", "3"])

    assert result.exit_code == 0, result.output
    _, kwargs = run_benchmark.call_args
    assert kwargs["trials"] == 3
    # The summary table renders even on the mock result (graceful-empty), naming the trial count.
    assert "trial(s)" in result.output


@pytest.mark.parametrize("trials", ["0", "-1"])
def test_benchmark_subcommand_rejects_a_non_positive_trials(mocker, trials):
    """``--trials 0`` / ``--trials -1`` fail loudly (click range) and never reach ``run_benchmark``.

    The QA-round-1 bug: without a range guard the run exited 0 with a misleading "experiment logged"
    and a nonsense ``0 task(s) x 0 trial(s)`` / ``pass@-1`` table.
    """
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")

    result = CliRunner().invoke(cli, ["benchmark", "--task", "001-greeting", "--trials", trials])

    assert result.exit_code != 0
    assert "trials" in result.output.lower()
    run_benchmark.assert_not_called()


def test_benchmark_subcommand_rejects_a_non_positive_nb_samples(mocker):
    """``--nb-samples 0`` is a friendly range error too — never a silent zero-item cap."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")

    result = CliRunner().invoke(cli, ["benchmark", "--task", "001-greeting", "--nb-samples", "0"])

    assert result.exit_code != 0
    run_benchmark.assert_not_called()


def test_benchmark_subcommand_reports_an_empty_selection(mocker):
    """A ``BenchmarkSelectionError`` becomes a friendly non-zero CLI error, not a traceback."""
    from evals.harness.benchmark import BenchmarkSelectionError

    mocker.patch(
        "evals.harness.benchmark.run_benchmark",
        side_effect=BenchmarkSelectionError("no benchmark task matched"),
    )

    result = CliRunner().invoke(cli, ["benchmark", "--task", "nope"])

    assert result.exit_code != 0
    assert "no benchmark task matched" in result.output


def test_regression_subcommand_invokes_run_regression(mocker):
    """``evals regression --probe X`` forwards the id to ``run_regression`` and reports the project."""
    run_regression = mocker.patch("evals.harness.regression.run_regression")

    result = CliRunner().invoke(cli, ["regression", "--probe", "smoke-read-tool"])

    assert result.exit_code == 0, result.output
    _, kwargs = run_regression.call_args
    assert kwargs["probe_id"] == "smoke-read-tool"
    assert "decode-evals" in result.output


def test_regression_subcommand_reports_an_empty_selection(mocker):
    """A ``RegressionSelectionError`` becomes a friendly non-zero CLI error, not a traceback."""
    from evals.harness.regression import RegressionSelectionError

    mocker.patch(
        "evals.harness.regression.run_regression",
        side_effect=RegressionSelectionError("no regression probe matched"),
    )

    result = CliRunner().invoke(cli, ["regression", "--probe", "nope"])

    assert result.exit_code != 0
    assert "no regression probe matched" in result.output


def test_suite_subcommand_runs_and_reports_pass_rate(mocker):
    """``evals suite`` runs the Test Suite, reports the pass rate + project, and exits clean above bar."""
    run_test_suite = mocker.patch("evals.harness.test_suite.run_test_suite")
    run_test_suite.return_value = mocker.Mock(pass_rate=1.0)

    result = CliRunner().invoke(cli, ["suite"])

    assert result.exit_code == 0, result.output
    assert "pass rate 100%" in result.output
    assert "decode-evals" in result.output


def test_suite_subcommand_gates_non_zero_below_the_bar(mocker):
    """A pass rate under the suite bar is a friendly non-zero exit — the regression gate fires (§6)."""
    run_test_suite = mocker.patch("evals.harness.test_suite.run_test_suite")
    run_test_suite.return_value = mocker.Mock(pass_rate=0.5)

    result = CliRunner().invoke(cli, ["suite"])

    assert result.exit_code != 0
    assert "below the bar" in result.output


def test_suite_subcommand_reports_the_version_gate_when_opik_is_too_old(mocker):
    """On the pinned opik 1.9.8 the surface exits with a clear versioned message, not a traceback."""
    from evals.harness.test_suite import SuiteUnavailableError

    mocker.patch(
        "evals.harness.test_suite.run_test_suite",
        side_effect=SuiteUnavailableError("Opik Test Suites need opik>=2.0"),
    )

    result = CliRunner().invoke(cli, ["suite"])

    assert result.exit_code != 0
    assert "opik>=2.0" in result.output


def test_sync_regression_upserts_probe_items(mocker):
    """``evals sync --regression --no-benchmark`` syncs the probe registry into the regression dataset."""
    sync_regression = mocker.patch("evals.harness.datasets.sync_regression_dataset")
    probe = mocker.Mock()
    mocker.patch("evals.regression.loader.load_probes", return_value=[probe])

    result = CliRunner().invoke(cli, ["sync", "--no-benchmark", "--regression"])

    assert result.exit_code == 0, result.output
    sync_regression.assert_called_once_with([probe])
    assert "decode-regression-v1" in result.output


def test_sync_default_syncs_both_datasets(mocker):
    """Plain ``evals sync`` upserts BOTH the benchmark and the regression datasets."""
    sync_benchmark = mocker.patch("evals.harness.datasets.sync_benchmark_dataset")
    sync_regression = mocker.patch("evals.harness.datasets.sync_regression_dataset")
    mocker.patch("evals.harness.task_loader.load_benchmark_tasks", return_value=[])
    mocker.patch("evals.regression.loader.load_probes", return_value=[])

    result = CliRunner().invoke(cli, ["sync"])

    assert result.exit_code == 0, result.output
    sync_benchmark.assert_called_once()
    sync_regression.assert_called_once()


def test_sync_nothing_selected_is_a_friendly_no_op():
    """``evals sync --no-benchmark --no-regression`` selects nothing and says so, never errors."""
    result = CliRunner().invoke(cli, ["sync", "--no-benchmark", "--no-regression"])

    assert result.exit_code == 0
    assert "nothing selected" in result.output


def test_importing_the_cli_does_not_import_opik():
    """Building the CLI must not import ``opik`` at module scope (ADR-0017 §1).

    Checked in a fresh subprocess so no other test's imports pollute ``sys.modules`` — the import
    must be clean on its own.
    """
    code = (
        "import evals.run, sys; "
        "leaked = sorted(m for m in sys.modules if 'opik' in m); "
        "assert not leaked, leaked"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=_subprocess_env(),
    )
    assert result.returncode == 0, result.stderr


def test_python_m_evals_help_runs():
    """``python -m evals --help`` works end to end (real entrypoint, real logging bootstrap)."""
    result = subprocess.run(
        [sys.executable, "-m", "evals", "--help"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=_subprocess_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "benchmark" in result.stdout
