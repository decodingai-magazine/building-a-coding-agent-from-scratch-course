"""Register everything decode needs on ONE Kitaru Server, idempotently (ADR-0022 §10).

    uv run python scripts/bootstrap_kitaru.py                        # targets $KITARU_API_URL
    uv run python scripts/bootstrap_kitaru.py --server http://localhost:8000
    uv run python scripts/bootstrap_kitaru.py --dry-run              # print the argv, change nothing

A Kitaru Server is one URL — the local OSS deployment ``kitaru login --local`` provisions
(``make kitaru-local``), or a managed workspace. Everything decode needs on it is the same on every
one of them, so it is one script rather than a runbook section of copy-pasted commands:

* the agent ``decode`` and its two Agent Versions — the replay run spec a Worker spawns, one per
  Worker (``SANDBOX_MODE=docker`` for the laptop, ``none`` for the Modal-hosted Worker's own
  container — ADR-0019 §4, ADR-0020 §5); this file replaces the DELETED
  ``scripts/register_kitaru_agent.py``, whose pure builders (:func:`build_run_env`,
  :func:`register_argv`) moved here unchanged;
* the importer ``opik`` (``importers/opik_importer.py``), which turns an Opik trace export into
  Sessions — the parser behind ``python -m evals kitaru import``;
* every evaluator under ``evaluators/`` (entrypoint ``evaluate``).

**Idempotency is read-then-register, NOT a retry key.** Verified against kitaru 0.26.0: a duplicate
``agent register`` / ``importer register`` / ``evaluator register`` is a 409 ``conflict``, and
``… version register`` ALWAYS creates the next version — so a naive re-run would leave v2, v3, v4 of
an identical spec behind. This script reads the server's current state first and registers only what
is absent or actually different:

* an Agent Version is matched on its whole run spec (command / working dir / env / timeout), so a
  re-run changes nothing and a moved venv registers exactly one new version;
* an importer / evaluator version is matched on the SHA-256 digest of the script it would upload,
  carried in ``--display-version`` (``sha-<12 hex>``) — the server stores scripts as opaque blobs,
  so the digest is the only way to ask "is this file already registered?".

An operator script, not library code: it prints with ``click.echo``, shells out to the ``kitaru``
CLI (so what it does is what an operator could type, and ``--dry-run`` prints exactly that), and
imports nothing from ``evals`` — ``python scripts/bootstrap_kitaru.py`` puts only ``scripts/`` on
``sys.path``, so its one small subprocess helper is deliberately its own rather than
``evals.harness.kitaru_cli``'s.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

# The workspace agent decode's sessions are recorded under; created once, then reused forever.
DEFAULT_AGENT = "decode"

# Where the laptop Worker spawns `decode run`: the Harness Home, outside any repo (ADR-0012 §6).
DEFAULT_HARNESS_HOME = Path.home() / ".decode-kitaru-worker"

# The whole replay: a from-scratch clone, a docker Workspace, one long agent run. Generous, because a
# process the Worker kills mid-run is indistinguishable from an agent failure in the replay record.
DEFAULT_TIMEOUT_SECONDS = 1800

# The Sandbox Modes a Worker can replay under; `docker` first, because it is the laptop default.
SANDBOX_MODES = ("docker", "none", "modal")
DEFAULT_SANDBOX_MODE = "docker"

# The importer that reads an Opik trace export (`python -m evals kitaru import` produces its input).
IMPORTER_NAME = "opik"
IMPORTER_SCRIPT = Path("importers/opik_importer.py")
IMPORTER_ENTRYPOINT = "parse"
IMPORTER_PROVIDER = "opik"

# Every evaluator in the repo is registered under its file name (`decode_bad_request_400.py` →
# `decode-bad-request-400`), with kitaru's own evaluator entrypoint.
EVALUATORS_DIR = Path("evaluators")
EVALUATOR_ENTRYPOINT = "evaluate"

# The connection variable a Kitaru Server is named by when `--server` is not passed (ADR-0019 §3).
KITARU_API_URL_ENV = "KITARU_API_URL"

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


class BootstrapError(Exception):
    """One step could not be registered — surfaced as ONE CLI line."""


@dataclass(frozen=True, slots=True)
class VersionSpec:
    """One Agent Version this server should have: the replay context of one Worker."""

    sandbox_mode: str
    decode_bin: Path
    harness_home: Path
    repo: Path
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the table the script prints: what exists now, and how it got there."""

    kind: str
    ref: str
    id: str
    action: str


