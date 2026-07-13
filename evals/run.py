"""The ``python -m evals`` CLI body — a Click group with the eval tracks as subcommands (ADR-0017).

Skeleton only: ``benchmark`` (outcome oracles, task 105) and ``regression`` (behavior probes, task
106) are stubbed here so the surface exists and ``--help`` works. Deliberately imports no ``opik``
at module scope — the Opik harness is pulled in lazily by the tracks that need it, so building the
CLI never needs keys or a network.
"""

from __future__ import annotations

import click


@click.group()
def cli() -> None:
    """decode eval suite — benchmark + regression harness (ADR-0017)."""


@cli.command()
def benchmark() -> None:
    """Run the outcome benchmark (lands in task 105)."""
    click.echo("evals benchmark: not implemented yet (task 105).")


@cli.command()
def regression() -> None:
    """Run the behavior regression probes (lands in task 106)."""
    click.echo("evals regression: not implemented yet (task 106).")


@cli.command()
@click.option(
    "--benchmark/--no-benchmark",
    "benchmark",
    default=True,
    show_default=True,
    help="Sync the benchmark tasks into the decode-benchmark-v1 Opik dataset.",
)
def sync(benchmark: bool) -> None:
    """Upsert the eval tracks' Opik datasets (ADR-0017 §2).

    ``--benchmark`` (on by default) loads ``evals/benchmark/tasks/`` and upserts one item per task
    into ``decode-benchmark-v1``. Opik is imported lazily here (not at CLI build time) so
    ``--help`` never needs keys or a network. Regression dataset sync lands with task 106.
    """
    if not benchmark:
        click.echo("evals sync: nothing selected (pass --benchmark).")
        return

    # Lazy import: keeps the CLI module opik-free at import time (ADR-0017 §1).
    from evals.harness.datasets import BENCHMARK_DATASET_NAME, sync_benchmark_dataset
    from evals.harness.task_loader import load_benchmark_tasks

    tasks = load_benchmark_tasks()
    sync_benchmark_dataset(tasks)
    click.echo(f"evals sync: upserted {len(tasks)} task(s) into {BENCHMARK_DATASET_NAME}.")
