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

# Friendly line when ``decode run`` is invoked with RUNTIME_ENABLED=false (ADR-0008).
_RUNTIME_DISABLED_MESSAGE = (
    "Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true in your environment "
    "or .env to use `decode run` (see .env.example)."
)

# Kitaru replay REQUIRES an anchor (``from_`` has no default — verified on kitaru 0.18); decode
# mirrors that and invents none (ADR-0010 §5).
_REPLAY_NO_FROM_MESSAGE = (
    "Decode: `decode replay` needs --from <checkpoint> — Kitaru replay requires an explicit anchor "
    "(it has no default). List a recorded run's checkpoints with `kitaru executions get <exec_id>` and "
    "pass one as --from (e.g. an early `*_model_request` step to swap the model for the whole run)."
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
        f"`make sync-secrets ENV={settings.decode_env}` (see CREDENTIALS.md)."
    )


def _runtime_config_preflight(repo: str | None = None) -> str | None:
    """The shared headless guard chain for ``decode run`` / ``decode replay``; a friendly line or None.

    Both headless entrypoints run this identical ordered chain, returning the FIRST friendly error
    line. Order is load-bearing:

    1. Environment-Bucket guard — at a remote ``DECODE_ENV`` the provider key is expected to come from
       the bucket, so a bucket failure must be named before any key guard can mis-blame ``.env``.
    2. Per-provider config guard — unconditional: hydration is process-scoped (ADR-0015 §5), so this
       already runs against the hydrated config, whichever mechanism supplied it.
    3. ``RUNTIME_ENABLED`` — a disabled runtime never builds/replays a flow.
    4. Sandbox backend guard, then the sandbox-repo guard. ``repo`` is the ``--repo`` flag (``None``
       for ``decode replay``), resolved against ``SANDBOX_REPO`` inside.
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
    behaviour is identical to the pre-runtime build. ``decode run "<task>"`` (ADR-0008) runs a
    single task headlessly through the durable runtime instead.

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

    Guards (same friendly-line-on-stderr, non-zero-exit contract as the REPL): at a remote
    ``DECODE_ENV`` the Environment-Bucket guard fires first (a missing bucket names
    ``make sync-secrets ENV=<env>``, ADR-0015 §5), then the per-provider config guard — it reads the
    already-hydrated config, whichever mechanism supplied it — then ``RUNTIME_ENABLED``: a disabled
    runtime never builds a flow. ``kitaru`` is imported lazily here so a ``DECODE_ENV=local`` REPL
    path never loads it.
    """
    # The shared headless guard chain, byte-identical to ``decode replay`` so the two cannot drift;
    # any failure exits non-zero here, before any flow is built.
    config_error = _runtime_config_preflight(repo=repo)
    if config_error is not None:
        click.echo(config_error, err=True)
        raise click.exceptions.Exit(1)

    # Resolve the Workspace source once and thread it into the flow (ADR-0012 §3); guaranteed
    # ``None`` in ``none`` mode by the guard above.
    resolved_repo = _resolve_sandbox_repo(repo)

    # Lazy import: keep kitaru's heavy zenml/temporalio stack off the REPL path entirely.
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
    # Under the ``"calls"`` default the flow ends in several terminal checkpoints, so Kitaru's
    # ``.wait()`` cannot auto-extract one return value; the flow saves its final text via the
    # ``_capture_runtime_output`` checkpoint, read back by artifact name (ADR-0010 §3). Bypass never
    # pauses, so the handle is finished here. ``model`` / ``repo`` / ``local`` ride as flow inputs.
    handle = run_agent_task.run(task=task, model=model, repo=resolved_repo, local=local)
    click.echo(
        _load_runtime_output(handle.exec_id)
    )  # stdout: only the clean agent answer (pipe-safe)
    _echo_replay_anchor(handle.exec_id, model)  # stderr: exec_id + a paste-ready decode replay hint
    # The Git hand-back (ADR-0012 §8) runs INSIDE the flow, not here: the flow's process is the one
    # that owns the Workspace — on a remote stack that is the Modal container, and this submitting
    # process's ``.decode/sandbox`` is a stranger to the run (``flow._ship_headless_workspace``).


def _echo_replay_anchor(exec_id: str, model: str | None) -> None:
    """Echo a finished bypass run's ``exec_id`` + a paste-ready ``decode replay`` hint to **stderr** (ADR-0010 §4).

    Kept off stdout so a piped ``decode run`` stays exactly the agent's answer. Documentation, not a
    validated command — presence, not correctness.
    """
    model_hint = model if model else "<model-id>"
    click.echo(f"exec_id: {exec_id}", err=True)
    click.echo(f"replay it with a change:  decode replay {exec_id} --model {model_hint}", err=True)


def _run_hitl(task: str, model: str | None, repo: str | None, local: bool) -> None:
    """Drive the HITL Durable Flow and print the result, or the pause + how to resolve it (ADR-0008 §3).

    A pause is a normal HITL outcome (exit stays zero): print the exec_id + the ``kitaru executions``
    commands to resolve and resume it. A finished run prints the answer on stdout and points at
    ``kitaru executions replay`` — ``decode replay`` is bypass-only (ADR-0010 §5,7).
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
    # ``decode replay`` is bypass-only, so point a completed HITL run at the Kitaru operator surface.
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

    Guards: the same headless chain as ``decode run`` (environment bucket / provider-config /
    ``RUNTIME_ENABLED`` / sandbox) fires first — a replay re-executes downstream model calls, so it needs
    a valid provider config. Kitaru's own replay failures each become one friendly stderr line, never a raw traceback: an
    ambiguous/invalid ``--from`` (``KitaruStateError``), a swap that diverged the recorded call sequence
    (``KitaruDivergenceError``), and a missing/unloadable ``EXEC_ID`` (``KitaruBackendError``). ``kitaru``
    is imported lazily here so the REPL path never loads it.
    """
    config_error = _runtime_config_preflight()
    if config_error is not None:
        click.echo(config_error, err=True)
        raise click.exceptions.Exit(1)

    # Kitaru requires an explicit ``from_`` — checked after the guard chain so a disabled runtime /
    # missing key still wins (ADR-0010 §5).
    if from_ is None:
        click.echo(_REPLAY_NO_FROM_MESSAGE, err=True)
        raise click.exceptions.Exit(1)

    # Lazy imports: keep kitaru off the REPL path, exactly like ``decode run``.
    from kitaru.errors import KitaruDivergenceError, KitaruError, KitaruStateError

    from decode.runtime import is_hitl_execution, replay_agent_task

    logger.debug("decode replay starting (exec_id=%r, from_=%r, model=%r)", exec_id, from_, model)
    try:
        # Bypass-only: a HITL replay re-asks every wait (ADR-0010 §5,7). ``is_hitl_execution`` reads
        # the recorded flow name; a missing/unloadable id raises KitaruBackendError, caught below.
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
        # Catch-all for the remaining kitaru failures so no raw traceback ever escapes.
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
    """The friendly line for an ambiguous/invalid ``--from`` (``KitaruStateError``); Kitaru's own
    message already names the valid checkpoints, so it is surfaced verbatim."""
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

    Kept off stdout for piping. The hint points ONLY at Kitaru's confirmed operator surface — there
    is no ``kitaru diff`` CLI and no ``.diff()`` SDK method (verified on kitaru 0.18); the full
    playbook lives in AGENTS.md under "Headless replay & what-if".
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