# --- the pure builders (moved verbatim from the deleted scripts/register_kitaru_agent.py) --------


def build_run_env(*, repo: Path, sandbox_mode: str = DEFAULT_SANDBOX_MODE) -> dict[str, str]:
    """The run spec's process env: the replay context, and nothing secret (ADR-0019 §4).

    Each key is load-bearing: ``SANDBOX_MODE`` picks the Workspace every tool call runs in, and
    ``SANDBOX_REPO`` makes that Workspace a clone of ``repo``. ``DECODE_ENV`` is deliberately NOT
    here (ADR-0021 §4): it names the environment a replay runs AS, and that is the Worker's to say —
    a replay spawned by ``decode-kitaru-worker-prod`` should file its traces under ``decode-prod``,
    not under whatever a registration script guessed months earlier. Provider credentials are NOT
    here either: kitaru's Worker layers the run spec ON TOP of its own ``os.environ``
    (``kitaru/worker/process.py::build_process_env``), so they ride the shell that started the
    Worker — uploading them to the workspace would copy live keys off the host to buy nothing.

    Under ``none`` the repo is dropped entirely: decode rejects a repo when there is no sandbox to
    clone it into (ADR-0012 §3), so shipping one would fail every spawn at pre-flight. The Worker's
    container is the isolation and its Harness Home is the tool scope (ADR-0020 §5).
    """
    env = {"SANDBOX_MODE": sandbox_mode}
    if sandbox_mode != "none":
        env["SANDBOX_REPO"] = str(repo)
    return env


