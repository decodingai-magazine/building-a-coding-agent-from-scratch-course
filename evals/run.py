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
