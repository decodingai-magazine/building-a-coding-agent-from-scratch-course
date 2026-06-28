"""The ``decode`` CLI entrypoint.

Thin by design: it bootstraps logging, then (from task 002 onward) hands off to the
TUI + harness. ``init_logger()`` runs at module level before any other project import.
"""

from __future__ import annotations

from decode.logging import init_logger

init_logger()

import asyncio  # noqa: E402  (intentional post-logger import)
import logging  # noqa: E402
from collections.abc import Callable  # noqa: E402

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

# The friendly line shown when ``decode run`` is invoked but the Headless Runtime is disabled
# (``RUNTIME_ENABLED=false``; ADR-0008). Like the provider guard, this exits non-zero with one line
# instead of building a Durable Flow.
_RUNTIME_DISABLED_MESSAGE = (
    "Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true in your environment "
    "or .env to use `decode run` (see .env.example)."
)


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


# The single-api-key providers whose model key the Credentials Proxy resolves from a Kitaru secret
# (ADR-0008 §5), mirroring ``decode.agent.factory.PROXY_SECRET_KEY``. ``modal`` is absent: it
# authenticates with proxy *tokens* read from settings, so it is never proxied and keeps the
# settings-shape guard even when ``RUNTIME_CREDENTIALS_PROXY_ENABLED`` is on.
_PROXY_PROVIDERS: frozenset[str] = frozenset({"gemini", "openrouter"})


def _uses_credentials_proxy() -> bool:
    """True when ``decode run`` sources the model key from the Kitaru Credentials Proxy (ADR-0008 §5).

    Only the single-api-key providers (``gemini`` / ``openrouter``) are proxied; ``modal`` is never
    proxied (its proxy *tokens* live in settings), so this stays ``False`` for it. When ``False`` the
    headless path keeps the byte-identical settings-key/token guard (:func:`_provider_config_error`).
    """
    return settings.runtime_credentials_proxy_enabled and settings.llm_provider in _PROXY_PROVIDERS


def _proxy_secret_message() -> str:
    """The one friendly line when the Credentials Proxy is on but the Kitaru secret is unusable.

    Names the *real* fix — create the Kitaru secret — instead of the misleading ``set GEMINI_API_KEY``
    settings line, because with the proxy on the model key comes from Kitaru, not settings (ADR-0008 §5).
    The provider's key name comes from the factory's single-source ``PROXY_SECRET_KEY`` map.
    """
    from decode.agent.factory import PROXY_SECRET_KEY  # cheap + kitaru-free; lazy for symmetry

    secret_name = settings.runtime_secret_name
    key_name = PROXY_SECRET_KEY.get(settings.llm_provider, "GEMINI_API_KEY")
    return (
        f"Decode: RUNTIME_CREDENTIALS_PROXY_ENABLED is on but the Kitaru secret {secret_name!r} is "
        f"missing or has no {key_name} value — create it with "
        f"`kitaru secrets set {secret_name} --{key_name}=…` (see .env.example)."
    )


def _proxy_credential_error() -> str | None:
    """Validate the Credentials Proxy's Kitaru secret resolves; a friendly line if not (ADR-0008 §5).

    The proxy-aware counterpart to :func:`_provider_config_error` for ``decode run``: it resolves the
    provider key from the Kitaru secret once, up front (offline on the local stack), so a **missing**
    secret (``KitaruRuntimeError``) or a secret **lacking** the provider key (``RuntimeError``) becomes
    one friendly stderr line instead of a ~30-frame traceback from inside the flow (task 061, User
    Story #3). The throwaway resolution here is deliberate: the flow resolves the key *again* inside
    its body so the raw key never rides in the flow payload (the "secrets never reach the … payload"
    invariant). ``kitaru`` is reached only through the factory seam, which imports it lazily, so the
    REPL path never loads it.
    """
    from decode.agent.factory import resolve_provider_key_via_proxy

    try:
        resolve_provider_key_via_proxy(settings.llm_provider)  # type: ignore[arg-type]
    except RuntimeError as exc:
        logger.debug("credentials proxy secret unavailable for %s: %s", settings.llm_provider, exc)
        return _proxy_secret_message()
    return None