def register_argv(
    *,
    agent: str,
    decode_bin: Path,
    harness_home: Path,
    repo: Path,
    timeout_seconds: int,
    sandbox_mode: str = DEFAULT_SANDBOX_MODE,
    server: str | None = None,
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
    return _with_server(argv, server)


def agent_register_argv(*, agent: str, spec: VersionSpec, server: str | None = None) -> list[str]:
    """``kitaru agent register`` — creates the agent AND its first version in one call.

    The first version carries the same run spec :func:`register_argv` would register, so the agent's
    v1 is already a usable laptop Worker spec rather than an empty placeholder.
    """
    version_argv = register_argv(
        agent=agent,
        decode_bin=spec.decode_bin,
        harness_home=spec.harness_home,
        repo=spec.repo,
        timeout_seconds=spec.timeout_seconds,
        sandbox_mode=spec.sandbox_mode,
    )
    argv = ["kitaru", "agent", "register", agent, *version_argv[5:]]
    return _with_server(argv, server)


def importer_register_argv(
    *, name: str, script: Path, entrypoint: str, provider: str, server: str | None = None
) -> list[str]:
    """``kitaru importer register`` — the importer and its first version, digest-stamped."""
    argv = [
        "kitaru",
        "importer",
        "register",
        name,
        "--script",
        str(script),
        "--entrypoint",
        entrypoint,
        "--provider",
        provider,
        "--display-version",
        script_digest(script),
    ]
    return _with_server(argv, server)


def importer_version_argv(
    *, name: str, script: Path, entrypoint: str, server: str | None = None
) -> list[str]:
    """``kitaru importer version register`` — the next version of an existing importer."""
    argv = [
        "kitaru",
        "importer",
        "version",
        "register",
        name,
        "--script",
        str(script),
        "--entrypoint",
        entrypoint,
        "--display-version",
        script_digest(script),
    ]
    return _with_server(argv, server)


def evaluator_register_argv(
    *, name: str, script: Path, entrypoint: str = EVALUATOR_ENTRYPOINT, server: str | None = None
) -> list[str]:
    """``kitaru evaluator register`` — the evaluator and its first version, digest-stamped."""
    argv = [
        "kitaru",
        "evaluator",
        "register",
        name,
        "--script",
        str(script),
        "--entrypoint",
        entrypoint,
        "--display-version",
        script_digest(script),
    ]
    return _with_server(argv, server)


def evaluator_version_argv(
    *, name: str, script: Path, entrypoint: str = EVALUATOR_ENTRYPOINT, server: str | None = None
) -> list[str]:
    """``kitaru evaluator version register`` — the next version of an existing evaluator."""
    argv = [
        "kitaru",
        "evaluator",
        "version",
        "register",
        name,
        "--script",
        str(script),
        "--entrypoint",
        entrypoint,
        "--display-version",
        script_digest(script),
    ]
    return _with_server(argv, server)


def _with_server(argv: list[str], server: str | None) -> list[str]:
    """Every invocation names its server explicitly — ``--server`` beats a stale login store."""
    return [*argv, "--server", server] if server else argv


# --- what "already registered" means --------------------------------------------------------------


def script_digest(script: Path) -> str:
    """``sha-<12 hex>`` of the file's bytes — the version marker an upload is matched on.

    The server keeps an uploaded script as an opaque blob, so a re-run cannot compare source; the
    digest rides in ``--display-version``, where ``kitaru importer version list`` shows it.
    """
    return f"sha-{hashlib.sha256(script.read_bytes()).hexdigest()[:12]}"


def desired_run_spec(spec: VersionSpec) -> dict[str, Any]:
    """The run spec fields a registered version is compared against, in the server's own shape."""
    return {
        "command": f"{spec.decode_bin} run",
        "working_dir": str(spec.harness_home),
        "env": build_run_env(repo=spec.repo, sandbox_mode=spec.sandbox_mode),
        "timeout_seconds": spec.timeout_seconds,
    }


def matching_version(versions: list[dict[str, Any]], spec: VersionSpec) -> dict[str, Any] | None:
    """The registered Agent Version that already IS this spec, or ``None``.

    Compared field by field rather than by equality of the whole object: the server adds its own
    ``secret_ids`` / ``hooks`` / ``runtime_capabilities`` defaults, which a client never sends.
    """
    desired = desired_run_spec(spec)
    for version in versions:
        run_spec = version.get("run_spec")
        if not isinstance(run_spec, dict):
            continue
        if all(run_spec.get(key) == value for key, value in desired.items()):
            return version
    return None


def matching_script_version(versions: list[dict[str, Any]], script: Path) -> dict[str, Any] | None:
    """The registered importer / evaluator version whose display version is this file's digest."""
    digest = script_digest(script)
    for version in versions:
        if version.get("display_version") == digest:
            return version
    return None


def evaluator_scripts(root: Path = EVALUATORS_DIR) -> list[Path]:
    """Every evaluator script in the repo, sorted — dunder files are not evaluators."""
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*.py") if not path.name.startswith("_"))


def evaluator_name(script: Path) -> str:
    """``evaluators/decode_bad_request_400.py`` → ``decode-bad-request-400``."""
    return script.stem.replace("_", "-")


def desired_versions(*, repo: Path, decode_bin: Path, harness_home: Path) -> list[VersionSpec]:
    """The two Agent Versions every server gets: the laptop Worker, then the Modal one.

    Their order IS the version order on a fresh server (docker = v1, none = v2), so the runbooks can
    name ``decode@1`` for a laptop replay. The Modal Worker's paths come from
    ``decode.remote.image`` (both Modal apps share that image builder), so moving the venv or the
    Harness Home in the image changes the registration instead of breaking every replay it claims.
    """
    from decode.remote.image import DECODE_BIN, HARNESS_HOME

    return [
        VersionSpec(
            sandbox_mode="docker", decode_bin=decode_bin, harness_home=harness_home, repo=repo
        ),
        VersionSpec(
            sandbox_mode="none",
            decode_bin=Path(DECODE_BIN),
            harness_home=Path(HARNESS_HOME),
            repo=repo,
        ),
    ]


# --- the one subprocess helper --------------------------------------------------------------------


def run_kitaru(argv: list[str]) -> dict[str, Any]:
    """Run one ``kitaru`` argv and return its parsed envelope; raise :class:`BootstrapError`."""
    full = [*argv, "--output", "json", "--non-interactive"]
    try:
        completed = subprocess.run(full, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise BootstrapError(f"no `kitaru` on PATH ({exc}) — run `make install`.") from exc
    # stdout OR stderr: verified live on kitaru 0.26.0, a SUCCEEDING sub-command prints its envelope
    # to stdout and a FAILING one prints it to stderr — and the failing one is exactly the envelope a
    # read-then-register script needs ("not found" is its normal answer on a fresh server).
    payload = _parse(completed.stdout) or _parse(completed.stderr)
    if payload is None:
        raise BootstrapError(
            f"`{shlex.join(argv)}` (exit {completed.returncode}) answered with no json envelope: "
            f"{(completed.stderr or completed.stdout or '').strip()[:200]}"
        )
    if payload.get("ok") is False or completed.returncode != 0:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        raise BootstrapError(f"`{shlex.join(argv)}` failed: {error.get('message') or 'unknown'}")
    return payload


def _parse(stream: str | None) -> dict[str, Any] | None:
    """One json object out of a CLI stream, or ``None`` when there is none to read."""
    try:
        payload = json.loads(stream or "")
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


Runner = Any  # a callable taking the argv and answering the parsed envelope


def _item(envelope: dict[str, Any]) -> dict[str, Any]:
    item = envelope.get("item")
    return item if isinstance(item, dict) else {}


def _items(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in envelope.get("items") or [] if isinstance(item, dict)]


def _read(runner: Runner, argv: list[str]) -> dict[str, Any] | None:
    """A read that answers ``None`` instead of raising when the resource is not there yet."""
    try:
        return runner(argv)
    except BootstrapError:
        return None


# --- the orchestration ----------------------------------------------------------------------------


def bootstrap(
    *,
    runner: Runner,
    agent: str = DEFAULT_AGENT,
    repo: Path,
    decode_bin: Path,
    harness_home: Path,
    server: str | None = None,
    dry_run: bool = False,
    evaluators_root: Path = EVALUATORS_DIR,
    importer_script: Path = IMPORTER_SCRIPT,
    echo: Any = click.echo,
) -> list[Row]:
    """Bring this server up to date and return one :class:`Row` per registered resource.

    Under ``--dry-run`` every WRITE is printed instead of run; the reads still happen (they are
    read-only, and they are what makes the printed argv the real delta). A server that cannot be
    read in dry-run is treated as empty, so the command still shows a full first-time bootstrap
    instead of failing.
    """
    rows = _bootstrap_agent(
        runner=runner,
        agent=agent,
        specs=desired_versions(repo=repo, decode_bin=decode_bin, harness_home=harness_home),
        server=server,
        dry_run=dry_run,
        echo=echo,
    )
    rows += _bootstrap_script_resource(
        runner=runner,
        kind="importer",
        name=IMPORTER_NAME,
        script=importer_script,
        entrypoint=IMPORTER_ENTRYPOINT,
        server=server,
        dry_run=dry_run,
        echo=echo,
    )
    for script in evaluator_scripts(evaluators_root):
        rows += _bootstrap_script_resource(
            runner=runner,
            kind="evaluator",
            name=evaluator_name(script),
            script=script,
            entrypoint=EVALUATOR_ENTRYPOINT,
            server=server,
            dry_run=dry_run,
            echo=echo,
        )
    return rows


def _bootstrap_agent(
    *,
    runner: Runner,
    agent: str,
    specs: list[VersionSpec],
    server: str | None,
    dry_run: bool,
    echo: Any,
) -> list[Row]:
    """Create the agent when absent, then add exactly the versions this server does not have."""
    existing = _read(runner, _with_server(["kitaru", "agent", "get", agent], server))
    rows: list[Row] = []
    pending = list(specs)

    if existing is None:
        first = pending.pop(0)
        argv = agent_register_argv(agent=agent, spec=first, server=server)
        item = _emit(runner, argv, dry_run=dry_run, echo=echo)
        rows.append(
            Row(
                kind="agent",
                ref=agent,
                id=str(_item(item).get("agent", {}).get("id", "-")),
                action="created",
            )
        )
        rows.append(_version_row(agent, first, _item(item).get("version", {}), "registered"))
        versions: list[dict[str, Any]] = []
    else:
        rows.append(
            Row(kind="agent", ref=agent, id=str(_item(existing).get("id", "-")), action="exists")
        )
        listed = _read(runner, _with_server(["kitaru", "agent", "version", "list", agent], server))
        versions = _items(listed or {})

    for spec in pending:
        match = matching_version(versions, spec)
        if match is not None:
            rows.append(_version_row(agent, spec, match, "exists"))
            continue
        argv = register_argv(
            agent=agent,
            decode_bin=spec.decode_bin,
            harness_home=spec.harness_home,
            repo=spec.repo,
            timeout_seconds=spec.timeout_seconds,
            sandbox_mode=spec.sandbox_mode,
            server=server,
        )
        item = _emit(runner, argv, dry_run=dry_run, echo=echo)
        rows.append(_version_row(agent, spec, _item(item).get("version", {}), "registered"))
    return rows


def _version_row(agent: str, spec: VersionSpec, version: Any, action: str) -> Row:
    """One table line for an Agent Version, named by the Sandbox Mode it replays under."""
    payload = version if isinstance(version, dict) else {}
    number = payload.get("version")
    return Row(
        kind=f"agent version ({spec.sandbox_mode})",
        ref=f"{agent}@{number}" if number else f"{agent}@?",
        id=str(payload.get("id", "-")),
        action=action,
    )


def _bootstrap_script_resource(
    *,
    runner: Runner,
    kind: str,
    name: str,
    script: Path,
    entrypoint: str,
    server: str | None,
    dry_run: bool,
    echo: Any,
) -> list[Row]:
    """Register an importer / evaluator, or report the version that already carries this digest."""
    if not script.is_file():
        raise BootstrapError(f"no {kind} script at {script} (run from the repo root).")

    existing = _read(runner, _with_server(["kitaru", kind, "get", name], server))
    if existing is None:
        argv = (
            importer_register_argv(
                name=name,
                script=script,
                entrypoint=entrypoint,
                provider=IMPORTER_PROVIDER,
                server=server,
            )
            if kind == "importer"
            else evaluator_register_argv(
                name=name, script=script, entrypoint=entrypoint, server=server
            )
        )
        item = _item(_emit(runner, argv, dry_run=dry_run, echo=echo))
        return [_script_row(kind, name, item.get("version"), "registered")]

    listed = _read(runner, _with_server(["kitaru", kind, "version", "list", name], server))
    match = matching_script_version(_items(listed or {}), script)
    if match is not None:
        return [_script_row(kind, name, match, "exists")]

    argv = (
        importer_version_argv(name=name, script=script, entrypoint=entrypoint, server=server)
        if kind == "importer"
        else evaluator_version_argv(name=name, script=script, entrypoint=entrypoint, server=server)
    )
    item = _item(_emit(runner, argv, dry_run=dry_run, echo=echo))
    return [_script_row(kind, name, item.get("version"), "registered")]


def _script_row(kind: str, name: str, version: Any, action: str) -> Row:
    payload = version if isinstance(version, dict) else {}
    number = payload.get("version")
    return Row(
        kind=kind,
        ref=f"{name}@{number}" if number else f"{name}@?",
        id=str(payload.get("id", "-")),
        action=action,
    )


def _emit(runner: Runner, argv: list[str], *, dry_run: bool, echo: Any) -> dict[str, Any]:
    """Run a WRITE, or print it verbatim under ``--dry-run``."""
    if dry_run:
        echo(shlex.join(argv))
        return {}
    return runner(argv)


def render_table(rows: list[Row]) -> str:
    """``kind · name@version · id`` as fixed-width columns — one line per registered resource."""
    header = Row(kind="KIND", ref="NAME@VERSION", id="ID", action="ACTION")
    widths = [
        max(len(getattr(row, field)) for row in [header, *rows])
        for field in ("kind", "ref", "id", "action")
    ]
    lines = []
    for row in [header, *rows]:
        cells = (row.kind, row.ref, row.id, row.action)
        lines.append(
            " · ".join(
                cell.ljust(width) for cell, width in zip(cells, widths, strict=True)
            ).rstrip()
        )
    return "\n".join(lines)


@click.command()
@click.option(
    "--server",
    default=None,
    help=f"The Kitaru Server to register on [default: ${KITARU_API_URL_ENV}, else your login store].",
)
@click.option("--dry-run", is_flag=True, help="Print the kitaru commands instead of running them.")
@click.option(
    "--agent",
    default=DEFAULT_AGENT,
    show_default=True,
    help="The agent decode's sessions are recorded under.",
)
@click.option(
    "--repo",
    default=str(Path(__file__).resolve().parents[1]),
    show_default="this repo",
    type=click.Path(path_type=Path),
    help="Repo cloned into a replay's Workspace (the docker Agent Version).",
)
@click.option(
    "--harness-home",
    default=str(DEFAULT_HARNESS_HOME),
    show_default=True,
    type=click.Path(path_type=Path),
    help="The laptop Worker's working dir; must be outside the repo.",
)
@click.option(
    "--decode-bin",
    default=None,
    type=click.Path(path_type=Path),
    help="The decode entrypoint the laptop Worker spawns.  [default: <repo>/.venv/bin/decode]",
)
def main(
    server: str | None,
    dry_run: bool,
    agent: str,
    repo: Path,
    harness_home: Path,
    decode_bin: Path | None,
) -> None:
    """Register decode's agent versions, importer and evaluators on ONE Kitaru Server."""
    repo = repo.expanduser().resolve()
    harness_home = harness_home.expanduser().resolve()
    entrypoint = (decode_bin or repo / ".venv/bin/decode").expanduser().resolve()
    target = server or os.environ.get(KITARU_API_URL_ENV, "").strip() or None

    if not entrypoint.is_file():
        raise click.ClickException(
            f"no decode entrypoint at {entrypoint} — run `make install` in {repo} or pass "
            "--decode-bin."
        )
    if not dry_run:
        # The laptop Worker chdirs here for every spawn, so it must exist before the first task is
        # claimed. The Modal Worker's /harness is created by the image build, never here.
        harness_home.mkdir(parents=True, exist_ok=True)

    click.echo(f"kitaru bootstrap → {target or 'the server in your kitaru login store'}")
    try:
        rows = bootstrap(
            runner=run_kitaru,
            agent=agent,
            repo=repo,
            decode_bin=entrypoint,
            harness_home=harness_home,
            server=target,
            dry_run=dry_run,
        )
    except (BootstrapError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None

    click.echo(render_table(rows))
    if dry_run:
        click.echo("--dry-run: nothing was registered.")
        return
    agent_id = next((row.id for row in rows if row.kind == "agent"), None)
    if agent_id:
        click.echo(f"KITARU_AGENT_ID={agent_id}")


if __name__ == "__main__":
    sys.exit(main())
