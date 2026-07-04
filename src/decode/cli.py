"""The ``decode`` CLI entrypoint.

Thin by design: it bootstraps logging, then (from task 002 onward) hands off to the
TUI + harness. ``init_logger()`` runs at module level before any other project import.
"""

from __future__ import annotations

from decode.logging import init_logger

init_logger()

import asyncio  # noqa: E402  (intentional post-logger import)
import logging  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

import click  # noqa: E402
from pydantic import ValidationError  # noqa: E402

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

# The friendly line shown when ``SANDBOX_MODE=docker`` is selected but the Docker daemon is not
# reachable (ADR-0011 §1). Like the provider guards this is presence/reachability only — a running
# daemon is required, not a *correct* image — and it fires in both the REPL and the headless pre-flight.
_SANDBOX_DOCKER_UNREACHABLE_MESSAGE = (
    "Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable — start Docker and retry "
    "(see .env.example)."
)

# The friendly line shown when ``SANDBOX_MODE=modal`` is selected but Modal account credentials are
# absent (ADR-0011 §1). Presence only — no network call, no ``modal`` import: a wrong token fails at
# the first sandbox call, matching the provider-key guards.
_SANDBOX_MODAL_NO_CREDENTIALS_MESSAGE = (
    "Decode: SANDBOX_MODE=modal but Modal credentials are missing — run `modal token set …` "
    "(see .env.example)."
)

# The friendly line shown when a Workspace repo is requested (``--repo`` or ``SANDBOX_REPO``) while
# ``SANDBOX_MODE=none`` (ADR-0012 §3). The clone-at-launch only makes sense in a sandbox mode — the
# isolated Workspace does not exist in ``none`` — so this is a config error, refused the task-004 way
# (one stderr line, non-zero exit, no traceback) in BOTH the REPL startup and the headless pre-flight.
_SANDBOX_REPO_NONE_MODE_MESSAGE = (
    "Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace, which only exists "
    "in a sandbox mode — set SANDBOX_MODE=docker or SANDBOX_MODE=modal, or drop --repo/SANDBOX_REPO "
    "(see .env.example)."
)

# How long the ``docker info`` daemon-reachability probe may run before it is treated as unreachable.
# Deliberately short — a healthy local daemon answers near-instantly, and startup must not hang on it.
_DOCKER_PROBE_TIMEOUT_S = 5.0

# The startup Agent persona when ``--agent`` is omitted (ADR-0003 §9): the full-tool build agent.
_DEFAULT_AGENT = "build"

# The friendly line shown when ``decode run`` is invoked but the Headless Runtime is disabled
# (``RUNTIME_ENABLED=false``; ADR-0008). Like the provider guard, this exits non-zero with one line
# instead of building a Durable Flow.
_RUNTIME_DISABLED_MESSAGE = (
    "Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true in your environment "
    "or .env to use `decode run` (see .env.example)."
)

