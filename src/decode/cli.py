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
from decode.permissions.types import PermissionMode  # noqa: E402
from decode.tui.app import run_app  # noqa: E402

logger = logging.getLogger(__name__)

# The one friendly line shown when no Gemini key is configured, instead of the raw
# ``pydantic_ai.UserError`` traceback ``build_agent()`` would otherwise raise (ADR-0002 §1).
# Kept verbatim from task 004 for backward-compat (the default provider is still ``gemini``).
_NO_KEY_MESSAGE = (
    "Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example)."
)

# The friendly line shown when ``LLM_PROVIDER=openrouter`` is selected without its API key.
_OPENROUTER_NO_KEY_MESSAGE = (
    "Decode: LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY set in your environment or .env "
    "(see .env.example)."
)

# The friendly line shown when ``LLM_PROVIDER=modal`` has exactly one proxy token set — a
# misconfiguration (ADR-0005 §5): tokens are both-or-neither (neither = an --unauthenticated endpoint).
_MODAL_PROXY_TOKENS_MESSAGE = (
    "Decode: LLM_PROVIDER=modal proxy tokens are both-or-neither — set both MODAL_PROXY_TOKEN_ID "
    "and MODAL_PROXY_TOKEN_SECRET, or neither for an --unauthenticated endpoint (see .env.example)."
)

# The startup Agent persona when ``--agent`` is omitted (ADR-0003 §9): the full-tool build agent.
_DEFAULT_AGENT = "build"


def _provider_config_error() -> str | None:
    """Return one friendly line if the selected LLM Provider's required config is missing, else None.

    Generalizes the task-004 ``GEMINI_API_KEY``-only guard to every provider (ADR-0005 §6). Reads
    ``settings.llm_provider`` and checks only **presence** / both-or-neither shape — never
    correctness (a wrong key fails at the first model request, matching the task-004 guard). The
    cli echoes the return value to stderr and exits non-zero when it is not ``None``.

    Per provider:

    * ``gemini`` — needs ``GEMINI_API_KEY`` (unchanged ``_NO_KEY_MESSAGE``).
    * ``openrouter`` — needs ``OPENROUTER_API_KEY``.
    * ``modal`` — needs **only** ``MODAL_ENDPOINT_URL`` + ``MODAL_ENDPOINT_MODEL`` (proxy tokens are
      optional: neither set is a valid ``--unauthenticated`` endpoint). The message names only the
      absent var(s). If url + model are present, a both-or-neither check flags **exactly one** proxy
      token set as a misconfiguration.
    """
    provider = settings.llm_provider
    if provider == "gemini":
        if not settings.gemini_api_key.get_secret_value():
            return _NO_KEY_MESSAGE
        return None
    if provider == "openrouter":
        if not settings.openrouter_api_key.get_secret_value():
            return _OPENROUTER_NO_KEY_MESSAGE
        return None
    if provider == "modal":
        missing = [
            name
            for name, value in (
                ("MODAL_ENDPOINT_URL", settings.modal_endpoint_url),
                ("MODAL_ENDPOINT_MODEL", settings.modal_endpoint_model),
            )
            if not value
        ]
        if missing:
            return (
                f"Decode: LLM_PROVIDER=modal needs {' and '.join(missing)} set in your "
                "environment or .env (see .env.example)."
            )
        token_id = settings.modal_proxy_token_id.get_secret_value()
        token_secret = settings.modal_proxy_token_secret.get_secret_value()
        if bool(token_id) != bool(token_secret):
            return _MODAL_PROXY_TOKENS_MESSAGE
        return None
    return None  # defensive: the settings ``Literal`` blocks any other value upstream.


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
@click.option(
    "--mode",
    "mode",
    default=None,
    metavar="NAME",
    help="Start in this permission mode (default / plan / edit / bypass); "
    "defaults to the agent's own default mode.",
)
def cli(resume: str | None, agent: str, mode: str | None) -> None:
    """Decode — a terminal coding agent you run in your terminal."""
    logger.debug("decode starting (resume=%s, agent=%s, mode=%s)", resume, agent, mode)
    # Per-provider config startup guard (ADR-0005 §6, generalizes the task-004 GEMINI_API_KEY guard):
    # build_agent() constructs the selected provider's model, which raises a raw pydantic_ai.UserError
    # when its required config is missing. Validate the selected provider's required config *here*,
    # before any agent is built (and before --agent / --mode validation), and exit with one friendly
    # line on stderr rather than dumping a traceback at the user.
    config_error = _provider_config_error()
    if config_error is not None:
        logger.debug("provider %s misconfigured; refusing to start", settings.llm_provider)
        click.echo(config_error, err=True)
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

    # Unknown-mode startup guard (ADR-0003 §9): validate ``--mode`` against the four permission mode
    # names *before* the REPL, mirroring the no-key / unknown-agent guards — a bad mode exits with
    # one friendly line listing the valid modes instead of a traceback. ``None`` (no flag) keeps the
    # agent's own default mode, so it is not validated.
    if mode is not None:
        try:
            PermissionMode(mode.strip().lower())
        except ValueError as exc:
            valid = ", ".join(m.value for m in PermissionMode)
            logger.debug("unknown --mode %r; refusing to start", mode)
            click.echo(f"Decode: unknown mode {mode!r}; valid modes: {valid}.", err=True)
            raise click.exceptions.Exit(1) from exc

    # Launch the REPL wired to the harness; the bare ``--resume`` flag arrives as "latest", a
    # named ``--resume <id>`` as that id, and no flag as None (a fresh session). run_app loads
    # the matching session log and seeds the conversation (ADR-0002 §9, task 014) and starts with
    # the selected ``agent`` persona (ADR-0003 §7,9), in ``--mode`` if given (else the agent default).
    asyncio.run(run_app(resume=resume, agent=agent, mode=mode))


if __name__ == "__main__":
    cli()
