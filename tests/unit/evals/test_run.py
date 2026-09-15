"""Unit tests for the ``python -m evals`` CLI skeleton (ADR-0017 §1).

The subcommands are stubs until tasks 105/106; this proves the surface exists, ``--help`` works,
and — the load-bearing bit — building the CLI imports no ``opik`` at module scope (the Opik harness
is pulled in lazily by the tracks that need it, so ``--help`` never needs keys or a network).
"""

from __future__ import annotations

import json
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
    assert "online" in result.output


def test_benchmark_subcommand_invokes_run_benchmark(mocker):
    """``evals benchmark`` forwards every flag to ``run_benchmark`` and reports where to look."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")
    run_benchmark.return_value.job_dir = Path(".decode/evals/runs/bench-x")
    run_benchmark.return_value.experiment_name = "bench-x"

    result = CliRunner().invoke(
        cli,
        [
            "benchmark",
            "--task",
            "001-find-and-replace",
            "--difficulty",
            "easy",
            "--sandbox",
            "modal",
            "--trials",
            "3",
            "--threads",
            "2",
            "--job-name",
            "bench-x",
            "--model",
            "qwen-x",
        ],
    )

    assert result.exit_code == 0, result.output
    _, kwargs = run_benchmark.call_args
    assert kwargs == {
        "task_id": "001-find-and-replace",
        "difficulty": "easy",
        "sandbox": "modal",
        "trials": 3,
        "threads": 2,
        "job_name": "bench-x",
        "model": "qwen-x",
    }
    assert "decode-evals" in result.output
    assert "bench-x" in result.output
    assert ".decode/evals/runs/bench-x" in result.output


def test_benchmark_subcommand_refuses_a_traversing_job_name(mocker):
    """``--job-name '../../evil'`` is refused BEFORE any (billed) trial starts.

    The name is both a directory under ``.decode/evals/runs/`` and the Opik experiment name, so a
    ``..`` component would write Trial Dirs outside the harness tree.
    """
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")

    result = CliRunner().invoke(cli, ["benchmark", "--job-name", "../../evil"])

    assert result.exit_code != 0
    assert "--job-name" in result.output
    run_benchmark.assert_not_called()


def test_benchmark_subcommand_defaults_are_the_cheap_ones(mocker):
    """No flags: one trial, docker, and the harness picks the job name + thread policy."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")

    result = CliRunner().invoke(cli, ["benchmark"])

    assert result.exit_code == 0, result.output
    _, kwargs = run_benchmark.call_args
    assert kwargs["sandbox"] == "docker"
    assert kwargs["trials"] == 1
    assert kwargs["threads"] is None  # resolved per sandbox by run_benchmark
    assert kwargs["job_name"] is None
    assert kwargs["model"] is None
    assert kwargs["difficulty"] is None


def test_benchmark_subcommand_prints_the_summary_table(mocker):
    """``--trials 3`` labels the table's columns with the real k (ADR-0022 §6)."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")
    run_benchmark.return_value.job_dir = Path(".decode/evals/runs/bench-x")

    # A wide console: the runner's 80 columns wrap the ten-column table's headers mid-word.
    result = CliRunner().invoke(
        cli, ["benchmark", "--task", "001-greeting", "--trials", "3"], env={"COLUMNS": "160"}
    )

    assert result.exit_code == 0, result.output
    # The table renders even on the mock result (graceful-empty), naming the trial count.
    assert "trial(s)" in result.output
    assert "pass@3" in result.output
    assert "spend: wall clock" in result.output  # the spend line under the table


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


def test_benchmark_subcommand_rejects_a_non_positive_threads(mocker):
    """``--threads 0`` is a friendly range error too — never a silent zero-worker run."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")

    result = CliRunner().invoke(cli, ["benchmark", "--threads", "0"])

    assert result.exit_code != 0
    run_benchmark.assert_not_called()