def _launch_durable[T](run_flow: Callable[[], T]) -> T:
    """Run a durable flow, turning a proxy credential-seam failure into one friendly line (ADR-0008 §5).

    Belt-and-braces behind :func:`_proxy_credential_error`'s pre-flight (which validates the secret
    before launch), so this only fires on a rare edge — the secret deleted between validation and
    launch, or a future non-local stack where the in-flow ``get_secret`` differs. It is scoped to an
    **engaged proxy** and to Kitaru's own ``KitaruRuntimeError`` so an unrelated flow error is never
    masked; with the proxy off the flow runs unwrapped, byte-identical to before.
    """
    if not _uses_credentials_proxy():
        return run_flow()
    from kitaru.errors import (
        KitaruRuntimeError,
    )  # kitaru is already loaded on the ``decode run`` path

    try:
        return run_flow()
    except KitaruRuntimeError as exc:
        logger.debug("credentials proxy seam failed at flow launch: %s", exc)
        click.echo(_proxy_secret_message(), err=True)
        raise click.exceptions.Exit(1) from exc


@click.group(invoke_without_command=True)
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
@click.pass_context
def cli(ctx: click.Context, resume: str | None, agent: str, mode: str | None) -> None:
    """Decode — a terminal coding agent you run in your terminal.

    Bare ``decode`` (no subcommand) launches the interactive REPL with the flags below — the
    behaviour is identical to the pre-runtime build. ``decode run "<task>"`` (ADR-0008) runs a
    single task headlessly through the durable runtime instead.
    """
    # A subcommand (e.g. ``run``) was invoked: let it handle everything. The REPL-only flags and
    # startup guards below apply solely to the bare ``decode`` REPL path, so we return before them.
    if ctx.invoked_subcommand is not None:
        return

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


