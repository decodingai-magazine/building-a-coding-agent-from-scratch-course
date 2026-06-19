"""The ``decode`` CLI entrypoint.

Thin by design: it bootstraps logging, then (from task 002 onward) hands off to the
TUI + harness. ``init_logger()`` runs at module level before any other project import.
"""

from __future__ import annotations

from decode.logging import init_logger

init_logger()

import logging  # noqa: E402  (intentional post-logger import)

import click  # noqa: E402

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--resume",
    is_flag=False,
    flag_value="latest",
    default=None,
    metavar="[SESSION]",
    help="Resume the latest session, or a named session id. (Wired in task 014.)",
)
def cli(resume: str | None) -> None:
    """decode — a terminal coding agent you run in your terminal."""
    logger.debug("decode starting (resume=%s)", resume)
    # M1 bootstrap milestone: the interactive REPL + agent loop arrive in tasks 002+.
    click.echo("decode: bootstrap OK — interactive REPL lands in task 002.")


if __name__ == "__main__":
    cli()