def test_benchmark_subcommand_has_no_nb_samples_flag(mocker):
    """``--nb-samples`` is gone: a job is scoped by ``--task`` / ``--difficulty`` (ADR-0022 §6)."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")

    result = CliRunner().invoke(cli, ["benchmark", "--nb-samples", "1"])

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


def test_benchmark_subcommand_passes_a_repeated_task_as_a_subset(mocker):
    """Several ``--task`` flags select one hand-picked subset for ONE experiment."""
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")
    run_benchmark.return_value.job_dir = Path(".decode/evals/runs/bench-x")
    run_benchmark.return_value.experiment_name = "bench-x"

    result = CliRunner().invoke(
        cli, ["benchmark", "--task", "001-find-and-replace", "--task", "002-regex-extraction"]
    )

    assert result.exit_code == 0, result.output
    assert run_benchmark.call_args.kwargs["task_id"] == (
        "001-find-and-replace",
        "002-regex-extraction",
    )


def test_benchmark_subcommand_passes_no_task_as_none(mocker):
    run_benchmark = mocker.patch("evals.harness.benchmark.run_benchmark")
    run_benchmark.return_value.job_dir = Path(".decode/evals/runs/bench-x")
    run_benchmark.return_value.experiment_name = "bench-x"

    result = CliRunner().invoke(cli, ["benchmark"])

    assert result.exit_code == 0, result.output
    assert run_benchmark.call_args.kwargs["task_id"] is None


def test_regression_subcommand_invokes_run_regression(mocker):
    """``evals regression --case X`` forwards the id to ``run_regression`` and reports the project."""
    run_regression = mocker.patch("evals.harness.regression.run_regression")

    result = CliRunner().invoke(cli, ["regression", "--case", "smoke-read-tool"])

    assert result.exit_code == 0, result.output
    _, kwargs = run_regression.call_args
    assert kwargs["case_id"] == "smoke-read-tool"
    assert kwargs["difficulty"] is None
    assert "decode-evals" in result.output


def test_regression_subcommand_forwards_the_difficulty_tier(mocker):
    """``evals regression --difficulty hard`` slices the paid run to one tier (ADR-0022 §8)."""
    run_regression = mocker.patch("evals.harness.regression.run_regression")

    result = CliRunner().invoke(cli, ["regression", "--difficulty", "hard"])

    assert result.exit_code == 0, result.output
    assert run_regression.call_args.kwargs["difficulty"] == "hard"


def test_regression_subcommand_rejects_an_unknown_tier():
    """A typo'd tier is a friendly ``Invalid value`` before any billed run starts."""
    result = CliRunner().invoke(cli, ["regression", "--difficulty", "trivial"])

    assert result.exit_code != 0
    assert "Invalid value" in result.output


def test_regression_subcommand_reports_an_empty_selection(mocker):
    """A ``RegressionSelectionError`` becomes a friendly non-zero CLI error, not a traceback."""
    from evals.harness.regression import RegressionSelectionError

    mocker.patch(
        "evals.harness.regression.run_regression",
        side_effect=RegressionSelectionError("no regression case matched"),
    )

    result = CliRunner().invoke(cli, ["regression", "--case", "nope"])

    assert result.exit_code != 0
    assert "no regression case matched" in result.output


def test_suite_subcommand_runs_and_reports_pass_rate(mocker):
    """``evals suite`` runs the Test Suite, reports the pass rate + project, and exits clean above bar."""
    run_test_suite = mocker.patch("evals.harness.test_suite.run_test_suite")
    run_test_suite.return_value = mocker.Mock(
        suite_name="decode-regression-suite-abcd1234", result=mocker.Mock(pass_rate=1.0)
    )

    result = CliRunner().invoke(cli, ["suite"])

    assert result.exit_code == 0, result.output
    assert "pass rate 100%" in result.output
    assert "decode-evals" in result.output
    # The resolved suite name is the run's breadcrumb: it says exactly which items were billed.
    assert "decode-regression-suite-abcd1234" in result.output


def test_suite_subcommand_gates_non_zero_below_the_bar(mocker):
    """A pass rate under the suite bar is a friendly non-zero exit — the regression gate fires (§6)."""
    run_test_suite = mocker.patch("evals.harness.test_suite.run_test_suite")
    run_test_suite.return_value = mocker.Mock(
        suite_name="decode-regression-suite-abcd1234", result=mocker.Mock(pass_rate=0.5)
    )

    result = CliRunner().invoke(cli, ["suite"])

    assert result.exit_code != 0
    assert "below the bar" in result.output


def test_suite_subcommand_forwards_the_case_and_tier_filters(mocker):
    """``suite`` takes the SAME ``--case`` / ``--difficulty`` slice ``regression`` does (§8)."""
    run_test_suite = mocker.patch("evals.harness.test_suite.run_test_suite")
    run_test_suite.return_value = mocker.Mock(
        suite_name="decode-regression-suite-abcd1234", result=mocker.Mock(pass_rate=1.0)
    )

    result = CliRunner().invoke(cli, ["suite", "--difficulty", "hard"])

    assert result.exit_code == 0, result.output
    assert run_test_suite.call_args.kwargs == {"case_id": None, "difficulty": "hard"}


