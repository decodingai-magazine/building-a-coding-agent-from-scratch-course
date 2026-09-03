"""Register the Agent Version a Kitaru Worker spawns to replay a decode Session (ADR-0019 §4).

    uv run python scripts/register_kitaru_agent.py [--dry-run]
    uv run python scripts/register_kitaru_agent.py --sandbox-mode none \\
        --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check

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

``--sandbox-mode`` picks WHERE those replayed tool calls land, and is the only difference between
the two Workers decode runs (ADR-0020 §5):

* ``docker`` (the default) — the laptop Worker, agent v2's spec, unchanged.
* ``none`` — the Modal-hosted Worker (task 145): the gVisor container is itself the isolation, so
  there is no ``SANDBOX_REPO`` at all (decode refuses a repo under ``none``, ADR-0012 §3) and tool
  calls land in the Worker's in-container Harness Home. Its ``--decode-bin`` and ``--harness-home``
  are paths inside the worker IMAGE, so pass ``--skip-bin-check`` — the laptop cannot stat them.
  They must be absolute: nothing resolves them here, so a relative one is both meaningless to the
  Worker and invisible to the Harness-Home-inside-repo guard.
* ``modal`` — the same container, but decode nests a Modal Sandbox and clones ``--repo`` into it.

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

# The Sandbox Modes a Worker can replay under; ``docker`` first, because it is the default.
SANDBOX_MODES = ("docker", "none", "modal")
DEFAULT_SANDBOX_MODE = "docker"

# What `kitaru agent get decode` shows an operator, per mode — the one line that tells the laptop
# version and the Modal-worker version apart. Every mode names its SANDBOX_MODE verbatim.
_DESCRIPTIONS = {
    "docker": (
        "decode run under SANDBOX_MODE=docker over a clone of the course repo; the task arrives in "
        "KITARU_TASK_INPUTS (ADR-0019 §4)."
    ),
    # No apostrophe anywhere in these: the printed argv is shlex-quoted, and one apostrophe turns a
    # paste-able command into '"'"' noise.
    "none": (
        "decode run under SANDBOX_MODE=none inside the Kitaru Worker container (no repo clone — the "
        "container is the isolation); the task arrives in KITARU_TASK_INPUTS (ADR-0020 §5)."
    ),
    "modal": (
        "decode run under SANDBOX_MODE=modal over a clone of the course repo in a nested Modal "
        "Sandbox; the task arrives in KITARU_TASK_INPUTS (ADR-0020 §5)."
    ),
}


def build_run_env(*, repo: Path, sandbox_mode: str = DEFAULT_SANDBOX_MODE) -> dict[str, str]:
    """The run spec's process env: the replay context, and nothing secret (ADR-0019 §4).

    Each key is load-bearing: ``SANDBOX_MODE`` picks the Workspace every tool call runs in,
    ``SANDBOX_REPO`` makes that Workspace a clone of ``repo``, and ``DECODE_ENV`` pins the config
    surface to ``local`` so the spawn cannot inherit a remote Environment Bucket from the Worker's
    shell. Provider credentials are NOT here — they ride the Worker's inherited env (module docstring).

    Under ``none`` the repo is dropped entirely: decode rejects a repo when there is no sandbox to
    clone it into (ADR-0012 §3), so shipping one would fail every spawn at pre-flight. The Worker's
    container is the isolation and its Harness Home is the tool scope (ADR-0020 §5).
    """
    env = {"SANDBOX_MODE": sandbox_mode}
    if sandbox_mode != "none":
        env["SANDBOX_REPO"] = str(repo)
    env["DECODE_ENV"] = "local"
    return env


def _check_in_image_path(option: str, path: Path) -> None:
    """Refuse a relative in-image path — ``--skip-bin-check`` can neither resolve nor check one.

    Under the flag the path is registered verbatim (never stat-ed, resolved or created), so a
    relative one has no meaning: the Worker chdirs into a container filesystem that shares no cwd
    with the operator's shell. It is also invisible to the Harness-Home-inside-repo guard, which
    compares a fully resolved ``repo`` against it — ``.decode/worker`` would sail through and
    register a replay that writes into the working tree.
    """
    if not path.is_absolute():
        raise click.ClickException(
            f"{option} {path} is relative, but --skip-bin-check says it is a path inside the worker "
            "image: in-image paths must be absolute (e.g. /harness). A relative one cannot be "
            "checked against the repo and means nothing to the Worker, which chdirs into a "
            "container."
        )


def register_argv(
    *,
    agent: str,
    decode_bin: Path,
    harness_home: Path,
    repo: Path,
    timeout_seconds: int,
    sandbox_mode: str = DEFAULT_SANDBOX_MODE,
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
    for key, value in build_run_env(repo=repo, sandbox_mode=sandbox_mode).items():
        argv += ["--env", f"{key}={value}"]
    argv += ["--timeout-seconds", str(timeout_seconds)]
    argv += ["--description", _DESCRIPTIONS[sandbox_mode]]
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
    help="Repo cloned into the replay's Workspace; ignored under --sandbox-mode none.",
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
    "--sandbox-mode",
    default=DEFAULT_SANDBOX_MODE,
    show_default=True,
    type=click.Choice(SANDBOX_MODES),
    help=(
        "Where replayed tool calls run: docker (the laptop Worker) | none (the Modal Worker's own "
        "container, no repo clone) | modal (a nested Modal Sandbox)."
    ),
)
@click.option(
    "--skip-bin-check",
    is_flag=True,
    help=(
        "The paths are inside the Modal worker image, not on this machine: register --decode-bin "
        "and --harness-home verbatim, without stat-ing, resolving or creating them. Both must then "
        "be absolute. A typo surfaces only when the Worker fails to spawn its first replay, so copy "
        "the paths from decode/remote/image.py (DECODE_BIN, HARNESS_HOME)."
    ),
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
    sandbox_mode: str,
    skip_bin_check: bool,
    timeout_seconds: int,
    dry_run: bool,
) -> None:
    """Register the next version of the Kitaru agent a Worker replays decode sessions with."""
    repo = repo.expanduser().resolve()
    harness_home = harness_home.expanduser()
    entrypoint = (decode_bin or repo / ".venv/bin/decode").expanduser()
    if skip_bin_check:
        # In-image paths are registered verbatim: resolving them against the laptop's filesystem
        # would rewrite a container path (symlinks, /tmp on macOS) into one the Worker cannot chdir
        # into. Since they are never resolved, they must already be absolute.
        _check_in_image_path("--harness-home", harness_home)
        _check_in_image_path("--decode-bin", entrypoint)
    else:
        harness_home = harness_home.resolve()
        entrypoint = entrypoint.resolve()
        if not entrypoint.is_file():
            raise click.ClickException(
                f"no decode entrypoint at {entrypoint} — run `make install` in {repo}, pass "
                "--decode-bin, or pass --skip-bin-check if this is a path inside the Modal worker "
                "image."
            )
    try:
        argv = register_argv(
            agent=agent,
            decode_bin=entrypoint,
            harness_home=harness_home,
            repo=repo,
            timeout_seconds=timeout_seconds,
            sandbox_mode=sandbox_mode,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from None

    click.echo(shlex.join(argv))
    if dry_run:
        click.echo("--dry-run: nothing was registered.")
        return

    # The Worker chdirs here for every spawn, so it must exist before the first task is claimed —
    # locally. An in-image path is created by the image build (decode.remote.image.build_image), never here:
    # making /harness on the operator's laptop would be litter, and often a permission error.
    if not skip_bin_check:
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