# The friendly line shown when ``decode replay`` is invoked without ``--from``. Kitaru's replay
# REQUIRES an anchor — ``from_`` is a required argument with no default (verified on kitaru 0.18:
# omitting it is a ``TypeError``, ``from_=None`` an ``AttributeError``). decode mirrors that exactly and
# invents **no** default anchor (ADR-0010 §5); it surfaces Kitaru's own requirement and points at how to
# find a checkpoint to anchor on.
_REPLAY_NO_FROM_MESSAGE = (
    "Decode: `decode replay` needs --from <checkpoint> — Kitaru replay requires an explicit anchor "
    "(it has no default). List a recorded run's checkpoints with `kitaru executions get <exec_id>` and "
    "pass one as --from (e.g. an early `*_model_request` step to swap the model for the whole run)."
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


def _docker_daemon_reachable() -> bool:
    """True if the local Docker daemon answers a fast ``docker info`` probe (ADR-0011 §1).

    Shells out to the standard ``docker`` CLI (dependency-free, mirroring the sandbox executors'
    CLI-over-SDK choice) rather than importing the docker SDK. Reachability, not correctness: a
    missing binary (:class:`FileNotFoundError`), a non-zero exit (daemon down), or a probe that
    overruns :data:`_DOCKER_PROBE_TIMEOUT_S` (:class:`subprocess.TimeoutExpired`) all mean "not
    reachable" — never a crash. No network beyond the local daemon socket; output is discarded.
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=_DOCKER_PROBE_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _modal_credentials_present() -> bool:
    """True if Modal account credentials are present, WITHOUT a network call or a ``modal`` import (ADR-0011 §1).

    Presence only, checked exactly the way the modal CLI itself resolves auth: the
    ``MODAL_TOKEN_ID`` + ``MODAL_TOKEN_SECRET`` account-token pair in the environment (distinct from
    the endpoint/proxy tokens in ``settings`` — these are read straight from ``os.environ`` because
    they belong to the modal CLI, not to decode config), or a ``~/.modal.toml`` written by
    ``modal token set``. Correctness is not checked here — a bad token fails at the first sandbox
    call, matching the provider-key guards.
    """
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return (Path.home() / ".modal.toml").exists()


def _sandbox_config_error() -> str | None:
    """Return one friendly line if the selected Sandbox Mode's backend is unavailable, else None (ADR-0011 §1).

    The sandbox counterpart to :func:`_provider_config_error`, wired into both the REPL startup chain
    and the headless ``decode run``/``replay`` pre-flight so an unavailable backend is refused the same
    friendly way — one stderr line, non-zero exit, never a traceback. Presence/reachability only, never
    correctness (matching the provider-key guards):

    * ``none`` — always ``None``; the default ``LocalExecutor`` path is untouched and **no probe runs**.
    * ``docker`` — a fast :func:`_docker_daemon_reachable` probe; unreachable → the friendly docker line.
    * ``modal`` — :func:`_modal_credentials_present` (env token pair or ``~/.modal.toml``); absent → the
      friendly modal line. No network call, no ``modal`` import.
    """
    mode = settings.sandbox_mode
    if mode == "docker":
        if not _docker_daemon_reachable():
            return _SANDBOX_DOCKER_UNREACHABLE_MESSAGE
        return None
    if mode == "modal":
        if not _modal_credentials_present():
            return _SANDBOX_MODAL_NO_CREDENTIALS_MESSAGE
        return None
    return None  # ``none`` (default): no probe, byte-identical to today's LocalExecutor path.


def _resolve_sandbox_repo(repo_flag: str | None) -> str | None:
    """Resolve the Workspace source repo: ``--repo`` flag > ``SANDBOX_REPO`` > None (ADR-0012 §3).

    The single resolution point shared by the REPL and the headless entrypoints, so the precedence
    can never drift. ``repo_flag`` is the ``--repo`` value (``None`` when the flag is absent, e.g. the
    ``decode replay`` path which has no ``--repo``); the ``SANDBOX_REPO`` setting is the fallback.
    Returns the resolved source (a URL or a local path) or ``None`` for "no repo → an empty Workspace".
    An empty ``SANDBOX_REPO`` (the default ``""``) is treated as unset.
    """
    return repo_flag or settings.sandbox_repo or None


def _sandbox_repo_config_error(repo: str | None) -> str | None:
    """One friendly line if a repo is requested while ``SANDBOX_MODE=none``, else None (ADR-0012 §3).

    A ``--repo`` / ``SANDBOX_REPO`` clones a repo into the isolated Workspace, which only exists in a
    sandbox mode — so a resolved repo with ``sandbox_mode == "none"`` is a contradictory config,
    refused the task-004 way (the caller echoes this to stderr and exits non-zero). ``repo`` is the
    already-resolved source from :func:`_resolve_sandbox_repo`; ``None`` (no repo) is always fine.
    """
    if repo is not None and settings.sandbox_mode == "none":
        return _SANDBOX_REPO_NONE_MODE_MESSAGE
    return None


def _uses_credentials_proxy() -> bool:
    """True when ``decode run`` sources the model key from the Kitaru Credentials Proxy (ADR-0008 §5).

    Only the single-api-key providers (``gemini`` / ``openrouter``) are proxied; ``modal`` is never
    proxied (its proxy *tokens* live in settings), so this stays ``False`` for it. When ``False`` the
    headless path keeps the byte-identical settings-key/token guard (:func:`_provider_config_error`).
    The proxied providers are exactly the keys of the factory's single-source ``PROXY_SECRET_KEY`` map
    (imported lazily — cheap + kitaru-free — to keep it off the REPL path).
    """
    from decode.agent.factory import PROXY_SECRET_KEY  # cheap + kitaru-free; lazy for symmetry

    return settings.runtime_credentials_proxy_enabled and settings.llm_provider in PROXY_SECRET_KEY


def _proxy_credential_error() -> str | None:
    """Validate the Credentials Proxy's Kitaru secret resolves; a friendly line if not (ADR-0008 §5).

    The proxy-aware counterpart to :func:`_provider_config_error` for ``decode run``: it resolves the
    provider key from the Kitaru secret once, up front (offline on the local stack), so a **missing**
    secret (``KitaruRuntimeError``) or a secret **lacking** the provider key (``RuntimeError``) becomes
    one friendly stderr line instead of a ~30-frame traceback from inside the flow (task 061, User
    Story #3). The line names the *real* fix — create the Kitaru secret — not the misleading ``set
    GEMINI_API_KEY`` settings line, because with the proxy on the model key comes from Kitaru, not
    settings; the provider's key name comes from the factory's single-source ``PROXY_SECRET_KEY`` map.
    The throwaway resolution here is deliberate: the flow resolves the key *again* inside its body so
    the raw key never rides in the flow payload (the "secrets never reach the … payload" invariant).
    ``kitaru`` is reached only through the factory seam, which imports it lazily, so the REPL path never
    loads it.
    """
    from decode.agent.factory import PROXY_SECRET_KEY, resolve_provider_key_via_proxy

    try:
        resolve_provider_key_via_proxy(settings.llm_provider)  # type: ignore[arg-type]
    except RuntimeError as exc:
        logger.debug("credentials proxy secret unavailable for %s: %s", settings.llm_provider, exc)
        secret_name = settings.runtime_secret_name
        key_name = PROXY_SECRET_KEY.get(settings.llm_provider, "GEMINI_API_KEY")
        return (
            f"Decode: RUNTIME_CREDENTIALS_PROXY_ENABLED is on but the Kitaru secret {secret_name!r} is "
            f"missing or has no {key_name} value — create it with "
            f"`kitaru secrets set {secret_name} --{key_name}=…` (see .env.example)."
        )
    return None


def _secret_store_config_error() -> str | None:
    """Hydrate + validate the secret-store config before the flow; one friendly line if it fails (ADR-0008 §5).

    The secret-store-config counterpart to :func:`_proxy_credential_error` for ``decode run`` (task
    064). When ``settings.runtime_secret_store_config`` is on, the whole provider config (provider,
    model, key, tuning) is hydrated from the Kitaru secret — but *inside the flow*, after this cli's
    provider-config guard has already run. Without this, two symptoms follow: (1) a key living **only**
    in the secret trips the misleading ``set GEMINI_API_KEY`` line because the guard saw the
    un-hydrated settings; and (2) a missing/malformed secret blows up as a ~30-frame traceback from
    inside the flow body. This reuses the flow's own
    :func:`~decode.runtime.flow._config_from_secret_store` context to hydrate the ``settings``
    singleton up front, runs the provider-config guard against THAT hydrated config (so a secret-only
    key satisfies it), and converts a missing/malformed secret (``KitaruRuntimeError`` ⊂
    ``RuntimeError``, or a pydantic ``ValidationError`` from a bad stored value) into one friendly
    stderr line naming the real fix. The context restores the singleton on exit and the flow
    re-hydrates idempotently in its own body. With the Credentials Proxy also on the settings-key guard
    is skipped here (the key comes from the secret via the proxy, whose pre-flight names the right fix)
    — so the two never emit conflicting lines. ``kitaru`` is reached only through that lazily-imported
    context, so the REPL path never loads it.
    """
    from decode.runtime.flow import _config_from_secret_store

    # Read the secret name from the pre-hydration singleton — that is the name the source fetches with,
    # so the error line names the secret the operator must actually create/repair.
    secret_name = settings.runtime_secret_name
    try:
        with _config_from_secret_store():
            # Inside the context ``settings`` is hydrated from the Kitaru secret. Validate the hydrated
            # provider config — but only when the proxy is off; with it on the key comes from the secret
            # via the proxy pre-flight (which names the right fix), so defer to it rather than risk a
            # misleading settings-key line here.
            if _uses_credentials_proxy():
                return None
            return _provider_config_error()
    except (RuntimeError, ValidationError) as exc:
        logger.debug("secret-store config secret %r missing or invalid: %s", secret_name, exc)
        return (
            f"Decode: RUNTIME_SECRET_STORE_CONFIG is on but the Kitaru secret {secret_name!r} could "
            f"not be loaded (it is missing, or a stored value is invalid) — create or repair it with "
            f"`kitaru secrets set {secret_name} --LLM_PROVIDER=… --GEMINI_API_KEY=…` (see .env.example)."
        )


def _runtime_config_preflight(repo: str | None = None) -> str | None:
    """The shared headless guard chain for ``decode run`` / ``decode replay``; a friendly line or None.

    Both headless entrypoints (``run`` builds a flow; ``replay`` re-executes downstream model calls, so
    it needs the same valid provider config) run this identical ordered chain before touching kitaru,
    returning the **first** friendly error line — or ``None`` when all pass. Order is load-bearing;
    task 071 inserted the sandbox guard (step 3) into task 069's original chain, task 082 the repo guard:

    1. **Per-provider config guard** (it builds a model) — skipped when a kitaru-backed source supplies
       the config: the Credentials Proxy (key from a secret) or the secret-store config source (whole
       config from a secret), because both are validated in a pre-flight *after* the runtime guard (they
       boot Kitaru, and a disabled runtime must short-circuit first). For everything else it is the
       byte-identical settings-key/token guard.
    2. **``RUNTIME_ENABLED``** — a disabled runtime never builds/replays a flow.
    3. **Sandbox backend guard** (``SANDBOX_MODE``, ADR-0011 §1) — when docker/modal, refuse if the
       backend is unavailable (docker daemon down / modal creds absent). Presence/reachability only and
       kitaru-free; ``none`` (the default) runs no probe. Placed after the runtime gate (a disabled
       runtime still short-circuits first) and before the kitaru-backed pre-flights (it touches no secret).
    3b. **Sandbox-repo guard** (``--repo`` / ``SANDBOX_REPO``, ADR-0012 §3) — a Workspace repo requested
       while ``SANDBOX_MODE=none`` is a contradictory config; refused here too (pure config, no kitaru).
       ``repo`` is the ``--repo`` flag value (``None`` for ``decode replay``, which has no such flag); it
       is resolved against ``SANDBOX_REPO`` inside, so a bare ``SANDBOX_REPO`` in ``none`` mode still trips.
    4. **Secret-store config pre-flight** (``RUNTIME_SECRET_STORE_CONFIG``) — hydrate + validate the whole
       config from the Kitaru secret up front. Runs before the proxy pre-flight so, with both on, the
       proxy resolves its key from the now-hydrated secret — one coherent path, never two conflicting lines.
    5. **Credentials-proxy pre-flight** — validate the Kitaru secret resolves before building a flow.

    The caller echoes the return value to stderr and exits non-zero when it is not ``None``. ``kitaru`` is
    reached only through the two pre-flights' lazily imported seams, so the REPL path never loads it.
    """
    secret_store_on = settings.runtime_secret_store_config

    if not _uses_credentials_proxy() and not secret_store_on:
        config_error = _provider_config_error()
        if config_error is not None:
            logger.debug("provider %s misconfigured; refusing to run", settings.llm_provider)
            return config_error

    if not settings.runtime_enabled:
        logger.debug("runtime disabled; refusing to run")
        return _RUNTIME_DISABLED_MESSAGE

    sandbox_error = _sandbox_config_error()
    if sandbox_error is not None:
        logger.debug("sandbox backend %s unavailable; refusing to run", settings.sandbox_mode)
        return sandbox_error

    repo_error = _sandbox_repo_config_error(_resolve_sandbox_repo(repo))
    if repo_error is not None:
        logger.debug("sandbox repo requested in none mode; refusing to run")
        return repo_error

    if secret_store_on:
        secret_store_error = _secret_store_config_error()
        if secret_store_error is not None:
            return secret_store_error

    if _uses_credentials_proxy():
        credential_error = _proxy_credential_error()
        if credential_error is not None:
            return credential_error

    return None


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
@click.option(
    "--repo",
    "repo",
    default=None,
    metavar="URL-OR-PATH",
    help="Clone this repo (a URL or a local path) into the isolated sandbox Workspace at launch; "
    "overrides SANDBOX_REPO. Requires a sandbox mode (SANDBOX_MODE=docker|modal).",
)
@click.option(
    "--local",
    "local",
    is_flag=True,
    help="Use a fast local clone (git clone --local) when --repo is a local path.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    resume: str | None,
    agent: str,
    mode: str | None,
    repo: str | None,
    local: bool,
) -> None:
    """Decode — a terminal coding agent you run in your terminal.

    Bare ``decode`` (no subcommand) launches the interactive REPL with the flags below — the
    behaviour is identical to the pre-runtime build. ``decode run "<task>"`` (ADR-0008) runs a
    single task headlessly through the durable runtime instead.

    In a sandbox mode ``--repo <url-or-local-path>`` clones a repo into the isolated Workspace at
    launch (overriding ``SANDBOX_REPO``); ``--local`` picks a fast local clone (ADR-0012 §3).
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

    # Sandbox backend startup guard (ADR-0011 §1): when ``SANDBOX_MODE`` is docker/modal, refuse to
    # start if the chosen backend is unavailable (docker daemon unreachable / modal creds absent) with
    # one friendly line instead of failing later on the first ``bash`` call. Presence, not correctness;
    # ``none`` (the default) runs no probe, so this is a no-op for every existing setup.
    sandbox_error = _sandbox_config_error()
    if sandbox_error is not None:
        logger.debug("sandbox backend %s unavailable; refusing to start", settings.sandbox_mode)
        click.echo(sandbox_error, err=True)
        raise click.exceptions.Exit(1)

    # Sandbox-repo startup guard (ADR-0012 §3): a Workspace clone (``--repo`` / ``SANDBOX_REPO``) only
    # makes sense in a sandbox mode — the isolated Workspace does not exist in ``none`` — so a resolved
    # repo with ``SANDBOX_MODE=none`` is a config error, refused with one friendly line (no traceback).
    resolved_repo = _resolve_sandbox_repo(repo)
    repo_error = _sandbox_repo_config_error(resolved_repo)
    if repo_error is not None:
        logger.debug("sandbox repo requested in none mode; refusing to start")
        click.echo(repo_error, err=True)
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
    # ``resolved_repo`` / ``local`` thread the sandbox Workspace clone-at-launch (ADR-0012 §3); in
    # ``none`` mode ``resolved_repo`` is guaranteed ``None`` (the guard above) so the REPL is unchanged.
    asyncio.run(run_app(resume=resume, agent=agent, mode=mode, repo=resolved_repo, local=local))


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
@click.option(
    "--model",
    "model",
    default=None,
    metavar="ID",
    help=(
        "Override the active provider's model id for this run (e.g. gemini-2.5-pro); defaults to the "
        "provider's configured model. Does not change the provider (set LLM_PROVIDER for that)."
    ),
)
@click.option(
    "--repo",
    "repo",
    default=None,
    metavar="URL-OR-PATH",
    help="Clone this repo (a URL or a local path) into the isolated sandbox Workspace; overrides "
    "SANDBOX_REPO. Requires a sandbox mode (SANDBOX_MODE=docker|modal).",
)
@click.option(
    "--local",
    "local",
    is_flag=True,
    help="Use a fast local clone (git clone --local) when --repo is a local path.",
)
def run(task: str, hitl: bool, model: str | None, repo: str | None, local: bool) -> None:
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

    ``--model ID`` overrides the active provider's model id for this run — the provider itself stays
    selected by ``LLM_PROVIDER`` (no cross-provider swap). It rides through as a durable **flow input**,
    so a later ``decode replay`` can swap it to ask what a different model would have done (ADR-0010
    §2,4). Presence, not correctness: a model id wrong for the provider is not validated here (matching
    the key guards) — it fails at the first model request.

    After a bypass run the agent's answer prints on **stdout** while the durable ``exec_id`` and a
    paste-ready ``decode replay`` hint print on **stderr** — so stdout stays clean for piping and the
    checkpoint→replay loop is discoverable from the terminal (ADR-0010 §4).

    Guards (same friendly-line-on-stderr, non-zero-exit contract as the REPL): the per-provider
    config guard (it builds a model) fires first, then ``RUNTIME_ENABLED`` — a disabled runtime never
    builds a flow. Two kitaru-backed config sources (ADR-0008 §5) move that key guard to a pre-flight
    after the runtime guard, because each boots Kitaru: when the **secret-store config source**
    (``RUNTIME_SECRET_STORE_CONFIG``) is on the whole provider config is hydrated from a Kitaru secret
    up front, so a key/model living only in the secret satisfies the guard and a missing/malformed
    secret is one friendly line; when the **Credentials Proxy** is on the model key comes from a Kitaru
    secret, validated by a proxy-aware pre-flight. With both on they compose (secret-store hydration
    first, then the proxy resolves its key from the now-hydrated secret), never two conflicting lines.
    ``kitaru`` is imported lazily here so the REPL path never loads it.
    """
    # The shared headless guard chain (provider-config / runtime / sandbox / secret-store / proxy),
    # byte-identical to ``decode replay`` — extracted into one helper so the two headless entrypoints
    # cannot drift. It returns the first friendly line (or None); a disabled runtime / missing key / bad
    # secret / a ``--repo`` in ``none`` mode exits non-zero here, before any flow is built. ``repo`` is
    # the ``--repo`` flag: the guard resolves it against ``SANDBOX_REPO`` (ADR-0012 §3).
    config_error = _runtime_config_preflight(repo=repo)
    if config_error is not None:
        click.echo(config_error, err=True)
        raise click.exceptions.Exit(1)

    # Resolve the Workspace source once (--repo > SANDBOX_REPO > none) and thread it — with ``local`` —
    # into the flow so the headless ``prepare_workspace`` clones the repo into ``/workspace`` (ADR-0012
    # §3). Guaranteed ``None`` in ``none`` mode by the guard above, so a non-sandbox run is unchanged.
    resolved_repo = _resolve_sandbox_repo(repo)

    # Lazy import: keep kitaru (and its heavy zenml/temporalio stack) off the REPL path entirely —
    # only ``decode run`` pays the import cost. The flow runs on the local Kitaru stack, offline.
    if hitl:
        _run_hitl(task, model, resolved_repo, local)
        return

    from decode.runtime import run_agent_task
    from decode.runtime.flow import _load_runtime_output

    logger.debug(
        "decode run starting (task=%r, model=%r, repo=%r, local=%s)",
        task,
        model,
        resolved_repo,
        local,
    )
    # Under the ``"calls"`` default (ADR-0010 §3) the bypass flow ends in several terminal per-call
    # checkpoints, so Kitaru's ``.wait()`` cannot auto-extract a single return value
    # (``_MultipleTerminalStepsOutputError`` — verified in task 068). The flow instead saves its final
    # text via the terminal ``_capture_runtime_output`` checkpoint; read it back by artifact name — the
    # same mechanism the HITL path uses. ``run(...)`` runs to completion in-process on the local stack
    # (bypass never pauses), so the handle is finished here. ``model`` is the Model Override threaded as
    # a flow input (ADR-0010 §2,4): ``None`` (no ``--model``) reads the provider's configured model.
    # ``repo`` / ``local`` are flow inputs too, so the Workspace clone rides into the durable run.
    handle = run_agent_task.run(task=task, model=model, repo=resolved_repo, local=local)
    click.echo(
        _load_runtime_output(handle.exec_id)
    )  # stdout: only the clean agent answer (pipe-safe)
    _echo_replay_anchor(handle.exec_id, model)  # stderr: exec_id + a paste-ready decode replay hint


def _echo_replay_anchor(exec_id: str, model: str | None) -> None:
    """Echo a finished bypass run's ``exec_id`` + a paste-ready ``decode replay`` hint to **stderr** (ADR-0010 §4).

    Kept off stdout so a piped ``decode run`` stays exactly the agent's answer. The ``exec_id`` is the
    durable execution the checkpoint→replay loop anchors on; the hint pre-fills a ``decode replay``
    command (task 070) with the run's own model id when ``--model`` was given, else a ``<model-id>``
    placeholder the operator fills in. Documentation, not a validated command — presence, not correctness.
    """
    model_hint = model if model else "<model-id>"
    click.echo(f"exec_id: {exec_id}", err=True)
    click.echo(f"replay it with a change:  decode replay {exec_id} --model {model_hint}", err=True)


def _run_hitl(task: str, model: str | None, repo: str | None, local: bool) -> None:
    """Drive the HITL Durable Flow and print the result, or the pause + how to resolve it (ADR-0008 §3).

    A finished run prints the agent's final text on stdout, then echoes its ``exec_id`` + a note that
    ``decode replay`` is bypass-only to stderr — a HITL run is replayed via ``kitaru executions
    replay`` because it re-asks every wait on the local stack (ADR-0010 §5,7). A run that paused on an
    unresolved durable wait prints the execution id and the ``kitaru executions`` commands an operator
    uses to inspect and resolve it out-of-band, then resume — exit stays zero (a pause is a normal HITL
    outcome, not an error). ``model`` is the Model Override, threaded to the HITL flow (ADR-0010 §2);
    ``repo`` / ``local`` thread the sandbox Workspace clone into the HITL run too (ADR-0012 §3).
    """
    from decode.runtime import run_hitl_agent_task

    logger.debug(
        "decode run --hitl starting (task=%r, model=%r, repo=%r, local=%s)",
        task,
        model,
        repo,
        local,
    )
    result = run_hitl_agent_task(task, model, repo, local)
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
    click.echo(result.output)  # stdout: only the clean agent answer (pipe-safe)
    # A completed HITL run is replayable too, but ``decode replay`` is bypass-only (a HITL replay
    # re-asks every wait on the local stack — ADR-0010 §5,7), so point at the Kitaru operator surface.
    click.echo(f"exec_id: {result.exec_id}", err=True)
    click.echo(
        f"  decode replay is bypass-only; replay this HITL run with "
        f"`kitaru executions replay {result.exec_id}` (ADR-0010 §5).",
        err=True,
    )


@cli.command("replay")
@click.argument("exec_id")
@click.option(
    "--from",
    "from_",
    default=None,
    metavar="CHECKPOINT",
    help=(
        "Replay anchor: a recorded checkpoint (name, invocation id, or call id). Turns before it serve "
        "from cache; it and everything downstream re-execute. Required — Kitaru has no default anchor."
    ),
)
@click.option(
    "--model",
    "model",
    default=None,
    metavar="ID",
    help=(
        "Swap the active provider's model id for the re-executed turns (the what-if change); defaults "
        "to the run's recorded model. Does not change the provider (set LLM_PROVIDER for that)."
    ),
)
def replay(exec_id: str, from_: str | None, model: str | None) -> None:
    """Replay a recorded bypass ``decode run`` from a checkpoint with a swapped model (ADR-0010 §5-6).

    The what-if counterpart to ``decode run``: it re-executes a durable execution ``EXEC_ID`` (the id a
    prior ``decode run`` printed on stderr) from the ``--from`` checkpoint with one thing changed — the
    model. Everything upstream of ``--from`` serves from the original run's cache; the anchor and its
    downstream turns re-execute for real, so a ``--model`` swap only bites downstream of ``--from``. The
    (possibly changed) answer prints on **stdout**; the new Fork ``exec_id``, the source id, and a diff
    hint print on **stderr** — so stdout stays pipe-clean and the compare-the-two loop is discoverable.

    \b
    A thin, **bypass-only** wrapper over Kitaru's native flow-object replay:
    * ``--from`` maps straight to Kitaru's ``from_`` — decode invents no default anchor. Kitaru *requires*
      one, so omitting ``--from`` exits with one friendly line naming the requirement (not a traceback).
    * ``--model`` maps to the Model Override flow input Kitaru swaps on replay (ADR-0010 §2); omitting it
      replays as-is. Raw ``--args`` / ``--overrides`` are **not** exposed here — they stay on the
      ``kitaru executions replay`` CLI (see the replay playbook in AGENTS.md).
    * A **HITL** exec_id is refused with guidance: a HITL replay re-asks every durable wait on the local
      stack (Kitaru cannot pre-populate wait results — ADR-0010 §5,7), so it points at
      ``kitaru executions replay`` instead. HITL answer-reuse is deferred (``tasks/future/``).

    Guards: the same headless chain as ``decode run`` (provider-config / ``RUNTIME_ENABLED`` / secret-store
    / proxy) fires first — a replay re-executes downstream model calls, so it needs a valid provider
    config. Kitaru's own replay failures each become one friendly stderr line, never a raw traceback: an
    ambiguous/invalid ``--from`` (``KitaruStateError``), a swap that diverged the recorded call sequence
    (``KitaruDivergenceError``), and a missing/unloadable ``EXEC_ID`` (``KitaruBackendError``). ``kitaru``
    is imported lazily here so the REPL path never loads it.
    """
    config_error = _runtime_config_preflight()
    if config_error is not None:
        click.echo(config_error, err=True)
        raise click.exceptions.Exit(1)

    # Kitaru requires an explicit ``from_`` (no default); mirror that requirement rather than invent an
    # anchor (ADR-0010 §5). Checked after the guard chain so a disabled runtime / missing key still wins.
    if from_ is None:
        click.echo(_REPLAY_NO_FROM_MESSAGE, err=True)
        raise click.exceptions.Exit(1)

    # Lazy imports: keep kitaru (and its heavy zenml/temporalio stack) off the REPL path — only
    # ``decode replay`` pays the import cost, exactly like ``decode run``.
    from kitaru.errors import KitaruDivergenceError, KitaruError, KitaruStateError

    from decode.runtime import is_hitl_execution, replay_agent_task

    logger.debug("decode replay starting (exec_id=%r, from_=%r, model=%r)", exec_id, from_, model)
    try:
        # Bypass-only: a HITL replay re-asks every wait on the local stack (ADR-0010 §5,7). Detection
        # (and the replay below) can raise ``KitaruBackendError`` for a missing/unloadable id — caught
        # below as one friendly line. ``is_hitl_execution`` reads the recorded flow name.
        if is_hitl_execution(exec_id):
            click.echo(_replay_hitl_message(exec_id), err=True)
            raise click.exceptions.Exit(1)
        result = replay_agent_task(exec_id, from_=from_, model=model)
    except KitaruStateError as exc:
        logger.debug("replay anchor rejected for %s: %s", exec_id, exc)
        click.echo(_replay_bad_anchor_message(exc), err=True)
        raise click.exceptions.Exit(1) from exc
    except KitaruDivergenceError as exc:
        logger.debug("replay diverged for %s: %s", exec_id, exc)
        click.echo(_replay_diverged_message(exec_id), err=True)
        raise click.exceptions.Exit(1) from exc
    except KitaruError as exc:
        # Catch-all for the remaining kitaru failures (``KitaruBackendError`` for a missing/unloadable
        # id, and any other) so no raw traceback ever escapes — one friendly line instead.
        logger.debug("replay could not load/execute %s: %s", exec_id, exc)
        click.echo(_replay_load_failed_message(exec_id, exc), err=True)
        raise click.exceptions.Exit(1) from exc

    click.echo(result.output)  # stdout: only the (possibly changed) agent answer (pipe-safe)
    _echo_replay_fork(
        result.exec_id, result.original_exec_id
    )  # stderr: fork id + source + diff hint


def _replay_hitl_message(exec_id: str) -> str:
    """The friendly line refusing a HITL exec_id — decode replay is bypass-only (ADR-0010 §5,7)."""
    return (
        f"Decode: `decode replay` is bypass-only — execution {exec_id} is a HITL run, and a HITL replay "
        f"re-asks every durable wait on the local stack (ADR-0010 §5). Replay it on the Kitaru operator "
        f"surface instead: `kitaru executions replay {exec_id} --from <checkpoint>`."
    )


def _replay_bad_anchor_message(exc: Exception) -> str:
    """The friendly line for an ambiguous/invalid ``--from`` (``KitaruStateError``).

    Kitaru's message already lists the available checkpoints (for an unknown selector) or says the
    selector is ambiguous — surfaced verbatim so the operator can pick a valid anchor directly.
    """
    return (
        f"Decode: replay could not use that --from anchor — {exc} "
        "Pick one from `kitaru executions get <exec_id>` (a checkpoint name, invocation id, or call id)."
    )


def _replay_diverged_message(exec_id: str) -> str:
    """The friendly line when the model swap diverged the recorded call sequence (``KitaruDivergenceError``)."""
    return (
        f"Decode: the model swap diverged the recorded call sequence of {exec_id} — the new model "
        "tool-called differently downstream of --from, so Kitaru cannot replay it against the original "
        "(that IS the honest what-if outcome: the change altered the run). Try anchoring --from later."
    )


def _replay_load_failed_message(exec_id: str, exc: Exception) -> str:
    """The friendly line for a missing/unloadable ``EXEC_ID`` (``KitaruBackendError``) or other kitaru failure."""
    return (
        f"Decode: replay could not load or execute {exec_id} — {exc} "
        "Check the id (from a `decode run` stderr `exec_id:` line, or `kitaru executions list`)."
    )


def _echo_replay_fork(new_exec_id: str, original_exec_id: str) -> None:
    """Echo the Fork's new exec_id, the source exec_id, and a diff hint to **stderr** (ADR-0010 §4,6).

    Kept off stdout so a piped ``decode replay`` stays exactly the (possibly changed) answer. The hint
    points ONLY at Kitaru's CONFIRMED operator surface (verified on kitaru 0.18 — there is **no** ``kitaru
    diff`` CLI and no ``.diff()`` SDK method): inspect and compare the two executions with
    ``kitaru executions get``. The full checkpoint→replay→diff→decide playbook (the "three runs" rule,
    ``--args`` / ``--overrides``, cohort scaling) lives in AGENTS.md under "Headless replay & what-if".
    """
    click.echo(f"exec_id: {new_exec_id}  (the fork — a new execution)", err=True)
    click.echo(f"original: {original_exec_id}", err=True)
    click.echo(
        f"compare them:  kitaru executions get {new_exec_id}  "
        f"vs  kitaru executions get {original_exec_id}",
        err=True,
    )


if __name__ == "__main__":
    cli()