def test_suite_subcommand_reports_an_empty_selection(mocker):
    """A ``SuiteSelectionError`` becomes a friendly non-zero CLI error, not a traceback."""
    from evals.harness.test_suite import SuiteSelectionError

    mocker.patch(
        "evals.harness.test_suite.run_test_suite",
        side_effect=SuiteSelectionError("no runnable regression case matched"),
    )

    result = CliRunner().invoke(cli, ["suite", "--case", "nope"])

    assert result.exit_code != 0
    assert "no runnable regression case matched" in result.output


def test_online_subcommand_prints_thread_scores(mocker):
    """``evals online`` runs the thread pass and prints one line per scored thread + a total."""
    mocker.patch("evals.harness.online.online_keys_missing", return_value=[])
    mocker.patch("evals.harness.online.live_project_name", return_value="decode-prod")
    mocker.patch("evals.harness.online.run_online_eval", return_value=object())
    mocker.patch(
        "evals.harness.online.format_thread_scores",
        return_value=["sess-1: conversation_coherence=0.88"],
    )

    result = CliRunner().invoke(cli, ["online"])

    assert result.exit_code == 0, result.output
    assert "sess-1: conversation_coherence=0.88" in result.output
    assert "scored 1 thread(s) in decode-prod" in result.output


def test_online_subcommand_forwards_the_filter(mocker):
    """``evals online --filter <oql>`` threads the OQL clause into ``run_online_eval``."""
    mocker.patch("evals.harness.online.online_keys_missing", return_value=[])
    mocker.patch("evals.harness.online.live_project_name", return_value="decode-prod")
    mocker.patch("evals.harness.online.format_thread_scores", return_value=[])
    run_online = mocker.patch("evals.harness.online.run_online_eval", return_value=object())

    result = CliRunner().invoke(cli, ["online", "--filter", 'status = "inactive"'])

    assert result.exit_code == 0, result.output
    assert run_online.call_args.kwargs["filter_string"] == 'status = "inactive"'


def test_online_subcommand_skips_friendly_without_keys(mocker):
    """Missing keys → a friendly skip (exit 0) that names the vars; ``run_online_eval`` never runs."""
    mocker.patch(
        "evals.harness.online.online_keys_missing",
        return_value=["OPIK_API_KEY", "GEMINI_API_KEY"],
    )
    run_online = mocker.patch("evals.harness.online.run_online_eval")

    result = CliRunner().invoke(cli, ["online"])

    assert result.exit_code == 0
    assert "skipped" in result.output
    assert "OPIK_API_KEY" in result.output and "GEMINI_API_KEY" in result.output
    run_online.assert_not_called()


def test_online_subcommand_reports_no_threads(mocker):
    """An empty result set is a clear message, not a bare success line."""
    mocker.patch("evals.harness.online.online_keys_missing", return_value=[])
    mocker.patch("evals.harness.online.live_project_name", return_value="decode-prod")
    mocker.patch("evals.harness.online.run_online_eval", return_value=object())
    mocker.patch("evals.harness.online.format_thread_scores", return_value=[])

    result = CliRunner().invoke(cli, ["online"])

    assert result.exit_code == 0, result.output
    assert "no threads to score in decode-prod" in result.output


def test_sync_regression_upserts_both_surfaces(mocker):
    """``evals sync --regression --no-benchmark`` writes the v2 dataset AND the Test Suite (§8)."""
    sync_regression = mocker.patch("evals.harness.datasets.sync_regression_cases")
    sync_regression.return_value.suite_name = "decode-regression-suite-abcd1234"
    case = mocker.Mock(skip_reason=None, difficulty="easy")
    mocker.patch("evals.regression.loader.load_cases", return_value=[case])

    result = CliRunner().invoke(cli, ["sync", "--no-benchmark", "--regression"])

    assert result.exit_code == 0, result.output
    sync_regression.assert_called_once_with([case])
    assert "decode-regression-v2" in result.output
    # The echo names the CONTENT-VERSIONED suite the sync resolved, never a guess at it.
    assert "decode-regression-suite-abcd1234" in result.output


