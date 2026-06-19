"""The ``decode`` CLI entrypoint.

Thin by design: it bootstraps logging, then (from task 002 onward) hands off to the
TUI + harness. ``init_logger()`` runs at module level before any other project import.
"""

from __future__ import annotations

from decode.logging import init_logger

init_logger()

import asyncio  # noqa: E402  (intentional post-logger import)
import logging  # noqa: E402

import click  # noqa: E402

from decode.tui.app import run_app  # noqa: E402

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--resume",
    is_flag=False,
    flag_value="latest",
    default=None,
    metavar="[SESSION]",
    help="Resume the latest session, or a named session id / filename.",
)
def cli(resume: str | None) -> None:
    """decode — a terminal coding agent you run in your terminal."""
    logger.debug("decode starting (resume=%s)", resume)
    # Launch the REPL wired to the harness; the bare ``--resume`` flag arrives as "latest", a
    # named ``--resume <id>`` as that id, and no flag as None (a fresh session). run_app loads
    # the matching session log and seeds the conversation (ADR-0002 §9, task 014).
    asyncio.run(run_app(resume=resume))


if __name__ == "__main__":
    cli()
