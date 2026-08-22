"""Register the Agent Version a Kitaru Worker spawns to replay a decode Session (ADR-0019 §4).

    uv run python scripts/register_kitaru_agent.py [--dry-run]

An operator script, not library code: it prints with ``click.echo`` and shells out to the ``kitaru``
CLI, so what it does is exactly what an operator could type — and ``--dry-run`` prints that command
instead of running it. It lives outside the ``decode`` import graph.

The Agent Version is decode's **replay context**, replicated rather than simulated:

* **command** — ``<repo>/.venv/bin/decode run``, with NO inline prompt. The prompt belongs to the
  Worker Task, which supplies it in ``KITARU_TASK_INPUTS`` (task 136). An absolute binary is used so
  the spawn depends on no ``PATH`` set-up in whatever shell started the Worker.
* **working dir** — a **Harness Home** OUTSIDE the repo. Every harness artifact anchors here
  (``.decode/sessions``, ``.decode/sandbox``, logs — ADR-0012 §6), so a replay writes nothing into
  the operator's working tree. The script refuses a Harness Home inside the repo.
* **env** — ``SANDBOX_MODE=docker`` + ``SANDBOX_REPO=<repo>``: replayed tool calls run in a docker
  Workspace that is a fresh ``git clone`` of this repo, never on the host tree. ``DECODE_ENV=local``
  pins the config surface, so the spawn does not inherit an operator's remote-bucket env.
* **secrets — deliberately none.** kitaru's Worker builds a task process env by layering the run
  spec (and any version-attached secret) ON TOP of its own ``os.environ``
  (``kitaru/worker/process.py::build_process_env``), so provider credentials already reach the run
  from the shell that started the Worker. Uploading them to the workspace would copy live keys off
  the host to buy nothing. Start the Worker from a shell that has them:

      set -a && . .env && set +a && kitaru worker start

The agent itself is never created here: ``kitaru agent version register <agent>`` resolves an
EXISTING agent and adds a version, so re-running this can never fork a second ``decode`` agent and
orphan the sessions recorded under the first.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import click

# The workspace agent decode's sessions are recorded under; reused, never re-created.
DEFAULT_AGENT = "decode"

# Where the Worker spawns `decode run`: the Harness Home, outside any repo (ADR-0012 §6).
DEFAULT_HARNESS_HOME = Path.home() / ".decode-kitaru-worker"

# The whole replay: a from-scratch clone, a docker Workspace, one long agent run. Generous, because a
# process the Worker kills mid-run is indistinguishable from an agent failure in the replay record.
DEFAULT_TIMEOUT_SECONDS = 1800

_DESCRIPTION = (
    "decode run under SANDBOX_MODE=docker over a clone of the course repo; the task arrives in "
    "KITARU_TASK_INPUTS (ADR-0019 §4)."
)


def build_run_env(*, repo: Path) -> dict[str, str]:
    """The run spec's process env: the replay context, and nothing secret (ADR-0019 §4).

    Three keys, each load-bearing: ``SANDBOX_MODE`` puts every tool call inside a docker Workspace,
    ``SANDBOX_REPO`` makes that Workspace a clone of ``repo``, and ``DECODE_ENV`` pins the config
    surface to ``local`` so the spawn cannot inherit a remote Environment Bucket from the Worker's
    shell. Provider credentials are NOT here — they ride the Worker's inherited env (module docstring).
    """
    return {
        "SANDBOX_MODE": "docker",
        "SANDBOX_REPO": str(repo),
        "DECODE_ENV": "local",
    }


def register_argv(
    *,
    agent: str,
    decode_bin: Path,
    harness_home: Path,
    repo: Path,
    timeout_seconds: int,
) -> list[str]:
    """The exact ``kitaru agent version register`` argv for this host.

    Raises ``ValueError`` when ``harness_home`` is inside ``repo``: the Worker's cwd is where every
    harness artifact and the Workspace clone itself land, so a Harness Home in the repo would write
    a replay's sessions, logs and sandbox into the operator's working tree.
    """
    if harness_home == repo or repo in harness_home.parents:
        raise ValueError(
            f"the Harness Home {harness_home} is inside the repo {repo}: a replay would write its "
            "sessions, logs and docker Workspace into your working tree. Pick a path outside it."
        )
    argv = ["kitaru", "agent", "version", "register", agent]
    argv += ["--command", f"{decode_bin} run"]
    argv += ["--working-dir", str(harness_home)]
    for key, value in build_run_env(repo=repo).items():
        argv += ["--env", f"{key}={value}"]
    argv += ["--timeout-seconds", str(timeout_seconds)]
    argv += ["--description", _DESCRIPTION]
    return argv


@click.command()
@click.option(
    "--agent",
    default=DEFAULT_AGENT,
    show_default=True,
    help="The EXISTING agent (name or UUID) to add a version to.",
)
@click.option(
    "--repo",
    default=str(Path(__file__).resolve().parents[1]),
    show_default="this repo",
    type=click.Path(path_type=Path),
    help="Repo cloned into the replay's docker Workspace.",
)
@click.option(
    "--harness-home",
    default=str(DEFAULT_HARNESS_HOME),
    show_default=True,
    type=click.Path(path_type=Path),
    help="The Worker's working dir; must be outside the repo.",
)
@click.option(
    "--decode-bin",
    default=None,
    type=click.Path(path_type=Path),
    help="The decode entrypoint the Worker spawns.  [default: <repo>/.venv/bin/decode]",
)
@click.option(
    "--timeout-seconds",
    default=DEFAULT_TIMEOUT_SECONDS,
    show_default=True,
    help="Process timeout for one replayed run.",
)
@click.option("--dry-run", is_flag=True, help="Print the kitaru command instead of running it.")
def main(
    agent: str,
    repo: Path,
    harness_home: Path,
    decode_bin: Path | None,
    timeout_seconds: int,
    dry_run: bool,
) -> None:
    """Register the next version of the Kitaru agent a Worker replays decode sessions with."""
    repo = repo.expanduser().resolve()
    harness_home = harness_home.expanduser().resolve()
    entrypoint = (decode_bin or repo / ".venv/bin/decode").expanduser().resolve()
    if not entrypoint.is_file():
        raise click.ClickException(
            f"no decode entrypoint at {entrypoint} — run `make install` in {repo}, or pass "
            "--decode-bin."
        )
    try:
        argv = register_argv(
            agent=agent,
            decode_bin=entrypoint,
            harness_home=harness_home,
            repo=repo,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from None

    click.echo(shlex.join(argv))
    if dry_run:
        click.echo("--dry-run: nothing was registered.")
        return

    # The Worker chdirs here for every spawn, so it must exist before the first task is claimed.
    harness_home.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(argv, check=False)
    if completed.returncode != 0:
        raise click.ClickException(
            f"`kitaru agent version register` failed (exit {completed.returncode}) — check "
            "`kitaru status` and that the agent exists (`kitaru agent list`)."
        )
    click.echo(f"Registered a new version of agent {agent!r}; Worker cwd: {harness_home}")


if __name__ == "__main__":
    sys.exit(main())