def test_sync_regression_skips_a_skip_guarded_case(mocker):
    """The Opik surfaces carry what actually RUNS; the registry keeps the blocked case visible."""
    sync_regression = mocker.patch("evals.harness.datasets.sync_regression_cases")
    runnable = mocker.Mock(skip_reason=None, difficulty="easy")
    skipped = mocker.Mock(skip_reason="MCP has not shipped", difficulty="medium")
    skipped.id = "12-mcp-tool-usage"
    mocker.patch("evals.regression.loader.load_cases", return_value=[runnable, skipped])

    result = CliRunner().invoke(cli, ["sync", "--no-benchmark", "--regression"])

    assert result.exit_code == 0, result.output
    sync_regression.assert_called_once_with([runnable])
    # The echo names the skip, so "upserted 1" vs "2 case(s) available" explains itself.
    assert "1 skipped: 12-mcp-tool-usage" in result.output


def test_sync_forwards_the_difficulty_tier_to_both_tracks(mocker):
    """``make eval-regression ARGS='--difficulty hard'`` syncs the same tier the gate then runs."""
    sync_regression = mocker.patch("evals.harness.datasets.sync_regression_cases")
    easy = mocker.Mock(skip_reason=None, difficulty="easy")
    hard = mocker.Mock(skip_reason=None, difficulty="hard")
    mocker.patch("evals.regression.loader.load_cases", return_value=[easy, hard])

    result = CliRunner().invoke(
        cli, ["sync", "--no-benchmark", "--regression", "--difficulty", "hard"]
    )

    assert result.exit_code == 0, result.output
    sync_regression.assert_called_once_with([hard])


def test_sync_default_syncs_both_datasets(mocker):
    """Plain ``evals sync`` upserts BOTH the benchmark and the regression datasets."""
    sync_benchmark = mocker.patch("evals.harness.datasets.sync_benchmark_dataset")
    sync_regression = mocker.patch("evals.harness.datasets.sync_regression_cases")
    mocker.patch("evals.harness.task_loader.load_benchmark_tasks", return_value=[])
    mocker.patch("evals.regression.loader.load_cases", return_value=[])

    result = CliRunner().invoke(cli, ["sync"])

    assert result.exit_code == 0, result.output
    sync_benchmark.assert_called_once()
    sync_regression.assert_called_once()


def test_sync_nothing_selected_is_a_friendly_no_op():
    """``evals sync --no-benchmark --no-regression`` selects nothing and says so, never errors."""
    result = CliRunner().invoke(cli, ["sync", "--no-benchmark", "--no-regression"])

    assert result.exit_code == 0
    assert "nothing selected" in result.output


def _api_error(status_code: int = 401):
    """A raw Opik REST ``ApiError`` — what a present-but-invalid ``OPIK_API_KEY`` surfaces (task 121)."""
    from opik.rest_api.core.api_error import ApiError

    return ApiError(status_code=status_code, body={"errors": ["Unauthorized"]})


def _assert_friendly_opik_key_error(result, *, status: str = "401") -> None:
    """A raw ``ApiError`` must become ONE friendly line — non-zero, names the key, no traceback."""
    assert result.exit_code != 0
    assert "OPIK_API_KEY" in result.output
    assert ".env.example" in result.output
    assert status in result.output
    # No raw REST traceback / exception class leaks to the user.
    assert "Traceback" not in result.output
    assert "ApiError" not in result.output


def test_benchmark_subcommand_reports_an_invalid_opik_key(mocker):
    """A present-but-invalid ``OPIK_API_KEY`` → one friendly line, not a raw ``ApiError`` traceback."""
    mocker.patch("evals.harness.benchmark.run_benchmark", side_effect=_api_error(401))

    result = CliRunner().invoke(cli, ["benchmark", "--task", "001-greeting"])

    _assert_friendly_opik_key_error(result)


def test_regression_subcommand_reports_an_invalid_opik_key(mocker):
    """The headline ``make eval-regression`` ritual: a wrong key stays friendly (twice-flagged; task 121)."""
    mocker.patch("evals.harness.regression.run_regression", side_effect=_api_error(401))

    result = CliRunner().invoke(cli, ["regression", "--case", "05-web-fetch-discipline"])

    _assert_friendly_opik_key_error(result)


