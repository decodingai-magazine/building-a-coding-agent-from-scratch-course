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

from decode.agent.context_window import resolve_context_window_detail  # noqa: E402
from decode.agents.loader import load_primary_agent  # noqa: E402
from decode.config.settings import (  # noqa: E402
    bucket_load_error,
    environment_bucket_name,
    settings,
)
from decode.permissions.types import PermissionMode  # noqa: E402
from decode.tui.app import run_app  # noqa: E402

logger = logging.getLogger(__name__)

# Friendly line when no Gemini key is configured, instead of a raw pydantic_ai.UserError
# traceback from build_agent() (ADR-0002 §1).
_NO_KEY_MESSAGE = (
    "Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example)."
)

# The friendly line shown when ``LLM_PROVIDER=openrouter`` is selected without its API key.
_OPENROUTER_NO_KEY_MESSAGE = (
    "Decode: LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY set in your environment or .env "
    "(see .env.example)."
)

# Modal proxy tokens are both-or-neither (neither = an --unauthenticated endpoint) — ADR-0005 §5.
_MODAL_PROXY_TOKENS_MESSAGE = (
    "Decode: LLM_PROVIDER=modal proxy tokens are both-or-neither — set both MODAL_PROXY_TOKEN_ID "
    "and MODAL_PROXY_TOKEN_SECRET, or neither for an --unauthenticated endpoint (see .env.example)."
)

# Docker daemon reachability guard — presence only; fires in both the REPL and the headless
# pre-flight (ADR-0011 §1).
_SANDBOX_DOCKER_UNREACHABLE_MESSAGE = (
    "Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable — start Docker and retry "
    "(see .env.example)."
)

# Modal credential presence guard — no network call, no ``modal`` import (ADR-0011 §1).
_SANDBOX_MODAL_NO_CREDENTIALS_MESSAGE = (
    "Decode: SANDBOX_MODE=modal but Modal credentials are missing — run `modal token set …` "
    "(see .env.example)."
)

# A --repo/SANDBOX_REPO with SANDBOX_MODE=none is a config error — the isolated Workspace only
# exists in a sandbox mode (ADR-0012 §3).
_SANDBOX_REPO_NONE_MODE_MESSAGE = (
    "Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace, which only exists "
    "in a sandbox mode — set SANDBOX_MODE=docker or SANDBOX_MODE=modal, or drop --repo/SANDBOX_REPO "
    "(see .env.example)."
)

# Deliberately short: a healthy local daemon answers near-instantly, and startup must not hang on it.
_DOCKER_PROBE_TIMEOUT_S = 5.0

# The startup Agent persona when ``--agent`` is omitted (ADR-0003 §9).
_DEFAULT_AGENT = "build"

# Friendly line when ``decode run`` is invoked with RUNTIME_ENABLED=false (ADR-0019 §1).
_RUNTIME_DISABLED_MESSAGE = (
    "Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true in your environment "
    "or .env to use `decode run` (see .env.example)."
)


