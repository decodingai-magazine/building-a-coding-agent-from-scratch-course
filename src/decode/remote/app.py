"""The Modal Headless App — ``decode run`` executed on Modal, launched from ``decode remote`` (ADR-0020).

    decode remote deploy                      # once — builds the image, publishes run_task / nightly / webhook
    decode remote run "…" [--repo …] [--sandbox-mode none|modal]
    decode remote attempts "…" --repo <url> --attempts 5 --sandbox-mode modal [--detach]

    # no laptop at all — a nightly cron job (registered at deploy, from these env vars) …
    DECODE_NIGHTLY_CRON="0 2 * * *" DECODE_NIGHTLY_TASK="…" [DECODE_NIGHTLY_REPO=<url>] \\
        decode remote deploy
    # … and a webhook any system can POST a task to (the URL is printed by the deploy):
    curl -X POST "$WEBHOOK_URL" -H "Modal-Key: $MODAL_PROXY_TOKEN_ID" \\
        -H "Modal-Secret: $MODAL_PROXY_TOKEN_SECRET" -H 'content-type: application/json' \\
        -d '{"task": "…", "repo": "<url>", "sandbox_mode": "modal"}'

This module is the EXECUTE half of the launch-vs-execute split: it is what ``modal deploy -m
decode.remote.app`` publishes, and nothing else imports it — the ``decode remote`` subcommands
(:mod:`decode.remote.cli`) talk to the DEPLOYMENT by name, so the laptop never builds an image and
never runs an ephemeral app. Every decision a run is made of lives in :mod:`decode.remote.headless`,
``modal``-free; the three Functions here are thin: ``run_task`` executes one run in its container,
``nightly`` calls it on a schedule, ``webhook`` spawns it on a POST.

* **The Function runs the SAME console script a laptop runs** — ``decode run`` as a subprocess — so
  remote behavior cannot drift from local behavior (ADR-0020 §1).
* **The image is built in-app** (ADR-0020 §2) by :mod:`decode.remote.image`, shared verbatim with
  the Modal-hosted Kitaru Worker (``scripts/modal_kitaru_worker.py``): ``debian_slim`` +
  ``Image.uv_sync()`` for the locked dependencies, then this repo's source baked on top and installed
  with ``--no-deps``. No checked-in image recipe, no registry. Deps and source are separate layers,
  so editing decode rebuilds only the last two — and a code change needs a re-deploy before the
  next run. The console script therefore exists at ONE deterministic absolute path,
  :data:`DECODE_BIN` — the same one the Worker's Agent Version is registered with.
* **One environment, one deployment** (ADR-0021 §2). ``DECODE_ENV`` is read from the DEPLOYING
  laptop's env, names the app and its Secret — both ``decode-headless-<env>`` — and is baked into
  the image; the Secret carries credentials only. So ``DECODE_ENV=prod decode remote deploy``
  publishes ``decode-headless-prod``, running with the Secret of that name, filing Opik traces under
  ``decode-prod`` and nesting its sandboxes in ``decode-sandbox-prod``. The Secret IS the whole
  config surface at every environment — there is no second store to fall back to. Create it once,
  values never committed::

      modal secret create decode-headless-prod LLM_PROVIDER=… GEMINI_API_KEY=… [SANDBOX_GIT_TOKEN=…]

  A ``DECODE_ENV`` left INSIDE a Secret is refused at startup (:func:`decode.remote.headless
  .decode_env_mismatch_error`): it would mean the app's name and the run's environment disagree.

* **Two triggers need no laptop** (ADR-0020 Amendment §8). ``nightly`` is a Modal cron: the schedule
  and the job (task / repo / mode / ceilings) are read from ``DECODE_NIGHTLY_*`` on the laptop AT
  DEPLOY and travel with the deployment (schedule on the Function, job as a ``Secret.from_dict``
  env) — no env, no schedule, so a plain deploy never starts billing anyone's nights. ``webhook`` is
  a POST endpoint behind Modal proxy auth that ``spawn``s ``run_task`` and returns the call id at
  once: a CI step, a ticket bot, a Zapier hook — anything that can POST — becomes a headless run.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

import click
import modal

from decode.remote.headless import (
    DEFAULT_SANDBOX_MODE,
    DEFAULT_TIMEOUT_SECONDS,
    FUNCTION_TIMEOUT_SECONDS,
    NIGHTLY_CRON_ENV,
    NIGHTLY_REGISTERED_FORMAT,
    NIGHTLY_TASK_ENV,
    NIGHTLY_UNCONFIGURED_MESSAGE,
    REPO_CLONE_DIR,
    SANDBOX_MODE_REJECTED_EXIT,
    WEB_PACKAGES,
    WebhookRequest,
    app_name,
    build_result,
    decode_env_mismatch_error,
    deploy_time_decode_env,
    execute_run,
    nightly_config_error,
    nightly_cron,
    nightly_job_env,
    nightly_run_kwargs,
    secret_name,
    webhook_request_error,
    webhook_response,
    webhook_spawn_kwargs,
)
from decode.remote.image import DECODE_BIN, HARNESS_HOME, build_image

# The image is built by ``decode.remote.image``, shared with the Modal-hosted Kitaru Worker
# (``scripts/modal_kitaru_worker.py``) — one build, one layout, one set of absolute paths. The
# webhook's FastAPI is the only layer the two apps do not share; the locked-deps layer below it is.
# The environment this deployment IS, read from the deploying laptop once (ADR-0021 §2). It names
# the app and the Secret below and is baked into the image, so a container can never disagree with
# the app it runs in about which environment it is.
DECODE_ENV = deploy_time_decode_env(os.environ)

IMAGE = build_image(
    decode_env=DECODE_ENV, extra_dirs=(REPO_CLONE_DIR,), extra_packages=WEB_PACKAGES
)
SECRET_NAME = secret_name(DECODE_ENV)

# Re-exported: the in-image paths are read from HERE by the Agent Version registration's drift guard
# (``scripts/register_kitaru_agent.py``) — they are defined once, in ``decode.remote.image``.
__all__ = ["DECODE_BIN", "HARNESS_HOME", "app", "nightly", "run_task", "webhook"]

app = modal.App(app_name(DECODE_ENV))


def nightly_schedule(env: Mapping[str, str]) -> modal.Cron | None:
    """The ``modal.Cron`` to register at deploy, or ``None`` — :func:`nightly_cron` wrapped."""
    cron = nightly_cron(env)
    return modal.Cron(cron) if cron else None


# --- the Modal surface ------------------------------------------------------------------------------


@app.function(
    image=IMAGE,
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    timeout=FUNCTION_TIMEOUT_SECONDS,
)
def run_task(
    task: str,
    repo: str | None = None,
    sandbox_mode: str = DEFAULT_SANDBOX_MODE,
    model: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_requests: int | None = None,
) -> dict[str, object]:
    """Run ONE headless decode task in this container and return its small result payload.

    The whole Function is :func:`decode.remote.headless.execute_run` on the container's env: the
    mode guard, the git credential helper, the harness clone under ``none``, the ``decode run``
    subprocess, the log read-back. Nothing about the agent is re-implemented here (ADR-0020 §1).

    One guard runs first: a Secret that carries its own ``DECODE_ENV`` disagreeing with the deployed
    one is refused (ADR-0021 §3), because the app name, the Secret name and the run's environment are
    supposed to be the same fact said once.
    """
    mismatch = decode_env_mismatch_error(os.environ, deployed_env=DECODE_ENV)
    if mismatch is not None:
        click.echo(mismatch, err=True)
        return build_result(
            sandbox_mode=sandbox_mode,
            repo=repo,
            exit_code=SANDBOX_MODE_REJECTED_EXIT,
            stdout=mismatch,
            log_text="",
        )
    return execute_run(
        task=task,
        repo=repo,
        sandbox_mode=sandbox_mode,
        model=model,
        timeout_seconds=timeout_seconds,
        max_requests=max_requests,
    )


# --- triggers without a laptop (ADR-0020 Amendment §8) ----------------------------------------------

# Deploy-time guard: a schedule with no task must die on the laptop, not at 2am in a container —
# and a schedule that IS registered should say so, once, where the operator is looking.
_nightly_error = nightly_config_error(os.environ)
if _nightly_error is not None:
    click.echo(_nightly_error, err=True)
    sys.exit(1)
if nightly_cron(os.environ) is not None:
    click.echo(
        NIGHTLY_REGISTERED_FORMAT.format(
            cron=os.environ[NIGHTLY_CRON_ENV].strip(), task=os.environ[NIGHTLY_TASK_ENV].strip()
        ),
        err=True,
    )


@app.function(
    image=IMAGE,
    secrets=[
        modal.Secret.from_name(SECRET_NAME),
        modal.Secret.from_dict(nightly_job_env(os.environ)),
    ],
    schedule=nightly_schedule(os.environ),
    timeout=FUNCTION_TIMEOUT_SECONDS,
)
def nightly() -> dict[str, object]:
    """The cron trigger: ONE headless run of the job this deployment was given, on its schedule.

    Configured entirely at ``decode remote deploy`` from the laptop's ``DECODE_NIGHTLY_*`` env (see
    the module docstring); without ``DECODE_NIGHTLY_CRON`` no schedule exists and this Function is
    inert. It runs :func:`run_task` in THIS container (``.local()``) — same image, same Secret, same
    subprocess — so a nightly run is byte-for-byte a ``decode remote run`` nobody had to type.
    Reads its result back in ``decode remote logs`` (and, with a repo under ``modal`` mode, as a
    ``decode/<session-id>`` branch on origin).
    """
    kwargs = nightly_run_kwargs(os.environ)
    if kwargs is None:
        click.echo(NIGHTLY_UNCONFIGURED_MESSAGE, err=True)
        return build_result(
            sandbox_mode=DEFAULT_SANDBOX_MODE,
            repo=None,
            exit_code=SANDBOX_MODE_REJECTED_EXIT,
            stdout=NIGHTLY_UNCONFIGURED_MESSAGE,
            log_text="",
        )
    return run_task.local(**kwargs)


@app.function(image=IMAGE)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
def webhook(request: WebhookRequest) -> dict[str, object]:
    """The event trigger: POST a task, get a call id back, the run happens without you.

    Behind Modal proxy auth (``Modal-Key`` / ``Modal-Secret`` headers — the same proxy token pair
    the open-model endpoints use), so the URL the deploy prints is not a public "spend my tokens"
    button. Fire-and-forget by design: a webhook caller (CI, a ticket bot, a scheduler) has
    seconds, an agent run has minutes, so the endpoint ``spawn``s :func:`run_task` on this deployed
    app and answers at once with where to watch. Runs with no Secret of its own — it spawns, it
    does not run.
    """
    error = webhook_request_error(request)
    if error is not None:
        # In the image only (WEB_PACKAGES); never on the laptop, so only the rejecting path pays for it.
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=error)
    call = run_task.spawn(**webhook_spawn_kwargs(request))
    return webhook_response(call.object_id, request, decode_env=DECODE_ENV)