def test_sync_subcommand_reports_an_invalid_opik_key(mocker):
    """``evals sync`` (the first thing ``make eval-regression`` runs) friendly-fails on a wrong key."""
    case = mocker.Mock(skip_reason=None, difficulty="easy")
    mocker.patch("evals.regression.loader.load_cases", return_value=[case])
    mocker.patch("evals.harness.datasets.sync_regression_cases", side_effect=_api_error(401))

    result = CliRunner().invoke(cli, ["sync", "--no-benchmark", "--regression"])

    _assert_friendly_opik_key_error(result)


def test_online_subcommand_reports_an_invalid_opik_key(mocker):
    """The online track translates a raw ``ApiError`` the same way — no leaked traceback."""
    mocker.patch("evals.harness.online.online_keys_missing", return_value=[])
    mocker.patch("evals.harness.online.live_project_name", return_value="decode-prod")
    mocker.patch("evals.harness.online.run_online_eval", side_effect=_api_error(401))

    result = CliRunner().invoke(cli, ["online"])

    _assert_friendly_opik_key_error(result)


def test_suite_subcommand_reports_an_invalid_opik_key(mocker):
    """``suite`` wraps ``run_test_suite`` in ``opik_boundary`` too — a wrong key stays friendly.

    A present-but-invalid ``OPIK_API_KEY`` here must not dump the raw ``ApiError`` traceback the
    other four subcommands already suppress (task 122, Nit 2).
    """
    mocker.patch("evals.harness.test_suite.run_test_suite", side_effect=_api_error(401))

    result = CliRunner().invoke(cli, ["suite"])

    _assert_friendly_opik_key_error(result)


def test_a_non_auth_api_error_is_still_friendly(mocker):
    """A non-401 Opik failure (e.g. 500) is still one friendly line naming the status, not a traceback."""
    mocker.patch("evals.harness.regression.run_regression", side_effect=_api_error(500))

    result = CliRunner().invoke(cli, ["regression", "--case", "05-web-fetch-discipline"])

    _assert_friendly_opik_key_error(result, status="500")


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


def test_benchmark_help_imports_no_opik():
    """``evals benchmark --help`` must render with no keys and no network (ADR-0017 §1).

    A fresh subprocess so no other test's imports pollute ``sys.modules``: building AND invoking the
    help of the one opik-heaviest command still pulls in nothing from opik.
    """
    code = (
        "import sys\n"
        "from click.testing import CliRunner\n"
        "from evals.run import cli\n"
        "result = CliRunner().invoke(cli, ['benchmark', '--help'])\n"
        "assert result.exit_code == 0, result.output\n"
        "assert '--threads' in result.output, result.output\n"
        "leaked = sorted(m for m in sys.modules if 'opik' in m)\n"
        "assert not leaked, leaked\n"
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


def test_help_lists_the_two_live_project_commands():
    """``online-rule`` and ``mine`` are part of the CLI surface (task 163)."""
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "online-rule" in result.output
    assert "mine" in result.output


def test_online_rule_create_skips_friendly_without_keys(mocker):
    """No keys → ONE skip line, exit 0, and nothing reaches Opik (ADR-0017 §9)."""
    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=["OPIK_API_KEY"])
    create = mocker.patch("evals.harness.online_rule.create_response_quality_rule")

    result = CliRunner().invoke(cli, ["online-rule", "create"])

    assert result.exit_code == 0
    assert "evals online-rule: skipped — set OPIK_API_KEY" in result.output
    create.assert_not_called()


@pytest.mark.parametrize(
    ("argv", "patched"),
    [
        (["online-rule", "create"], "evals.harness.online_rule.create_response_quality_rule"),
        (["mine"], "evals.harness.mine.open_source"),
    ],
    ids=["online-rule-create", "mine"],
)
def test_the_opik_only_commands_do_not_demand_an_inference_key(mocker, argv, patched):
    """Neither command makes an inference call, so the preflight runs ``require_provider=False``.

    Under the repo's committed ``.env`` (``LLM_PROVIDER=modal``) the full preflight would name
    ``MODAL_ENDPOINT_URL`` and skip a read-only query that never touches Modal (task 163 QA).
    """
    guard = mocker.patch("evals.harness.keys.eval_keys_missing", return_value=["OPIK_API_KEY"])
    mocker.patch(patched)

    result = CliRunner().invoke(cli, argv)

    assert result.exit_code == 0
    assert guard.call_args.kwargs == {"require_provider": False}


