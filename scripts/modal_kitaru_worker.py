"""The Modal-hosted Kitaru Worker — replays execute off the laptop (ADR-0020 §5).

    # publish the app (the image is built HERE, shared with the headless app):
    uv run modal deploy scripts/modal_kitaru_worker.py

    # start a worker that outlives the terminal (up to Modal's 24h function ceiling):
    uv run modal run --detach scripts/modal_kitaru_worker.py [--concurrency 4] \\
        [--agent-version-id <uuid>] [--name decode-modal-worker]

    # watch it / stop it, from anywhere:
    uv run kitaru worker list                 # the row named decode-modal-worker, live: True
    modal app logs decode-kitaru-worker
    modal app stop decode-kitaru-worker

An operator script, not library code: it lives outside the ``decode`` import graph, prints with
``click.echo``, and its Function runs the SAME console script a laptop runs — ``kitaru worker start``
as a subprocess. The Kitaru server executes nothing; a Worker is a process that CLAIMS tasks, and
this one just happens to sit in a gVisor container instead of on a laptop.

* **Claims are scoped** to ``agent`` (replays) and ``evaluator`` work. ``importer`` is deliberately
  absent: importer jobs read export files that exist on the operator's machine and nowhere in this
  container, so claiming one would fail it. ``--agent-version-id`` narrows the agent claim further,
  to ``agent=<id>`` — worth using while the laptop Worker is also polling, because the two would
  otherwise race for the same task and each one can only run its own Agent Version (the laptop's v2
  is ``SANDBOX_MODE=docker``; there is no Docker daemon here, and the v3 in-image paths do not exist
  on a laptop).
* **``KITARU_AGENT_ID`` is scrubbed** from the worker's env with one logged line. The Secret is not
  supposed to carry it (ADR-0020 §4), but if one is ever added, every spawned replay would inherit
  it, the Recording Seam would probe an agents route the task-scoped token cannot use, and the run
  would hard-fail with ``403: Task credentials are not accepted on this route`` (ADR-0019 §3,
  tasks/139, 08_evals_replays.md §7.3). The scrub is the backstop; the Secret's composition is the
  rule.
* **The worker spawns agent version 3** — ``decode run`` under ``SANDBOX_MODE=none`` with
  :data:`DECODE_BIN` and :data:`HARNESS_HOME` as its in-image paths, registered from a laptop with
  ``scripts/register_kitaru_agent.py --sandbox-mode none --skip-bin-check``. Both paths come from
  :mod:`scripts.modal_image`, so the image and the registration cannot drift apart. Pin the version
  when you replay — ``--agent decode@3``, never "latest": version 4 is a QA-accident duplicate of 3
  (see ``tasks/done/144-…``), and versions are immutable.
* **Secrets** ride the ``decode-kitaru-worker`` :class:`modal.Secret` — ``KITARU_API_URL`` +
  ``KITARU_API_KEY`` + provider keys, and deliberately NO ``KITARU_AGENT_ID``. Secret env outranks
  ``.env`` in Settings precedence, so ``DECODE_ENV`` stays ``local`` (ADR-0020 §4). Create it once,
  values never committed::

      modal secret create decode-kitaru-worker KITARU_API_URL=… KITARU_API_KEY=… GEMINI_API_KEY=…

  ``KITARU_API_KEY`` must be a **control plane** key (``ZENPROKEY_…``) on a managed workspace: a
  container has no ``kitaru login`` store, and a workspace-local key is rejected server-side under
  control-plane authentication. The kitaru client exchanges the key for a session token and renews
  it, so the worker stays authenticated for its whole life. The key never reaches an argv — only the
  Secret's process env. The tasks the worker spawns do NOT inherit it: kitaru clears
  ``KITARU_API_KEY`` from every task process and hands it a task-scoped token instead
  (``kitaru/worker/process.py::build_process_env``).
* **The worker dies at the 24h Modal ceiling** and is re-launched with the one ``modal run --detach``
  command above; whatever it still held is kitaru's own task-timeout story, not something this
  script engineers around (ADR-0020 §5).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import click
import modal

from scripts.modal_image import DECODE_BIN, HARNESS_HOME, KITARU_BIN, build_image

# --- the app and its image --------------------------------------------------------------------------

APP_NAME = "decode-kitaru-worker"

# The Modal Secret this Function runs with (ADR-0020 §4); operator-created, never committed.
SECRET_NAME = "decode-kitaru-worker"

# The same image the Modal Headless App runs, built once in scripts/modal_image.py: the Worker spawns
# `decode run` from DECODE_BIN, so the two apps must not drift into two layouts.
IMAGE = build_image()

app = modal.App(APP_NAME)

# Modal's ceiling for a Function. A Worker is long-running by nature, so it simply takes the maximum
# and is re-launched by hand afterwards (ADR-0020 §5).
FUNCTION_TIMEOUT_SECONDS = 24 * 60 * 60

# --- what one worker is --------------------------------------------------------------------------

# How many tasks this worker holds at once. Every held replay is a `decode run` process in THIS
# container, so the ceiling is the container's, not the workspace's.
DEFAULT_CONCURRENCY = 4

# The name in `kitaru worker list` — it is how an operator tells the Modal worker from the laptop
# one, which is otherwise just another hostname in the list.
DEFAULT_WORKER_NAME = "decode-modal-worker"

# The work this container can actually do. `importer` is missing on purpose (module docstring).
WORKER_CLAIMS = ("agent", "evaluator")

# The variable that must not survive into the worker's env, and the one line that says why.
AGENT_ID_ENV = "KITARU_AGENT_ID"
AGENT_ID_SCRUB_LINE = (
    f"Decode: dropped {AGENT_ID_ENV} from this worker's environment — a spawned replay would "
    "inherit it, probe an agents route its task-scoped token cannot use, and hard-fail with 403; "
    f"the {SECRET_NAME} secret is not supposed to carry it (ADR-0020 §4)."
)

# The variables kitaru itself reads to reach the workspace: the URL is mandatory (the worker copies
# it into every task's env), and one of the two credentials is what keeps it from polling 401s.
API_URL_ENV = "KITARU_API_URL"
API_KEY_ENV = "KITARU_API_KEY"
API_TOKEN_ENV = "KITARU_API_TOKEN"

MISSING_URL_MESSAGE = (
    f"Decode: {API_URL_ENV} is not set in this container, so the worker has no workspace to claim "
    f"tasks from — add it to the {SECRET_NAME} secret (`kitaru status` names the URL)."
)
MISSING_CREDENTIAL_MESSAGE = (
    f"Decode: neither {API_KEY_ENV} nor {API_TOKEN_ENV} is set in this container. A container has no "
    f"`kitaru login` store, so the worker would poll unauthenticated for a day — add a control plane "
    f"API key (ZENPROKEY_…) to the {SECRET_NAME} secret."
)

# The Harness Home is the cwd kitaru chdirs into to spawn every claimed task, so a container that
# cannot create it cannot run one — said in the same voice as the lines above, not as a traceback.
HARNESS_HOME_ERROR_FORMAT = (
    "Decode: could not create the Worker's Harness Home at {path} ({error}) — it is the working "
    "directory every claimed replay is spawned in, so the worker was not started."
)

# Nothing was started, so nothing is draining: a plain non-zero code, no traceback.
NOT_CONFIGURED_EXIT = 2

STARTING_LINE_FORMAT = (
    "Decode: starting the Kitaru Worker — name={name} concurrency={concurrency} claims={claims} "
    "cwd={cwd}"
)


# --- pure helpers: everything one worker is decided by ---------------------------------------------


def worker_claims(agent_version_id: str | None = None) -> list[str]:
    """The claims this worker serves, narrowed to one Agent Version when asked (ADR-0020 §5).

    Args:
        agent_version_id: Kitaru Agent Version id the ``agent`` claim is restricted to. Unset, the
            worker claims replays of any version — which is only safe while it is the only Worker
            polling, since a v2 (docker) replay claimed here has no daemon to run in.

    Returns:
        The claim strings, in ``--claim`` order.
    """
    claims = list(WORKER_CLAIMS)
    if agent_version_id:
        claims[claims.index("agent")] = f"agent={agent_version_id}"
    return claims


def worker_argv(
    *,
    concurrency: int,
    agent_version_id: str | None = None,
    name: str = DEFAULT_WORKER_NAME,
    kitaru_bin: str = KITARU_BIN,
) -> list[str]:
    """The exact ``kitaru worker start`` argv this container executes — the laptop's surface, verbatim.

    Args:
        concurrency: Maximum tasks held at once.
        agent_version_id: Agent Version the ``agent`` claim is restricted to, if any.
        name: Worker name shown by ``kitaru worker list``.
        kitaru_bin: The in-image ``kitaru`` console script.

    Returns:
        The argv, credential-free: the workspace URL and key reach the worker as process env only.
    """
    argv = [kitaru_bin, "worker", "start", "--name", name, "--concurrency", str(concurrency)]
    for claim in worker_claims(agent_version_id):
        argv += ["--claim", claim]
    return argv


def agent_id_scrub_line(base_env: Mapping[str, str]) -> str | None:
    """The ONE line announcing a dropped :data:`AGENT_ID_ENV`, or ``None`` when there is none.

    Args:
        base_env: The container's process environment.

    Returns:
        The line to log, naming the variable and the 403 it prevents — never its value, which is a
        workspace id an operator has no reason to read out of a log.
    """
    return AGENT_ID_SCRUB_LINE if AGENT_ID_ENV in base_env else None


def worker_env(base_env: Mapping[str, str]) -> dict[str, str]:
    """The worker's process env: the container's secrets, minus the one that breaks every replay.

    Args:
        base_env: The container's process environment.

    Returns:
        A copy without :data:`AGENT_ID_ENV`. Everything else — provider keys, the workspace URL and
        credential, ``DECODE_ENV`` — is exactly what the Secret handed the container, because the
        worker layers a spawned task's env on top of its own (``kitaru/worker/process.py``).
    """
    return {key: value for key, value in base_env.items() if key != AGENT_ID_ENV}


def credential_error(env: Mapping[str, str]) -> str | None:
    """ONE friendly line if this container cannot reach the workspace, else ``None``.

    Checked before the worker starts, because the failure it prevents is silent: a worker with no
    credential does not crash, it polls — for up to a day, claiming nothing, while an operator
    watches ``kitaru worker list`` for a row that never appears.

    Args:
        env: The worker's process environment.

    Returns:
        The line to print, naming the missing VARIABLE and never a value.
    """
    if not env.get(API_URL_ENV):
        return MISSING_URL_MESSAGE
    if not (env.get(API_KEY_ENV) or env.get(API_TOKEN_ENV)):
        return MISSING_CREDENTIAL_MESSAGE
    return None


def ensure_harness_home(path: str) -> str | None:
    """Create the Worker's working dir, or return ONE friendly line saying why it could not.

    The cwd every spawned replay inherits (ADR-0012 §6): agent version 3's run spec names this path
    as its ``--working-dir``; kitaru chdirs into it to spawn ``decode run``, so a missing directory
    is a spawn failure on every claimed task rather than a worker that fails loudly at startup.

    Normally a no-op re-creation — the image ``mkdir -p``s it at build time — so a failure here means
    the container's filesystem is not what the image promised (permissions, read-only mount, full
    disk). That reads like the credential pre-flight, not like a traceback: an operator wants the
    path and the OS's own reason, and nothing else.

    Args:
        path: The in-image Harness Home.

    Returns:
        The line to print, naming the path and the OS error, or ``None`` once the directory exists.
    """
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return HARNESS_HOME_ERROR_FORMAT.format(path=path, error=error)
    return None


# --- the Modal surface ------------------------------------------------------------------------------


@app.function(
    image=IMAGE,
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    timeout=FUNCTION_TIMEOUT_SECONDS,
)
def run_worker(
    concurrency: int = DEFAULT_CONCURRENCY,
    agent_version_id: str | None = None,
    name: str = DEFAULT_WORKER_NAME,
) -> int:
    """Run ONE Kitaru Worker in this container until it dies, and return its exit code.

    The whole Function is: make the Harness Home, drop the variable that would 403 every replay,
    refuse to start if this container cannot authenticate, then run the same ``kitaru worker start``
    an operator runs on a laptop. Both pre-flight refusals — an unmakeable Harness Home, a missing
    credential — cost ONE line and :data:`NOT_CONFIGURED_EXIT`, never a traceback. Nothing about claiming or replaying is re-implemented here — that
    is the point of ADR-0020 §1.

    Args:
        concurrency: Maximum tasks held at once.
        agent_version_id: Agent Version the ``agent`` claim is restricted to, if any.
        name: Worker name shown by ``kitaru worker list``.

    Returns:
        The worker's exit code, or :data:`NOT_CONFIGURED_EXIT` when it was never started.
    """
    harness_home_error = ensure_harness_home(HARNESS_HOME)
    if harness_home_error is not None:
        click.echo(harness_home_error, err=True)
        return NOT_CONFIGURED_EXIT

    scrub_line = agent_id_scrub_line(os.environ)
    if scrub_line is not None:
        click.echo(scrub_line, err=True)
    env = worker_env(os.environ)

    error = credential_error(env)
    if error is not None:
        click.echo(error, err=True)
        return NOT_CONFIGURED_EXIT

    argv = worker_argv(concurrency=concurrency, agent_version_id=agent_version_id, name=name)
    click.echo(
        STARTING_LINE_FORMAT.format(
            name=name,
            concurrency=concurrency,
            claims=",".join(worker_claims(agent_version_id)),
            cwd=HARNESS_HOME,
        ),
        err=True,
    )
    # Both streams are INHERITED, so the worker's own output IS the Function log — a silent worker is
    # a healthy worker (08_evals_replays.md §7.7), and a claimed task's output shows up live.
    completed = subprocess.run(argv, cwd=HARNESS_HOME, env=env, check=False)
    click.echo(f"Decode: the Kitaru Worker exited with {completed.returncode}.", err=True)
    return completed.returncode


@app.local_entrypoint()
def main(
    concurrency: int = DEFAULT_CONCURRENCY,
    agent_version_id: str | None = None,
    name: str = DEFAULT_WORKER_NAME,
) -> None:
    """Start the Modal-hosted Kitaru Worker; exit with its code when it eventually dies.

    Use ``modal run --detach`` — without it, the worker stops when this terminal does, which is the
    laptop-bound thing this app exists to escape.
    """
    exit_code = run_worker.remote(
        concurrency=concurrency, agent_version_id=agent_version_id, name=name
    )
    if exit_code:
        sys.exit(int(exit_code))


__all__ = ["DECODE_BIN", "HARNESS_HOME", "KITARU_BIN"]
