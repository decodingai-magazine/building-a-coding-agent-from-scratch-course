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

from decode.agents.loader import load_agent  # noqa: E402
from decode.config.settings import settings  # noqa: E402
from decode.tui.app import run_app  # noqa: E402

logger = logging.getLogger(__name__)

# The one friendly line shown when no Gemini key is configured, instead of the raw
# ``pydantic_ai.UserError`` traceback ``build_agent()`` would otherwise raise (ADR-0002 §1).
_NO_KEY_MESSAGE = (
    "Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example)."
)

# The startup Agent persona when ``--agent`` is omitted (ADR-0003 §9): the full-tool build agent.
_DEFAULT_AGENT = "build"


@click.command()
@click.option(
    "--resume",
    is_flag=False,
    flag_value="latest",
    default=None,
    metavar="[SESSION]",
    help="Resume the latest session, or a named session id / filename.",
)
@click.option(
    "--agent",
    "agent",
    default=_DEFAULT_AGENT,
    metavar="NAME",
    help="Start with this agent persona (build / plan / explore / code-reviewer).",
)
def cli(resume: str | None, agent: str) -> None:
    """Decode — a terminal coding agent you run in your terminal."""
    logger.debug("decode starting (resume=%s, agent=%s)", resume, agent)
    # No-key startup guard (task 004 carryover): build_agent() constructs the Gemini provider,
    # which raises a raw pydantic_ai.UserError (mentioning GOOGLE_API_KEY — the wrong var for
    # this project) when no key is set. Catch it *here*, before any agent is built, and exit
    # with one friendly line on stderr rather than dumping a traceback at the user.
    if not settings.gemini_api_key.get_secret_value():
        logger.debug("no GEMINI_API_KEY configured; refusing to start")
        click.echo(_NO_KEY_MESSAGE, err=True)
        raise click.exceptions.Exit(1)

    # Unknown-agent startup guard (ADR-0003 §9): validate ``--agent`` against the catalog *before*
    # the REPL so a bad name exits with one friendly line (listing the available agents) instead of
    # a traceback — mirroring the no-key guard. ``load_agent`` raises ValueError naming the choices.
    try:
        load_agent(agent)
    except ValueError as exc:
        logger.debug("unknown --agent %r; refusing to start", agent)
        click.echo(f"Decode: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc

    # Launch the REPL wired to the harness; the bare ``--resume`` flag arrives as "latest", a
    # named ``--resume <id>`` as that id, and no flag as None (a fresh session). run_app loads
    # the matching session log and seeds the conversation (ADR-0002 §9, task 014) and starts with
    # the selected ``agent`` persona (ADR-0003 §7,9).
    asyncio.run(run_app(resume=resume, agent=agent))


if __name__ == "__main__":
    cli()