def test_online_rule_create_forwards_every_flag(mocker):
    """Each flag lands on ``create_response_quality_rule``; the created id is printed."""
    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=[])
    outcome = mocker.Mock(
        action="created", project="decode-prod", model="gemini-2.5-flash", rule_id="rule-1"
    )
    create = mocker.patch(
        "evals.harness.online_rule.create_response_quality_rule", return_value=outcome
    )

    result = CliRunner().invoke(
        cli,
        [
            "online-rule",
            "create",
            "--project",
            "decode-prod",
            "--model",
            "gemini-2.5-flash",
            "--sampling",
            "0.5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert create.call_args.kwargs == {
        "project": "decode-prod",
        "model": "gemini-2.5-flash",
        "sampling": 0.5,
        "dry_run": False,
    }
    assert "created response_quality (rule-1) in decode-prod" in result.output


def test_online_rule_create_reports_an_existing_rule_without_creating_one(mocker):
    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=[])
    outcome = mocker.Mock(action="exists", project="decode-prod", model="m", rule_id="rule-0")
    mocker.patch("evals.harness.online_rule.create_response_quality_rule", return_value=outcome)

    result = CliRunner().invoke(cli, ["online-rule", "create"])

    assert result.exit_code == 0
    assert "already exists: rule-0" in result.output


def test_online_rule_create_reports_an_underivable_judge_model_as_one_line(mocker):
    """The openrouter/modal routes refuse with one line naming ``--model`` — no traceback."""
    from evals.harness.online_rule import OnlineRuleError

    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=[])
    mocker.patch(
        "evals.harness.online_rule.create_response_quality_rule",
        side_effect=OnlineRuleError("cannot derive an Opik judge model — pass --model <id>."),
    )

    result = CliRunner().invoke(cli, ["online-rule", "create"])

    assert result.exit_code == 1
    assert "pass --model" in result.output
    assert "Traceback" not in result.output


def test_online_rule_create_reports_an_invalid_opik_key(mocker):
    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=[])
    mocker.patch(
        "evals.harness.online_rule.create_response_quality_rule", side_effect=_api_error(401)
    )

    result = CliRunner().invoke(cli, ["online-rule", "create"])

    _assert_friendly_opik_key_error(result)


def test_mine_skips_friendly_without_keys(mocker):
    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=["OPIK_API_KEY"])
    open_source = mocker.patch("evals.harness.mine.open_source")

    result = CliRunner().invoke(cli, ["mine"])

    assert result.exit_code == 0
    assert "evals mine: skipped — set OPIK_API_KEY" in result.output
    open_source.assert_not_called()


def test_mine_forwards_its_flags_and_prints_one_table_per_signature(mocker):
    from evals.harness.mine import Signature, SignatureGroup, TraceHit

    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=[])
    source = mocker.Mock(project="decode-prod")
    mocker.patch("evals.harness.mine.open_source", return_value=source)
    signature = Signature(preset="errors", error="Boom", last_tool="bash", model="gemini-2.5-flash")
    hit = TraceHit(
        signature=signature,
        id="trace-1",
        thread_id="thread-1",
        start_time=None,
        git_sha=None,
        model="gemini-2.5-flash",
        error="Boom",
    )
    # Patch target is the HARNESS function `evals.harness.mine.mine` (the CLI imports it lazily,
    # inside the command body, as `mine_traces`) — not the Click command, which is also named `mine`.
    mine_traces = mocker.patch(
        "evals.harness.mine.mine", return_value=[SignatureGroup(signature=signature, traces=(hit,))]
    )

    result = CliRunner().invoke(
        cli, ["mine", "--preset", "denied", "--since", "2026-09-04T00:00:00Z", "--limit", "7"]
    )

    assert result.exit_code == 0, result.output
    assert mine_traces.call_args.kwargs == {
        "preset": "denied",
        "since": "2026-09-04T00:00:00Z",
        "limit": 7,
    }
    assert "trace-1" in result.output
    assert "1 trace(s) in 1 signature(s) from decode-prod" in result.output


def test_mine_json_emits_the_group_document(mocker):
    from evals.harness.mine import Signature, SignatureGroup, TraceHit

    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=[])
    mocker.patch("evals.harness.mine.open_source", return_value=mocker.Mock(project="decode-prod"))
    signature = Signature(preset="errors", error="Boom", last_tool=None, model=None)
    hit = TraceHit(
        signature=signature,
        id="trace-1",
        thread_id=None,
        start_time=None,
        git_sha=None,
        model=None,
        error="Boom",
    )
    mocker.patch(
        "evals.harness.mine.mine", return_value=[SignatureGroup(signature=signature, traces=(hit,))]
    )

    result = CliRunner().invoke(cli, ["mine", "--json"])

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document == [
        {
            "signature": "errors | Boom | - | -",
            "count": 1,
            "traces": [
                {
                    "id": "trace-1",
                    "thread_id": None,
                    "start_time": None,
                    "git_sha": None,
                    "model": None,
                    "error": "Boom",
                }
            ],
        }
    ]


