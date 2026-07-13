"""The ``python -m evals`` CLI body — a Click group with the eval tracks as subcommands (ADR-0017).

``benchmark`` runs the outcome benchmark (task 106); ``sync`` upserts the Opik datasets (task 105);
``regression`` (behavior probes) is still a stub. Deliberately imports no ``opik`` at module scope —
the Opik harness is pulled in lazily by the tracks that need it, so building the CLI never needs keys
or a network.
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
    type=int,
    default=None,
    help="Cap the number of dataset items sampled (Opik nb_samples).",
)
def benchmark(
    task_id: str | None, difficulty: str | None, sandbox: str, nb_samples: int | None
) -> None:
    """Run the outcome benchmark as an Opik experiment (ADR-0017 §3,4,5).

    Each selected task runs the real agent in a fresh isolated Workspace, grades it with the hidden
    ``verify.sh`` oracle, and scores the run with the code metrics (+ a single task's G-Eval judges)
    under ``settings.eval_project_name``. Opik + the harness are imported lazily so ``--help`` never
    needs keys or a network (ADR-0017 §1).
    """
    from evals.harness.benchmark import BenchmarkSelectionError, run_benchmark

    try:
        run_benchmark(
            task_id=task_id,
            difficulty=difficulty,
            sandbox=sandbox,
            nb_samples=nb_samples,
        )
    except BenchmarkSelectionError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"evals benchmark: experiment logged under {settings_project_name()}.")


def settings_project_name() -> str:
    """The Opik project eval runs log under — read lazily so the CLI stays opik/settings-light."""
    from decode.config.settings import settings

    return settings.eval_project_name


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