def _provider_config_error() -> str | None:
    """One friendly line if the selected provider's required config is missing, else None (ADR-0005 §6).

    Presence / both-or-neither shape only — never correctness (a wrong key fails at the first model
    request). ``modal`` needs only url + model; its proxy tokens are optional but both-or-neither.
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


def _context_window_notice(model: str | None = None) -> str | None:
    """One stderr line when the compaction window is an ASSUMPTION, else ``None``.

    Non-blocking, unlike the guards around it: an unknown model is a perfectly runnable
    configuration, just one whose window decode had to guess. Silence here is what let a 1M
    Gemini default sit in front of a 262k endpoint — both compaction tiers then fire above the
    endpoint's ceiling, so the request is truncated before compaction ever runs.

    Resolution runs through the task-123 seam, so the notice only claims "assumed" when NEITHER the
    provider probe NOR the static table produced a number — warning after a successful probe would
    train the operator to ignore the line. ``model`` is the ``--model`` override, so a headless run
    warns about the model it will actually use.

    This is an inference path (a REPL or a ``decode run`` is starting), which is exactly where a
    probe is allowed; the memo makes the resolution the agent build does moments later free.
    ``--help`` / ``--version`` never reach here.
    """
    resolved = resolve_context_window_detail(model)
    if not resolved.is_assumed:
        return None
    return (
        f"Decode: no known context window for model {model or settings.active_model!r}; assuming "
        f"{resolved.tokens:,} tokens for compaction. Set "
        "COMPACTION_CONTEXT_WINDOW_TOKENS to the model's real max input window if that is wrong "
        "(an OpenAI-compatible endpoint reports it as max_model_len on GET /v1/models)."
    )


def _docker_daemon_reachable() -> bool:
    """True if the local Docker daemon answers a fast ``docker info`` probe (ADR-0011 §1).

    Shells out to the docker CLI (no SDK). Reachability, not correctness: a missing binary, a
    non-zero exit, or a probe timeout all mean "not reachable" — never a crash.
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
    """True if Modal account credentials are present — no network call, no ``modal`` import (ADR-0011 §1).

    Checks auth the way the modal CLI resolves it: the MODAL_TOKEN_ID/MODAL_TOKEN_SECRET env pair
    (read from ``os.environ`` — they belong to the modal CLI, not decode config) or ``~/.modal.toml``.
    Presence only — a bad token fails at the first sandbox call.
    """
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return (Path.home() / ".modal.toml").exists()