@cli.command("run")
@click.argument("task")
@click.option(
    "--hitl",
    is_flag=True,
    help=(
        "Human-in-the-loop: run under a gating gate so mutating tools and ask_user pause on durable "
        "Kitaru waits resolved out-of-band (`kitaru executions input`), instead of bypassing them."
    ),
)
def run(task: str, hitl: bool) -> None:
    """Run a single TASK headlessly through the durable runtime, then print the result (ADR-0008).

    The autonomous counterpart to the REPL: ``decode run "<task>"`` builds the same agent as the
    TUI but drives it through a Kitaru Durable Flow (checkpoints + replay). Two modes:

    \b
    * default — **bypass** (task 058): every tool runs inline with no prompt; the agent's final text
      is printed to stdout. ``ask_user`` / approvals are headless no-ops.
    * ``--hitl`` — **human-in-the-loop** (task 059): a gating gate so ``write`` / ``edit`` / ``bash``
      and ``ask_user`` pause the whole execution on a durable wait. If a wait is resolved out-of-band
      while the run polls it continues and prints the result; otherwise the run pauses and prints the
      execution id + the ``kitaru executions input`` command to resolve it. The poll timeout differs
      by wait kind (a known limitation — decode does not fork the adapter): the ``ask_user`` /
      ``exit_plan_mode`` answer waits honor ``runtime_wait_timeout_s``; the native ``write`` /
      ``edit`` / ``bash`` **approval** waits use the adapter's fixed ``600s`` default (ADR-0008 §3).

    Guards (same friendly-line-on-stderr, non-zero-exit contract as the REPL): the per-provider
    config guard (it builds a model) fires first, then ``RUNTIME_ENABLED`` — a disabled runtime never
    builds a flow. When the **Credentials Proxy** is on (ADR-0008 §5) the model key comes from a Kitaru
    secret, not settings, so the settings-key guard is replaced by a proxy-aware pre-flight that
    validates the secret resolves before any flow is built — a missing/incomplete secret is one
    friendly line, never a traceback. ``kitaru`` is imported lazily here so the REPL path never loads it.
    """
    # Per-provider config guard. Proxy-aware (ADR-0008 §5): for proxy-OFF and for modal (never
    # proxied), this is the byte-identical settings-key/token guard. A proxied provider under the
    # proxy validates the Kitaru secret instead — deferred to the pre-flight below, after the runtime
    # guard, because that lookup boots Kitaru and a disabled runtime must short-circuit first.
    if not _uses_credentials_proxy():
        config_error = _provider_config_error()
        if config_error is not None:
            logger.debug("provider %s misconfigured; refusing to run", settings.llm_provider)
            click.echo(config_error, err=True)
            raise click.exceptions.Exit(1)

    if not settings.runtime_enabled:
        logger.debug("runtime disabled; refusing to run")
        click.echo(_RUNTIME_DISABLED_MESSAGE, err=True)
        raise click.exceptions.Exit(1)

    # Credentials-proxy pre-flight (ADR-0008 §5): validate the Kitaru secret resolves BEFORE building
    # a flow, so a missing/incomplete secret exits with one friendly line (naming ``kitaru secrets
    # set``) instead of a ~30-frame KitaruRuntimeError traceback from inside the flow body. Boots
    # Kitaru, hence after the runtime guard. Applies to both the bypass and ``--hitl`` paths.
    if _uses_credentials_proxy():
        credential_error = _proxy_credential_error()
        if credential_error is not None:
            click.echo(credential_error, err=True)
            raise click.exceptions.Exit(1)

    # Lazy import: keep kitaru (and its heavy zenml/temporalio stack) off the REPL path entirely —
    # only ``decode run`` pays the import cost. The flow runs on the local Kitaru stack, offline.
    if hitl:
        _run_hitl(task)
        return

    from decode.runtime import run_agent_task

    logger.debug("decode run starting (task=%r)", task)
    # ``flow.run(...).wait()`` returns the flow's terminal checkpoint — a pydantic-ai
    # ``AgentRunResult`` — so surface its ``.output`` text (the flow's str return is not what Kitaru
    # hands back). ``getattr`` keeps it robust if a future Kitaru version returns the str directly.
    # ``_launch_durable`` is the belt-and-braces credential-seam safety net (a no-op when proxy off).
    result = _launch_durable(lambda: run_agent_task.run(task=task).wait())
    click.echo(getattr(result, "output", result))


def _run_hitl(task: str) -> None:
    """Drive the HITL Durable Flow and print the result, or the pause + how to resolve it (ADR-0008 §3).

    A finished run prints the agent's final text. A run that paused on an unresolved durable wait
    prints the execution id and the ``kitaru executions`` commands an operator uses to inspect and
    resolve it out-of-band, then resume — exit stays zero (a pause is a normal HITL outcome, not an
    error).
    """
    from decode.runtime import run_hitl_agent_task

    logger.debug("decode run --hitl starting (task=%r)", task)
    # ``_launch_durable`` is the belt-and-braces credential-seam safety net behind the pre-flight in
    # ``run`` (a no-op when the proxy is off): a missing-secret edge becomes one friendly line, not a
    # raw traceback, and never the ``is_finished``/``is_successful`` paused mis-report.
    result = _launch_durable(lambda: run_hitl_agent_task(task))
    if result.paused:
        click.echo(
            f"Decode: the task paused on a durable human-in-the-loop wait (execution "
            f"{result.exec_id}). Resolve it out-of-band, then resume:",
            err=True,
        )
        click.echo("  kitaru executions list", err=True)
        click.echo(
            f"  kitaru executions input {result.exec_id} --wait <name> --value '<answer>'", err=True
        )
        click.echo(f"  kitaru executions resume {result.exec_id}", err=True)
        return
    click.echo(result.output)


if __name__ == "__main__":
    cli()
