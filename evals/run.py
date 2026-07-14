"""The ``python -m evals`` CLI body — a Click group with the eval tracks as subcommands (ADR-0017).

``benchmark`` runs the outcome benchmark (task 106); ``regression`` runs the behavior probes host-native
(task 111); ``sync`` upserts the Opik datasets (tasks 105, 111). Deliberately imports no ``opik`` at
module scope — the Opik harness is pulled in lazily by the tracks that need it, so building the CLI never
needs keys or a network.
"""

from __future__ import annotations

import click


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
        run_regression(probe_id=probe_id)
    except RegressionSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"evals regression: experiment logged under {settings_project_name()}.")


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

    if benchmark:
        from evals.harness.task_loader import load_benchmark_tasks

        tasks = load_benchmark_tasks()
        sync_benchmark_dataset(tasks)
        click.echo(f"evals sync: upserted {len(tasks)} task(s) into {BENCHMARK_DATASET_NAME}.")

    if regression:
        from evals.regression.loader import load_probes

        probes = load_probes()
        sync_regression_dataset(probes)
        click.echo(f"evals sync: upserted {len(probes)} probe(s) into {REGRESSION_DATASET_NAME}.")