def test_mine_says_so_when_nothing_matched(mocker):
    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=[])
    mocker.patch("evals.harness.mine.open_source", return_value=mocker.Mock(project="decode-prod"))
    mocker.patch("evals.harness.mine.mine", return_value=[])

    result = CliRunner().invoke(cli, ["mine", "--preset", "long"])

    assert result.exit_code == 0
    assert "evals mine: no long traces in decode-prod." in result.output


def test_mine_rejects_a_naive_since_as_one_line(mocker):
    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=[])
    mocker.patch("evals.harness.mine.open_source", return_value=mocker.Mock(project="decode-prod"))

    result = CliRunner().invoke(cli, ["mine", "--since", "2026-09-04T00:00:00"])

    assert result.exit_code == 1
    assert "timezone-aware" in result.output
    assert "Traceback" not in result.output


def test_mine_reports_an_invalid_opik_key(mocker):
    mocker.patch("evals.harness.keys.eval_keys_missing", return_value=[])
    mocker.patch("evals.harness.mine.open_source", side_effect=_api_error(401))

    result = CliRunner().invoke(cli, ["mine"])

    _assert_friendly_opik_key_error(result)


def test_the_new_live_commands_help_imports_no_opik():
    """``online-rule create --help`` and ``mine --help`` render with no keys and no network."""
    code = (
        "import sys\n"
        "from click.testing import CliRunner\n"
        "from evals.run import cli\n"
        "for argv in (['online-rule', 'create', '--help'], ['mine', '--help']):\n"
        "    result = CliRunner().invoke(cli, argv)\n"
        "    assert result.exit_code == 0, result.output\n"
        "assert '--sampling' in CliRunner().invoke(cli, ['online-rule', 'create', '--help']).output\n"
        "assert '--preset' in CliRunner().invoke(cli, ['mine', '--help']).output\n"
        "leaked = sorted(m for m in sys.modules if 'opik' in m)\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=_subprocess_env(),
    )
    assert result.returncode == 0, result.stderr


# --- `evals kitaru`: the Opik → Kitaru bridge (task 165) ---


def test_help_lists_the_kitaru_bridge():
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "kitaru" in result.output


def test_kitaru_import_skips_friendly_without_the_two_keys(mocker):
    """Keyless checkout: one line, exit 0 — never a traceback inside opik or kitaru."""
    mocker.patch(
        "evals.harness.kitaru_cli.kitaru_keys_missing",
        return_value=["OPIK_API_KEY", "KITARU_API_URL"],
    )
    open_source = mocker.patch("evals.harness.kitaru_import.open_source")

    result = CliRunner().invoke(cli, ["kitaru", "import", "trace-1"])

    assert result.exit_code == 0
    assert "skipped" in result.output
    assert "KITARU_API_URL" in result.output
    open_source.assert_not_called()


def test_kitaru_cohort_skips_friendly_without_the_two_keys(mocker):
    mocker.patch("evals.harness.kitaru_cli.kitaru_keys_missing", return_value=["KITARU_API_URL"])
    open_experiment = mocker.patch("evals.harness.kitaru_cohort.open_experiment")

    result = CliRunner().invoke(cli, ["kitaru", "cohort", "from-experiment", "bench-x"])

    assert result.exit_code == 0
    assert "skipped" in result.output
    open_experiment.assert_not_called()