def _sandbox_config_error() -> str | None:
    """One friendly line if the selected Sandbox Mode's backend is unavailable, else None (ADR-0011 §1).

    Presence/reachability only, wired into both the REPL startup chain and the headless pre-flight;
    ``none`` (the default) runs no probe.
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
    return None  # ``none``: no probe, byte-identical to the LocalExecutor path.


def _resolve_sandbox_repo(repo_flag: str | None) -> str | None:
    """Resolve the Workspace source repo: ``--repo`` flag > ``SANDBOX_REPO`` > None (ADR-0012 §3).

    The single resolution point shared by the REPL and headless entrypoints, so the precedence can
    never drift. An empty ``SANDBOX_REPO`` (the default) is treated as unset.
    """
    return repo_flag or settings.sandbox_repo or None


def _sandbox_repo_config_error(repo: str | None) -> str | None:
    """One friendly line if a repo is requested while ``SANDBOX_MODE=none``, else None (ADR-0012 §3)."""
    if repo is not None and settings.sandbox_mode == "none":
        return _SANDBOX_REPO_NONE_MODE_MESSAGE
    return None


def _env_bucket_error() -> str | None:
    """One friendly line if a remote ``DECODE_ENV``'s Environment Bucket could not be loaded (ADR-0015 §5).

    ``None`` at ``local`` (nothing to load) and whenever the bucket hydrated cleanly. The settings
    singleton is built at import, so the bucket source captures its failure instead of raising; this
    turns that captured failure into the house friendly-line-on-stderr + non-zero-exit contract, in
    the REPL startup chain and the headless pre-flight alike. It runs FIRST in both: at a remote env
    the provider key is EXPECTED to come from the bucket, so a bucket failure must name
    ``make sync-secrets``, not ``GEMINI_API_KEY``.
    """
    if settings.decode_env == "local":
        return None
    error = bucket_load_error()
    if error is None:
        return None
    logger.debug("environment bucket unavailable at DECODE_ENV=%s: %s", settings.decode_env, error)
    bucket = environment_bucket_name(settings.decode_env)
    return (
        f"Decode: DECODE_ENV={settings.decode_env} but the environment bucket {bucket!r} could not "
        f"be loaded (it is missing, or the Kitaru local server is down) — run "
        f"`make sync-secrets ENV={settings.decode_env}` (see running_the_code/06_credentials.md)."
    )


def _runtime_config_preflight(repo: str | None = None) -> str | None:
    """The headless guard chain for ``decode run``; the FIRST friendly error line, or None.

    Order is load-bearing:

    1. Environment-Bucket guard — at a remote ``DECODE_ENV`` the provider key is expected to come from
       the bucket, so a bucket failure must be named before any key guard can mis-blame ``.env``.
    2. Per-provider config guard — unconditional: hydration is process-scoped (ADR-0015 §5), so this
       already runs against the hydrated config, whichever mechanism supplied it.
    3. ``RUNTIME_ENABLED`` — a disabled runtime never builds an agent.
    4. Sandbox backend guard, then the sandbox-repo guard. ``repo`` is the ``--repo`` flag, resolved
       against ``SANDBOX_REPO`` inside.
    """
    bucket_error = _env_bucket_error()
    if bucket_error is not None:
        return bucket_error

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

    return None


@click.group(invoke_without_command=True)
@click.version_option(version=__import__("decode").__version__, prog_name="decode")
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
    help="Start with this agent persona (build / plan / code-reviewer).",
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
    behaviour is identical to the pre-runtime build. ``decode run "<task>"`` (ADR-0019) runs a
    single task headlessly through the same agent instead.

    In a sandbox mode ``--repo <url-or-local-path>`` clones a repo into the isolated Workspace at
    launch (overriding ``SANDBOX_REPO``); ``--local`` picks a fast local clone (ADR-0012 §3).
    """
    # A subcommand was invoked: the REPL-only flags and startup guards below apply solely to the
    # bare ``decode`` REPL path.
    if ctx.invoked_subcommand is not None:
        return

    logger.debug("decode starting (resume=%s, agent=%s, mode=%s)", resume, agent, mode)
    # Environment-Bucket startup guard (ADR-0015 §5), FIRST in the chain: at a remote DECODE_ENV the
    # provider key is expected to come from the bucket, so a bucket failure must name
    # `make sync-secrets`, not GEMINI_API_KEY. A no-op at the ``local`` default.
    bucket_error = _env_bucket_error()
    if bucket_error is not None:
        click.echo(bucket_error, err=True)
        raise click.exceptions.Exit(1)

    # Provider config startup guard (ADR-0005 §6): one friendly stderr line before any agent is
    # built, instead of the raw pydantic_ai.UserError build_agent() would raise.
    config_error = _provider_config_error()
    if config_error is not None:
        logger.debug("provider %s misconfigured; refusing to start", settings.llm_provider)
        click.echo(config_error, err=True)
        raise click.exceptions.Exit(1)

    # Context-window notice: NOT a guard — it warns and continues. The window is derived from the
    # active model when unset; an unrecognised model gets a conservative assumption the operator
    # should see rather than discover as truncated requests.
    window_notice = _context_window_notice()
    if window_notice is not None:
        click.echo(window_notice, err=True)

    # Sandbox backend startup guard (ADR-0011 §1): refuse an unavailable backend now, not on the
    # first ``bash`` call.
    sandbox_error = _sandbox_config_error()
    if sandbox_error is not None:
        logger.debug("sandbox backend %s unavailable; refusing to start", settings.sandbox_mode)
        click.echo(sandbox_error, err=True)
        raise click.exceptions.Exit(1)

    # Sandbox-repo startup guard (ADR-0012 §3): a resolved repo with SANDBOX_MODE=none is a config error.
    resolved_repo = _resolve_sandbox_repo(repo)
    repo_error = _sandbox_repo_config_error(resolved_repo)
    if repo_error is not None:
        logger.debug("sandbox repo requested in none mode; refusing to start")
        click.echo(repo_error, err=True)
        raise click.exceptions.Exit(1)

    # Unknown-agent startup guard (ADR-0003 §9, ADR-0013 §3): ``load_primary_agent`` raises
    # ValueError naming the primary choices for both an unknown name and a subagent (explore).
    try:
        load_primary_agent(agent)
    except ValueError as exc:
        logger.debug("unknown --agent %r; refusing to start", agent)
        click.echo(f"Decode: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc

    # Unknown-mode startup guard (ADR-0003 §9); ``None`` (no flag) keeps the agent's own default mode.
    if mode is not None:
        try:
            PermissionMode(mode.strip().lower())
        except ValueError as exc:
            valid = ", ".join(m.value for m in PermissionMode)
            logger.debug("unknown --mode %r; refusing to start", mode)
            click.echo(f"Decode: unknown mode {mode!r}; valid modes: {valid}.", err=True)
            raise click.exceptions.Exit(1) from exc

    # Launch the REPL: bare ``--resume`` arrives as "latest", a named one as its id, no flag as None.
    # In ``none`` mode ``resolved_repo`` is guaranteed None (guard above), so the REPL is unchanged.
    asyncio.run(run_app(resume=resume, agent=agent, mode=mode, repo=resolved_repo, local=local))


@cli.command("run")
@click.argument("task")
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
def run(task: str, model: str | None, repo: str | None, local: bool) -> None:
    """Run a single TASK headlessly, then print the agent's answer (ADR-0019 §1).

    The autonomous counterpart to the REPL: ``decode run "<task>"`` builds the SAME agent the TUI
    does and drives it to completion in one ``asyncio.run``. Every gated tool runs inline with no
    prompt (the gate is in bypass) and ``ask_user`` is a headless no-op — there is no pause, ever.

    ``--model ID`` overrides the active provider's model id for this run; the provider itself stays
    selected by ``LLM_PROVIDER`` (no cross-provider swap). Presence, not correctness: a model id
    wrong for the provider is not validated here (matching the key guards) — it fails at the first
    model request.

    In a sandbox mode ``--repo <url-or-local-path>`` clones a repo into the isolated Workspace
    (overriding ``SANDBOX_REPO``) and ``--local`` picks a fast local clone; on completion the
    Workspace ships back as a ``decode/<session-id>`` branch (ADR-0012 §3,8).

    The agent's answer prints on **stdout** and nothing else does, so a piped ``decode run`` yields
    exactly the answer; diagnostics go to stderr.

    Guards (same friendly-line-on-stderr, non-zero-exit contract as the REPL): at a remote
    ``DECODE_ENV`` the Environment-Bucket guard fires first (a missing bucket names
    ``make sync-secrets ENV=<env>``, ADR-0015 §5), then the per-provider config guard — it reads the
    already-hydrated config, whichever mechanism supplied it — then ``RUNTIME_ENABLED``: a disabled
    runtime never builds an agent, then the sandbox backend / repo guards.
    """
    # The headless guard chain; any failure exits non-zero here, before any agent is built.
    config_error = _runtime_config_preflight(repo=repo)
    if config_error is not None:
        click.echo(config_error, err=True)
        raise click.exceptions.Exit(1)

    # Same non-blocking window notice the REPL emits — a headless run is exactly where an assumed
    # window goes unnoticed, since nobody is watching a status bar. ``model`` rides along so the
    # notice describes the model ``--model`` actually selected.
    window_notice = _context_window_notice(model)
    if window_notice is not None:
        click.echo(window_notice, err=True)

    # Resolve the Workspace source once (ADR-0012 §3); guaranteed ``None`` in ``none`` mode by the
    # guard above.
    resolved_repo = _resolve_sandbox_repo(repo)

    # Imported inside the subcommand so the REPL path loads no headless machinery.
    from decode.runtime import run_headless_task

    logger.debug(
        "decode run starting (task=%r, model=%r, repo=%r, local=%s)",
        task,
        model,
        resolved_repo,
        local,
    )
    output = run_headless_task(task, model=model, repo=resolved_repo, local=local)
    click.echo(output)  # stdout: only the clean agent answer (pipe-safe)
    # The Git hand-back (ADR-0012 §8) runs inside ``run_headless_task``, right after the sandbox
    # executor is reaped — the runner process is the one that owns the Workspace.


if __name__ == "__main__":
    cli()
