"""The ``python -m evals`` CLI body — a Click group with the eval tracks as subcommands (ADR-0017).

``benchmark`` runs the outcome benchmark (ADR-0022 §1); ``regression`` runs the behavior cases
host-native (§8); ``suite`` runs the same cases against their natural-language assertions; ``sync``
upserts the Opik surfaces. Deliberately imports no ``opik`` at module scope — the Opik harness is
pulled in lazily by the tracks that need it, so building the CLI never needs keys or a network.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Iterator

# Opik HTTP statuses that mean "your credential was rejected" (a present-but-invalid OPIK_API_KEY).
_OPIK_AUTH_STATUSES = frozenset({401, 403})


def _opik_error_as_click(exc: Exception) -> click.ClickException:
    """Turn a raw Opik REST ``ApiError`` into ONE friendly CLI line (ADR-0017 §9; task 121).

    A present-but-invalid ``OPIK_API_KEY`` otherwise dumps a ~40-line ``ApiError`` traceback (HTTP
    headers and all) from ``make eval-regression`` — the ritual the docs tell every developer to
    type. An auth status (401/403) names the key exactly as the missing-key guard does; any other
    Opik failure still collapses to a single line naming the status. No secret is ever echoed.
    """
    status = getattr(exc, "status_code", None)
    if status in _OPIK_AUTH_STATUSES:
        message = (
            f"Opik rejected the API key ({status}) — check OPIK_API_KEY "
            "(see the Evals block in .env.example)."
        )
    else:
        detail = f" ({status})" if status is not None else ""
        message = (
            f"Opik request failed{detail} — check OPIK_API_KEY and your Opik workspace "
            "(see the Evals block in .env.example)."
        )
    return click.ClickException(f"evals: {message}")


@contextmanager
def opik_boundary() -> Iterator[None]:
    """Translate a raw Opik ``ApiError`` raised inside the block into a friendly ``ClickException``.

    Wraps each opik-reaching command body so a wrong key exits like a missing one — one line,
    non-zero, no traceback. ``opik`` is imported lazily here (not at module scope) so the CLI stays
    opik-free at build time and ``--help`` never needs keys or a network (ADR-0017 §1).
    """
    from opik.rest_api.core.api_error import ApiError

    try:
        yield
    except ApiError as exc:
        raise _opik_error_as_click(exc) from exc


def _validate_job_name(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """Reject a ``--job-name`` that is not one safe path segment, before any (billed) trial starts.

    The harness owns the rule (``evals.harness.benchmark.validate_job_name``) because every caller
    of ``new_job_dir`` must obey it; this callback only re-dresses it as the same one-line
    ``Invalid value for '--job-name'`` the other flags produce. Imported lazily so ``--help``, which
    exits before non-eager callbacks run, still pulls in no ``opik`` (ADR-0017 §1).
    """
    if value is None:
        return None
    from evals.harness.benchmark import validate_job_name

    try:
        return validate_job_name(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), ctx=ctx, param=param) from exc


@click.group()
def cli() -> None:
    """decode eval suite — benchmark + regression harness (ADR-0017)."""


@cli.command()
@click.option("--task", "task_id", default=None, help="Run only this benchmark task id.")
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default=None,
    help="Run only tasks of this difficulty tier.",
)
@click.option(
    "--sandbox",
    type=click.Choice(["docker", "modal"]),
    default="docker",
    show_default=True,
    help="The sandbox rung each Trial's `decode run` executes in.",
)
@click.option(
    "--trials",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Trials per task (Opik trial_count) — the pass@k / pass^k / flakiness axis.",
)
@click.option(
    "--threads",
    type=click.IntRange(min=1),
    default=None,
    help="Trials to run at once (Opik task_threads) [default: 1 for docker, 4 for modal].",
)
@click.option(
    "--job-name",
    default=None,
    callback=_validate_job_name,
    help="Name the job: the Opik experiment AND the Trial Dir parent [default: bench-<UTC stamp>].",
)
@click.option(
    "--model",
    default=None,
    help="Override the model every Trial runs on (`decode run --model`).",
)
def benchmark(
    task_id: str | None,
    difficulty: str | None,
    sandbox: str,
    trials: int,
    threads: int | None,
    job_name: str | None,
    model: str | None,
) -> None:
    """Run the outcome benchmark as one Opik Experiment (ADR-0022 §1,§3,§6).

    Each selected task runs ``--trials`` Benchmark Trials: a subprocess ``decode run`` against a
    fresh Seed Repo with ``SANDBOX_MODE=--sandbox``, graded host-side by the hidden ``tests/test.sh``
    Verifier on a pristine clone of the handed-back branch, with every trial's evidence left in its
    Trial Dir under ``.decode/evals/runs/<job>/``. The reward is the score of record; pass@1 / pass@k
    / pass^k / flakiness / cost ride ``experiment_scoring_functions`` onto the experiment row and are
    printed here as a Rich table with per-tier rollups. Opik + the harness are imported lazily so
    ``--help`` never needs keys or a network (ADR-0017 §1).
    """
    from rich.console import Console

    from evals.harness.aggregates import render_summary_table
    from evals.harness.benchmark import BenchmarkSelectionError, run_benchmark, summarize

    try:
        with opik_boundary():
            run = run_benchmark(
                task_id=task_id,
                difficulty=difficulty,
                sandbox=sandbox,
                trials=trials,
                threads=threads,
                job_name=job_name,
                model=model,
            )
    except BenchmarkSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    Console().print(render_summary_table(summarize(run.result, trials=trials)))
    click.echo(
        f"evals benchmark: experiment {run.experiment_name} logged under "
        f"{settings_project_name()}; trial dirs in {run.job_dir}."
    )


def settings_project_name() -> str:
    """The Opik project eval runs log under — read lazily so the CLI stays opik/settings-light."""
    from decode.config.settings import settings

    return settings.eval_project_name


@cli.command()
@click.option("--case", "case_id", default=None, help="Run only this regression case id.")
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default=None,
    help="Run only the cases of this difficulty tier.",
)
def regression(case_id: str | None, difficulty: str | None) -> None:
    """Run the behavior Regression Cases host-native as an Opik experiment (ADR-0022 §8).

    Each selected case seeds a fresh temp Workspace, runs the real agent HOST-NATIVE (``none`` mode —
    no docker) under the case's gate policy, and scores its behavior with the case's deterministic
    metrics. ``--difficulty`` slices the run to one tier and names the experiment after it
    (``decode-regression-gate-hard``), so a tier's baseline stays its own. Opik + the harness are
    imported lazily so ``--help`` never needs keys or a network (ADR-0017 §1).
    """
    from evals.harness.regression import RegressionSelectionError, run_regression

    try:
        with opik_boundary():
            run_regression(case_id=case_id, difficulty=difficulty)
    except RegressionSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"evals regression: experiment logged under {settings_project_name()}.")


@cli.command()
@click.option("--case", "case_id", default=None, help="Run only this regression case id.")
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default=None,
    help="Run only the cases of this difficulty tier.",
)
def suite(case_id: str | None, difficulty: str | None) -> None:
    """Run the Opik Test Suite regression surface — natural-language assertions (ADR-0022 §8).

    The CONTRAST to ``regression``: the same cases, graded by an LLM judge against each case's
    ``assertion`` (plus the suite's global bars) instead of deterministic metrics, gated on the run's
    ``pass_rate``. A filtered run registers and runs its own sliced suite, since ``run_tests`` has no
    per-item filter. Opik + the harness are imported lazily so ``--help`` never needs keys or a
    network (ADR-0017 §1).
    """
    from evals.harness.test_suite import (
        SUITE_PASS_BAR,
        SuitePassRateError,
        SuiteSelectionError,
        assert_pass_rate,
        run_test_suite,
    )

    try:
        with opik_boundary():
            result = run_test_suite(case_id=case_id, difficulty=difficulty)
    except SuiteSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    pass_rate = result.pass_rate
    click.echo(
        f"evals suite: pass rate {pass_rate:.0%} (bar {SUITE_PASS_BAR:.0%}), "
        f"logged under {settings_project_name()}."
    )
    try:
        assert_pass_rate(pass_rate)
    except SuitePassRateError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.option(
    "--filter",
    "filter_string",
    default=None,
    help="Opik OQL clause scoping which threads to score (e.g. 'start_time > \"2026-07-01T00:00:00Z\"').",
)
def online(filter_string: str | None) -> None:
    """Score decode's LIVE REPL threads with one conversation-level judge (ADR-0017 §10).

    The production-eval track: instead of driving a run, it grades the traces decode ALREADY emitted
    from real sessions (ADR-0014), inside the LIVE project (``settings.opik_project_name``, NOT
    ``eval_project_name``), via ``evaluate_threads`` with a single conversation judge whose scores log
    back onto those threads. Skips friendly (no error) when keys are missing; ``--filter`` scopes the
    run to recent threads. Opik + the harness are imported lazily so ``--help`` never needs keys or a
    network (ADR-0017 §1).
    """
    from evals.harness.online import (
        format_thread_scores,
        live_project_name,
        online_keys_missing,
        run_online_eval,
    )

    missing = online_keys_missing()
    if missing:
        click.echo("evals online: skipped — set " + ", ".join(missing) + " to score live threads.")
        return

    with opik_boundary():
        result = run_online_eval(filter_string=filter_string)
    lines = format_thread_scores(result)
    if not lines:
        click.echo(f"evals online: no threads to score in {live_project_name()}.")
        return
    for line in lines:
        click.echo(line)
    click.echo(f"evals online: scored {len(lines)} thread(s) in {live_project_name()}.")


@cli.command()
@click.option(
    "--benchmark/--no-benchmark",
    "benchmark",
    default=True,
    show_default=True,
    help="Sync the benchmark tasks into the decode-benchmark-v2 Opik dataset.",
)
@click.option(
    "--regression/--no-regression",
    "regression",
    default=True,
    show_default=True,
    help="Sync the Regression Cases into the decode-regression-v2 dataset AND their Test Suite.",
)
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default=None,
    help="Sync only the tasks / cases of this difficulty tier.",
)
def sync(benchmark: bool, regression: bool, difficulty: str | None) -> None:
    """Upsert the eval tracks' Opik surfaces (ADR-0022 §6,8).

    ``--benchmark`` loads ``evals/benchmark/tasks/`` into ``decode-benchmark-v2``; ``--regression``
    loads the case registry into BOTH regression surfaces from one pass — the ``decode-regression-v2``
    dataset the metric gate scores and the ``decode-regression-suite`` Test Suite the natural-language
    assertions are judged in (both on by default). A skip-guarded case stays in the registry but is
    not registered: the Opik surfaces carry what actually runs. ``--difficulty`` slices the upsert to
    one tier, so ``make eval-regression ARGS='--difficulty hard'`` syncs and gates the same eight
    cases. Opik is imported lazily here (not at CLI build time) so ``--help`` never needs keys or a
    network.
    """
    if not benchmark and not regression:
        click.echo("evals sync: nothing selected (pass --benchmark and/or --regression).")
        return

    # Lazy import: keeps the CLI module opik-free at import time (ADR-0017 §1).
    from evals.harness.datasets import (
        BENCHMARK_DATASET_NAME,
        REGRESSION_DATASET_NAME,
        REGRESSION_SUITE_NAME,
        sync_benchmark_dataset,
        sync_regression_cases,
    )

    with opik_boundary():
        if benchmark:
            from evals.harness.benchmark import select_tasks
            from evals.harness.task_loader import load_benchmark_tasks

            tasks = select_tasks(load_benchmark_tasks(), task_id=None, difficulty=difficulty)
            sync_benchmark_dataset(tasks)
            click.echo(f"evals sync: upserted {len(tasks)} task(s) into {BENCHMARK_DATASET_NAME}.")

        if regression:
            from evals.regression.loader import load_cases, runnable_cases, select_cases

            selected = select_cases(load_cases(), difficulty=difficulty)
            cases = runnable_cases(selected)
            skipped = [case.id for case in selected if case.skip_reason is not None]
            note = f" ({len(skipped)} skipped: {', '.join(skipped)})" if skipped else ""
            sync_regression_cases(cases)
            click.echo(
                f"evals sync: upserted {len(cases)} case(s){note} into {REGRESSION_DATASET_NAME} "
                f"and {REGRESSION_SUITE_NAME}."
            )