def test_kitaru_import_prints_one_line_per_thread(mocker):
    from evals.harness.kitaru_import import ImportOutcome

    mocker.patch("evals.harness.kitaru_cli.kitaru_keys_missing", return_value=[])
    mocker.patch("evals.harness.kitaru_cli.kitaru_server", return_value="http://localhost:8000")
    mocker.patch(
        "evals.harness.kitaru_cli.resolve_ref", side_effect=lambda kind, name, **kw: f"{name}@1"
    )
    source = mocker.patch("evals.harness.kitaru_import.open_source")
    source.return_value.project = "decode-prod"
    run_import = mocker.patch(
        "evals.harness.kitaru_import.run_import",
        return_value=[
            ImportOutcome(thread="t-1", status="imported", session_id="kit-1", readiness="ready"),
            ImportOutcome(thread="t-2", status="skipped", session_id="kit-2"),
        ],
    )

    result = CliRunner().invoke(cli, ["kitaru", "import", "trace-1", "--thread", "t-2"])

    assert result.exit_code == 0, result.output
    assert "t-1 → kit-1 (ready)" in result.output
    assert "1 of 2 thread(s) imported" in result.output
    assert run_import.call_args.kwargs["threads"] == ["t-2"]
    assert run_import.call_args.kwargs["trace_ids"] == ["trace-1"]
    assert run_import.call_args.kwargs["agent_ref"] == "decode@1"
    assert run_import.call_args.kwargs["importer_ref"] == "opik@1"


def test_kitaru_import_turns_a_kitaru_failure_into_one_line(mocker):
    from evals.harness.kitaru_cli import KitaruCommandError

    mocker.patch("evals.harness.kitaru_cli.kitaru_keys_missing", return_value=[])
    mocker.patch("evals.harness.kitaru_cli.kitaru_server", return_value="http://localhost:8000")
    mocker.patch(
        "evals.harness.kitaru_cli.resolve_ref",
        side_effect=KitaruCommandError("importer 'opik' has no registered version yet"),
    )
    mocker.patch("evals.harness.kitaru_import.open_source")

    result = CliRunner().invoke(cli, ["kitaru", "import", "trace-1"])

    assert result.exit_code != 0
    assert "no registered version" in result.output
    assert "Traceback" not in result.output


def test_kitaru_cohort_prints_the_frozen_version(mocker):
    from evals.harness.kitaru_cohort import CohortOutcome, FailedTrial

    mocker.patch("evals.harness.kitaru_cli.kitaru_keys_missing", return_value=[])
    mocker.patch("evals.harness.kitaru_cli.kitaru_server", return_value="http://localhost:8000")
    mocker.patch("evals.harness.kitaru_cohort.open_experiment", return_value=([], {"x": 1}))
    build_cohort = mocker.patch(
        "evals.harness.kitaru_cohort.build_cohort",
        return_value=CohortOutcome(
            cohort="decode-benchmark-failures",
            version_ref="decode-benchmark-failures@2",
            session_count=3,
            added=2,
            unresolved=[FailedTrial(task_id="018", session_id=None, kitaru_session_id=None)],
        ),
    )

    result = CliRunner().invoke(cli, ["kitaru", "cohort", "from-experiment", "bench-x"])

    assert result.exit_code == 0, result.output
    assert "decode-benchmark-failures@2" in result.output
    assert "no Session for trial 018" in result.output
    assert build_cohort.call_args.kwargs["cohort"] == "decode-benchmark-failures"


def test_kitaru_cohort_refusal_is_one_line(mocker):
    from evals.harness.kitaru_cohort import CohortError

    mocker.patch("evals.harness.kitaru_cli.kitaru_keys_missing", return_value=[])
    mocker.patch("evals.harness.kitaru_cli.kitaru_server", return_value="http://localhost:8000")
    mocker.patch(
        "evals.harness.kitaru_cohort.open_experiment",
        side_effect=CohortError("this experiment recorded no Kitaru Sessions"),
    )

    result = CliRunner().invoke(cli, ["kitaru", "cohort", "from-experiment", "bench-x"])

    assert result.exit_code != 0
    assert "recorded no Kitaru Sessions" in result.output
    assert "Traceback" not in result.output


def test_the_kitaru_bridge_help_imports_neither_opik_nor_kitaru():
    """``--help`` must need no keys, no network and no kitaru install (ADR-0017 §1)."""
    code = (
        "import sys\n"
        "from click.testing import CliRunner\n"
        "from evals.run import cli\n"
        "for argv in (['kitaru', '--help'], ['kitaru', 'import', '--help'],\n"
        "             ['kitaru', 'cohort', 'from-experiment', '--help']):\n"
        "    result = CliRunner().invoke(cli, argv)\n"
        "    assert result.exit_code == 0, result.output\n"
        "assert '--thread' in CliRunner().invoke(cli, ['kitaru', 'import', '--help']).output\n"
        "leaked = sorted(m for m in sys.modules if 'opik' in m or m.startswith('kitaru'))\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=_subprocess_env(),
    )
    assert result.returncode == 0, result.stderr
