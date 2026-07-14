"""The ``python -m evals`` CLI body — a Click group with the eval tracks as subcommands (ADR-0017).

``benchmark`` runs the outcome benchmark (task 106); ``regression`` runs the behavior probes host-native
(task 111); ``sync`` upserts the Opik datasets (tasks 105, 111). Deliberately imports no ``opik`` at
module scope — the Opik harness is pulled in lazily by the tracks that need it, so building the CLI never
needs keys or a network.
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
    help="The sandbox rung each task run executes in.",
)
@click.option(
    "--nb-samples",
    type=click.IntRange(min=1),
    default=None,
    help="Cap the number of dataset items sampled (Opik nb_samples).",
)
@click.option(
    "--trials",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Runs per item (Opik trial_count) — drives pass@k / pass^k / flakiness aggregates.",
)
def benchmark(
    task_id: str | None,
    difficulty: str | None,
    sandbox: str,
    nb_samples: int | None,
    trials: int,
) -> None:
    """Run the outcome benchmark as an Opik experiment (ADR-0017 §3,4,5,8).

    Each selected task runs the real agent in a fresh isolated Workspace ``--trials`` times, grades
    each run with the hidden ``verify.sh`` oracle, and scores it with the code metrics (+ a single
    task's G-Eval judges) under ``settings.eval_project_name``. The trial aggregates
    (pass@1 / pass@k / pass^k / flakiness + cost) are attached to the experiment and printed as a Rich
    summary table. Opik + the harness are imported lazily so ``--help`` never needs keys or a
    network (ADR-0017 §1).
    """
    from rich.console import Console

    from evals.harness.aggregates import render_summary_table, summarize
    from evals.harness.benchmark import BenchmarkSelectionError, run_benchmark

    try:
        with opik_boundary():
            result = run_benchmark(
                task_id=task_id,
                difficulty=difficulty,
                sandbox=sandbox,
                nb_samples=nb_samples,
                trials=trials,
            )
    except BenchmarkSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    Console().print(render_summary_table(summarize(result, trials=trials)))
    click.echo(f"evals benchmark: experiment logged under {settings_project_name()}.")


def settings_project_name() -> str:
    """The Opik project eval runs log under — read lazily so the CLI stays opik/settings-light."""
    from decode.config.settings import settings

    return settings.eval_project_name


@cli.command()
@click.option("--probe", "probe_id", default=None, help="Run only this regression probe id.")
def regression(probe_id: str | None) -> None:
    """Run the behavior regression probes host-native as an Opik experiment (ADR-0017 §3,4,6).

    Each selected probe seeds a fresh temp Workspace, runs the real agent HOST-NATIVE (``none`` mode —
    no docker) under the probe's gate policy, and scores its behavior with the probe's metrics under
    ``settings.eval_project_name``. Opik + the harness are imported lazily so ``--help`` never needs keys
    or a network (ADR-0017 §1).
    """
    from evals.harness.regression import RegressionSelectionError, run_regression

    try:
        with opik_boundary():
            run_regression(probe_id=probe_id)
    except RegressionSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"evals regression: experiment logged under {settings_project_name()}.")


@cli.command()
def suite() -> None:
    """Run the Opik 2.0 Test Suite regression surface — natural-language assertions (ADR-0017 §6).

    The CONTRAST to ``regression``: instead of deterministic code metrics + a threshold gate, a small
    subset of the most judge-flavored probes is graded against natural-language quality bars by an LLM
    judge, and the run is gated on its ``pass_rate``. Needs opik>=2.0; on the pinned opik 1.9.8 this
    exits with a clear version-gate message (task 116). Opik + the harness are imported lazily so
    ``--help`` never needs keys or a network (ADR-0017 §1).
    """
    from evals.harness.test_suite import (
        SUITE_PASS_BAR,
        SuitePassRateError,
        SuiteSelectionError,
        SuiteUnavailableError,
        assert_pass_rate,
        run_test_suite,
    )

    try:
        with opik_boundary():
            result = run_test_suite()
    except (SuiteUnavailableError, SuiteSelectionError) as exc:
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
    help="Sync the benchmark tasks into the decode-benchmark-v1 Opik dataset.",
)
@click.option(
    "--regression/--no-regression",
    "regression",
    default=True,
    show_default=True,
    help="Sync the behavior probes into the decode-regression-v1 Opik dataset.",
)
def sync(benchmark: bool, regression: bool) -> None:
    """Upsert the eval tracks' Opik datasets (ADR-0017 §2,6).

    ``--benchmark`` loads ``evals/benchmark/tasks/`` into ``decode-benchmark-v1``; ``--regression``
    loads the probe registry into ``decode-regression-v1`` (both on by default). Opik is imported lazily
    here (not at CLI build time) so ``--help`` never needs keys or a network.
    """
    if not benchmark and not regression:
        click.echo("evals sync: nothing selected (pass --benchmark and/or --regression).")
        return

    # Lazy import: keeps the CLI module opik-free at import time (ADR-0017 §1).
    from evals.harness.datasets import (
        BENCHMARK_DATASET_NAME,
        REGRESSION_DATASET_NAME,
        sync_benchmark_dataset,
        sync_regression_dataset,
    )

    with opik_boundary():
        if benchmark:
            from evals.harness.task_loader import load_benchmark_tasks

            tasks = load_benchmark_tasks()
            sync_benchmark_dataset(tasks)
            click.echo(f"evals sync: upserted {len(tasks)} task(s) into {BENCHMARK_DATASET_NAME}.")

        if regression:
            from evals.regression.loader import load_probes

            probes = load_probes()
            sync_regression_dataset(probes)
            click.echo(
                f"evals sync: upserted {len(probes)} probe(s) into {REGRESSION_DATASET_NAME}."
            )
