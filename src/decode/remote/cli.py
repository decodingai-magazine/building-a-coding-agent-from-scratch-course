"""``decode remote`` — launch headless runs on the deployed Modal Headless App (ADR-0020).

The LAUNCH half of the launch-vs-execute split. Every subcommand here targets the DEPLOYMENT
(:func:`deployed_run_task` — ``modal.Function.from_name``), never an ephemeral app: the laptop
builds no image, imports :mod:`decode.remote.app` never, and closes the lid on a ``--detach`` with
the runs still going. The price is one ``decode remote deploy`` before the first run (and after any
decode code change — the source is baked into the image).

``modal`` is imported lazily, inside the two helpers that touch the deployment, so registering this
group on the ``decode`` CLI costs the REPL nothing (ADR-0011 §4's lazy-backend rule, applied to the
launcher). Validation is client-side and shared with the Functions through
:mod:`decode.remote.headless`: a ``docker`` mode, a fan-out without a repo, a zero attempt count —
each costs ONE friendly line and no container.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import click

from decode.config.settings import settings
from decode.remote.headless import (
    DEFAULT_SANDBOX_MODE,
    DEFAULT_TIMEOUT_SECONDS,
    RUN_SUMMARY_FORMAT,
    SUPPORTED_SANDBOX_MODES,
    app_name,
    attempt_task,
    attempts_exit_code,
    attempts_input_error,
    attempts_input_warning,
    attempts_notes,
    attempts_table,
    compare_commands,
    detach_lines,
    failed_attempt_result,
    sandbox_mode_error,
)
from decode.remote.image import repo_root_error

if TYPE_CHECKING:  # ``modal`` types only — the runtime import stays inside the helpers below.
    import modal

# What ``modal deploy`` is pointed at: the app module, by import path, so the deploy works from any
# cwd inside the checkout (``modal deploy -m`` resolves it the way Python does).
APP_MODULE = "decode.remote.app"

# The deploy is delegated to the modal CLI as a subprocess — its output (the build log, the printed
# webhook URL, the ``✓ App deployed`` line) is the operator's, verbatim.
DEPLOY_ARGV = ("modal", "deploy", "-m", APP_MODULE)


def deployed_app_name() -> str:
    """``decode-headless-<env>`` for THIS laptop's ``DECODE_ENV`` — what every subcommand targets.

    Read from ``Settings`` rather than a constant, so ``DECODE_ENV=prod decode remote run`` reaches
    the ``prod`` deployment and a plain one cannot (ADR-0021 §2). It is the same value the deploy
    baked into that app's image, which is what makes "the launcher and the deployment agree" true by
    construction instead of by convention.
    """
    return app_name(settings.decode_env)


def logs_argv() -> tuple[str, ...]:
    """``modal app logs decode-headless-<env>`` — this environment's deployment, not another's."""
    return ("modal", "app", "logs", deployed_app_name())


NOT_DEPLOYED_FORMAT = (
    "Decode: the {app_name} app is not deployed, so there is nothing to run — run "
    "`decode remote deploy` once, then re-run this command ({error})."
)
NO_CREDENTIALS_FORMAT = (
    "Decode: Modal credentials are missing or rejected — run `modal token set …` "
    "(see .env.example) ({error})."
)


def deployed_run_task() -> modal.Function:
    """The ``run_task`` published by ``decode remote deploy`` — what every subcommand calls.

    NOT an ephemeral app's Function: ``modal run`` tears its app down as soon as the entrypoint
    returns, which would cancel every spawned call the moment ``--detach`` printed their ids. The
    deployment outlives the launcher, which is what makes fire-and-forget real (ADR-0020 §1) — and
    it is also the image every run shares, built once at deploy. The two ways this can fail are each
    ONE friendly line: no deployment yet, or no Modal credentials on this machine.
    """
    import modal

    name = deployed_app_name()
    try:
        return modal.Function.from_name(name, "run_task")
    except modal.exception.NotFoundError as error:
        raise click.ClickException(
            NOT_DEPLOYED_FORMAT.format(app_name=name, error=error)
        ) from error
    except modal.exception.AuthError as error:
        raise click.ClickException(NO_CREDENTIALS_FORMAT.format(error=error)) from error


def spawn_attempts(
    function: modal.Function,
    *,
    task: str,
    count: int,
    repo: str | None,
    sandbox_mode: str,
    model: str | None,
    timeout_seconds: int,
    max_requests: int | None = None,
) -> list[modal.FunctionCall]:
    """Fire ``count`` independent ``run_task`` calls at the same task and return their handles.

    No warm-up run and no stagger: the image is built once at deploy, so N cold spawns share it
    instead of racing to build it. Each call gets its own gVisor container, its own Workspace, its
    own ``decode/<session-id>``.
    """
    text = attempt_task(task)
    return [
        function.spawn(
            task=text,
            repo=repo,
            sandbox_mode=sandbox_mode,
            model=model,
            timeout_seconds=timeout_seconds,
            max_requests=max_requests,
        )
        for _ in range(count)
    ]


def collect_attempt(call: modal.FunctionCall, *, sandbox_mode: str) -> Mapping[str, object]:
    """Wait for one attempt and return its payload — or a ``FAILED`` stand-in if it never returns."""
    try:
        return call.get()
    except Exception as error:  # a dead container must cost ONE row, not the whole table
        return failed_attempt_result(error, sandbox_mode=sandbox_mode)


def _echo_lines(lines: Sequence[str]) -> None:
    for line in lines:
        click.echo(line)


# --- the click surface -------------------------------------------------------------------------------

_sandbox_mode_option = click.option(
    "--sandbox-mode",
    "sandbox_mode",
    default=DEFAULT_SANDBOX_MODE,
    show_default=True,
    metavar="|".join(SUPPORTED_SANDBOX_MODES),
    help="Where bash runs on Modal: none (the gVisor container itself; a --repo is cloned by the "
    "harness and nothing ships back) or modal (a nested Modal Sandbox that hands a "
    "decode/<session-id> branch back). docker is rejected — no daemon on Modal.",
)
_repo_option = click.option(
    "--repo", "repo", default=None, metavar="URL", help="Clone this repo for the run."
)
_model_option = click.option(
    "--model", "model", default=None, metavar="ID", help="Override the provider's model id."
)
_timeout_option = click.option(
    "--timeout-seconds",
    "timeout_seconds",
    default=DEFAULT_TIMEOUT_SECONDS,
    show_default=True,
    type=click.IntRange(min=1),
    help="Kill the decode process past this many seconds (the wall-clock ceiling).",
)
_max_requests_option = click.option(
    "--max-requests",
    "max_requests",
    default=None,
    type=click.IntRange(min=1),
    metavar="N",
    help="Stop the run after N model requests — decode run --max-requests (the token ceiling).",
)


@click.group("remote")
def remote() -> None:
    """Run decode headlessly on Modal — deploy once, then run, fan out, or read the logs.

    A remote run is ``decode run`` executed in a gVisor container on the deployed
    ``decode-headless-<env>`` app (ADR-0020): same console script, same answer on stdout, plus a
    recorded Kitaru Session and — with ``--sandbox-mode modal`` and a ``SANDBOX_GIT_TOKEN`` in the
    secret — a ``decode/<session-id>`` branch on origin. ``DECODE_ENV`` picks WHICH deployment every
    subcommand here talks to, deploy included (ADR-0021 §2). Runbook: running_the_code/04_deploy.md.
    """


@remote.command("deploy")
def deploy() -> None:
    """Build the image and publish the app (run once, and again after any decode code change).

    Delegates to ``modal deploy -m decode.remote.app`` from this checkout — the modal CLI prints the
    build log, the three Functions and the webhook URL. A nightly cron is registered when
    ``DECODE_NIGHTLY_CRON`` + ``DECODE_NIGHTLY_TASK`` are exported in this shell (see the runbook);
    without them the deploy carries no schedule.
    """
    checkout_error = repo_root_error()
    if checkout_error is not None:
        raise click.ClickException(checkout_error)
    click.echo(
        f"Decode: deploying {deployed_app_name()} (DECODE_ENV={settings.decode_env}).", err=True
    )
    # ``DECODE_ENV`` is passed EXPLICITLY, not inherited: ``modal deploy`` re-imports the app module
    # in a subprocess, which reads ``os.environ`` — and a value that came from this laptop's ``.env``
    # reached ``Settings`` without ever landing there. Without this line, `DECODE_ENV=prod` in a
    # ``.env`` would name the app ``decode-headless-prod`` on the launcher side and publish
    # ``decode-headless-local`` from the deploy side (ADR-0021 §3).
    completed = subprocess.run(
        list(DEPLOY_ARGV), check=False, env={**os.environ, "DECODE_ENV": settings.decode_env}
    )
    if completed.returncode:
        sys.exit(completed.returncode)


@remote.command("run")
@click.argument("task")
@_repo_option
@_sandbox_mode_option
@_model_option
@_timeout_option
@_max_requests_option
def run(
    task: str,
    repo: str | None,
    sandbox_mode: str,
    model: str | None,
    timeout_seconds: int,
    max_requests: int | None,
) -> None:
    """Run ONE task on Modal, stream its answer, and exit with the run's own code.

    The mode guard runs HERE first: ``--sandbox-mode docker`` costs one line on stderr and a
    non-zero exit, with no container started and nothing billed (ADR-0020 §3).
    """
    mode_error = sandbox_mode_error(sandbox_mode)
    if mode_error is not None:
        raise click.ClickException(mode_error)

    result = deployed_run_task().remote(
        task=task,
        repo=repo,
        sandbox_mode=sandbox_mode,
        model=model,
        timeout_seconds=timeout_seconds,
        max_requests=max_requests,
    )
    click.echo(result["answer"])
    click.echo(RUN_SUMMARY_FORMAT.format(**result), err=True)
    if result["exit_code"]:
        sys.exit(int(result["exit_code"]))


@remote.command("attempts")
@click.argument("task")
@_repo_option
@click.option(
    "--attempts",
    "attempts",
    default=3,
    show_default=True,
    type=int,
    help="How many independent attempts to spawn at the same task.",
)
@_sandbox_mode_option
@_model_option
@_timeout_option
@_max_requests_option
@click.option(
    "--detach",
    is_flag=True,
    help="Print the function-call ids and exit; the deployed app keeps running without the laptop.",
)
def attempts_command(
    task: str,
    repo: str | None,
    attempts: int,
    sandbox_mode: str,
    model: str | None,
    timeout_seconds: int,
    max_requests: int | None,
    detach: bool,
) -> None:
    """Fire N independent attempts at ONE task and compare the branches they ship (ADR-0020 §1).

    Every attempt is told not to push, so the Hand-back is the only ship path and the N branches are
    named ``decode/<session-id>`` and directly comparable. Default: wait for all N, print the table
    and the diff commands. ``--detach``: print the N function-call ids and exit — the deployed app
    keeps running without the laptop.
    """
    error = attempts_input_error(attempts=attempts, repo=repo, sandbox_mode=sandbox_mode)
    if error is not None:
        raise click.ClickException(error)
    warning = attempts_input_warning(repo=repo, sandbox_mode=sandbox_mode)
    if warning is not None:
        click.echo(warning, err=True)

    calls = spawn_attempts(
        deployed_run_task(),
        task=task,
        count=attempts,
        repo=repo,
        sandbox_mode=sandbox_mode,
        model=model,
        timeout_seconds=timeout_seconds,
        max_requests=max_requests,
    )

    if detach:
        _echo_lines(detach_lines([call.object_id for call in calls], repo=repo))
        return

    click.echo(f"Decode: waiting for {len(calls)} attempt(s) — they run in parallel.", err=True)
    results = [collect_attempt(call, sandbox_mode=sandbox_mode) for call in calls]
    click.echo(attempts_table(results))
    _echo_lines([*attempts_notes(results), *compare_commands(results, repo=repo)])
    exit_code = attempts_exit_code(results)
    if exit_code:
        sys.exit(exit_code)


@remote.command("logs")
def logs() -> None:
    """Tail this environment's deployed app logs — where every run's answer and summary line land.

    ``DECODE_ENV`` picks WHICH deployment (ADR-0021 §2): a plain invocation tails
    ``decode-headless-local``, ``DECODE_ENV=prod decode remote logs`` tails ``decode-headless-prod``.
    """
    completed = subprocess.run(list(logs_argv()), check=False)
    if completed.returncode:
        sys.exit(completed.returncode)
