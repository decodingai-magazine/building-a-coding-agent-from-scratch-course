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
    """``evals --help`` exits clean and names both track subcommands."""
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "benchmark" in result.output
    assert "regression" in result.output


def test_benchmark_subcommand_invokes_run_benchmark(mocker):
    """``evals benchmark`` forwards its filters to ``run_benchmark`` and reports the project (task 106)."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")

    result = CliRunner().invoke(cli, ["benchmark", "--task", "001-greeting", "--sandbox", "docker"])

    assert result.exit_code == 0, result.output
    _, kwargs = run_benchmark.call_args
    assert kwargs["task_id"] == "001-greeting"
    assert kwargs["sandbox"] == "docker"
    assert "decode-evals" in result.output


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


def test_regression_subcommand_is_stubbed():
    """The regression track is a stub until task 106 — it runs and says so, never errors."""
    result = CliRunner().invoke(cli, ["regression"])

    assert result.exit_code == 0
    assert "not implemented yet" in result.output


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
