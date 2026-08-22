"""The Modal Headless App — one remote ``decode run``, fired from a laptop (ADR-0020 §1-4).

    uv run modal run scripts/modal_headless.py --task "…" [--repo …] [--sandbox-mode none|modal]

An operator script, not library code: it lives outside the ``decode`` import graph, prints with
``click.echo``, and its Function runs the SAME console script a laptop runs — ``decode run`` as a
subprocess — so remote behavior cannot drift from local behavior (ADR-0020 §1).

* **The image is built in-app** (ADR-0020 §2): ``debian_slim`` + ``Image.uv_sync()`` for the locked
  dependencies, then this repo's source baked on top and installed with ``--no-deps``. No checked-in
  image recipe, no registry. Deps and source are separate layers, so editing decode rebuilds only
  the last two.
  The console script therefore exists at ONE deterministic absolute path, :data:`DECODE_BIN`.
* **Sandbox modes: ``none`` and ``modal`` only** (ADR-0020 §3). ``none`` — the gVisor container is
  the isolation; a ``repo`` is cloned by the HARNESS into :data:`REPO_CLONE_DIR` and decode launches
  with that cwd, so decode never sees ``--repo`` and its ADR-0012 §3 guard stays intact (and nothing
  ships back: the clone dies with the container). ``modal`` — ``--repo`` passes through to decode,
  which clones natively into a nested Modal Sandbox and hands ``decode/<session-id>`` back.
  ``docker`` is rejected with ONE friendly line, client-side, before a container ever starts.
* **Secrets** ride the ``decode-headless`` :class:`modal.Secret` — provider keys, ``KITARU_API_URL`` /
  ``KITARU_API_KEY`` / ``KITARU_AGENT_ID`` (the Recording Seam degrades gracefully without them), and
  an optional ``SANDBOX_GIT_TOKEN``. Secret env outranks ``.env`` in Settings precedence, so
  ``DECODE_ENV`` stays ``local`` and no Environment Bucket is used on Modal (ADR-0020 §4). Create it
  once, values never committed::

      modal secret create decode-headless GEMINI_API_KEY=… KITARU_API_URL=… KITARU_API_KEY=… \\
          KITARU_AGENT_ID=… [SANDBOX_GIT_TOKEN=…]

* **The token is never an argument.** ``SANDBOX_GIT_TOKEN`` is mirrored to ``$GITHUB_TOKEN`` and read
  at push time by the same credential helper decode itself uses (ADR-0016 §2) — no argv, no log line.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path

import click
import modal

# --- the app, and the fixed layout of its image ----------------------------------------------------

APP_NAME = "decode-headless"

# The Modal Secret this Function runs with (ADR-0020 §4); operator-created, never committed.
SECRET_NAME = "decode-headless"

REPO_ROOT = Path(__file__).resolve().parents[1]

# Where the repo source is baked; the venv ``Image.uv_sync()`` builds lives at ``/.uv/.venv``, so the
# console script installed into it is at ONE absolute path no PATH set-up can move.
IMAGE_SOURCE_DIR = "/opt/decode"
VENV_DIR = "/.uv/.venv"
DECODE_BIN = f"{VENV_DIR}/bin/decode"

# The Harness Home: every harness artifact (``.decode/sessions``, logs, ``.decode/sandbox``) anchors
# here, OUTSIDE any repo checkout (ADR-0012 §6).
HARNESS_HOME = "/harness"

# The child's log file — read back after the run for the session id and the shipped branch.
LOG_FILE = f"{HARNESS_HOME}/decode-run.log"

# Where the HARNESS clones ``--repo`` in ``none`` mode (decode never sees the repo there).
REPO_CLONE_DIR = "/scratch/repo"

# Build artefacts and local state that must never be baked into the image.
_SOURCE_IGNORE = [
    "**/.git",
    "**/.venv",
    "**/.decode",
    "**/__pycache__",
    "**/*.pyc",
    "**/.pytest_cache",
    "**/.ruff_cache",
    "**/node_modules",
]

IMAGE = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "curl", "ca-certificates")
    # Locked third-party deps only (uv_sync never installs the project itself) — the cached layer.
    .uv_sync(uv_project_dir=str(REPO_ROOT))
    # decode's own source, on top, installed without deps so the layer above is reused verbatim.
    .add_local_dir(REPO_ROOT, IMAGE_SOURCE_DIR, copy=True, ignore=_SOURCE_IGNORE)
    .run_commands(
        f"/.uv/uv pip install --no-deps --python {VENV_DIR}/bin/python {IMAGE_SOURCE_DIR}"
    )
    .run_commands(f"mkdir -p {HARNESS_HOME} {REPO_CLONE_DIR}")
    # ADR-0020 §4: one config surface, fed by the Secret's process env — never an Environment Bucket.
    .env({"DECODE_ENV": "local"})
)

app = modal.App(APP_NAME)

# --- the contract of one run -----------------------------------------------------------------------

# The two modes a Modal container can honour (ADR-0020 §3).
SUPPORTED_SANDBOX_MODES = ("none", "modal")
DEFAULT_SANDBOX_MODE = "none"

DOCKER_MODE_MESSAGE = (
    "Decode: sandbox mode 'docker' cannot run on Modal — a Modal container has no Docker daemon. "
    "Use --sandbox-mode none (the gVisor container is itself the isolation) or --sandbox-mode modal "
    "(a nested Modal Sandbox, which also hands a decode/<session-id> branch back)."
)

# One whole agent run: a clone, a sandbox, many model calls. Generous for the same reason the Agent
# Version's timeout is (``scripts/register_kitaru_agent.py``): a killed process is indistinguishable
# from a failed agent. The Function's own ceiling sits above it so the child is killed first — and
# says so — instead of the container vanishing mid-answer.
DEFAULT_TIMEOUT_SECONDS = 1800
FUNCTION_TIMEOUT_SECONDS = 3600

# Exit code for a run that never started because its sandbox mode is unrunnable here.
SANDBOX_MODE_REJECTED_EXIT = 2

# The answer rides back through Modal's return channel; a runaway transcript is trimmed to its tail
# (the answer is the last thing decode prints).
ANSWER_TAIL_CHARS = 8000

RUN_SUMMARY_FORMAT = (
    "Decode: run finished — exit={exit_code} sandbox={sandbox_mode} session={session_id} "
    "branch={session_branch}"
)

# ``none`` + ``--repo``: the harness clone lives and dies with the container (ADR-0020 §3).
NONE_MODE_NO_HANDBACK_NOTE = (
    "sandbox mode none has no Hand-back: the harness clone is discarded with the container. Use "
    "--sandbox-mode modal to ship a decode/<session-id> branch."
)

# A Hand-back that secured a branch but could not push it left the work ONLY in a container that is
# about to disappear — the one outcome an operator must not read as "shipped" (ADR-0016 §4).
UNPUSHED_BRANCH_NOTE = (
    "the Session Branch was NOT pushed: it existed only inside the container, which is now gone. "
    "Add a SANDBOX_GIT_TOKEN with push access to the decode-headless secret and re-run."
)

# The env var git and ``gh`` read, and the credential helper that feeds it to git's HTTPS transport
# at PUSH time — copied verbatim from ``decode.sandbox.workspace`` (scripts live outside decode's
# import graph); a unit test asserts the two never drift. The token is expanded by the helper's own
# shell, so it is never an argv element and never a formatted log line (ADR-0016 §2).
GIT_TOKEN_ENV = "GITHUB_TOKEN"
GIT_CREDENTIAL_HELPER_VALUE = (
    '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'
)

# The decode setting an operator hands the container its git credential under (ADR-0016 §2).
GIT_TOKEN_SETTING = "SANDBOX_GIT_TOKEN"

# The child's session id is a DEBUG line; the Hand-back branch is an INFO one. The branch is
# ``decode/<first 8 chars of the session id>`` — alphanumerics and dashes, so trailing prose
# punctuation ("… on branch decode/9c2d0f1a.") is never swallowed into the name.
_SESSION_ID_PATTERN = re.compile(r"session_id=([0-9a-fA-F-]{36})")
_SESSION_BRANCH_PATTERN = re.compile(r"\[handback\][^\n]*?\b(decode/[0-9a-zA-Z_-]+)")
_PUSH_FAILED_PATTERN = re.compile(r"\[handback\] could not push\b")


# --- pure helpers: everything one run is decided by ------------------------------------------------


def sandbox_mode_error(sandbox_mode: str) -> str | None:
    """ONE friendly line if ``sandbox_mode`` cannot run on Modal, else ``None`` (ADR-0020 §3).

    ``docker`` gets its own line — it is the mode an operator reaches for by habit, and the reason it
    is impossible (no daemon in a gVisor container) is worth naming. Checked on the LAPTOP before
    ``.remote()``, so a typo costs no container, and again in-Function for direct callers.
    """
    if sandbox_mode == "docker":
        return DOCKER_MODE_MESSAGE
    if sandbox_mode not in SUPPORTED_SANDBOX_MODES:
        modes = " or ".join(SUPPORTED_SANDBOX_MODES)
        return f"Decode: unknown sandbox mode {sandbox_mode!r}; on Modal use {modes}."
    return None


def decode_argv(
    *,
    task: str,
    sandbox_mode: str,
    repo: str | None = None,
    model: str | None = None,
    decode_bin: str = DECODE_BIN,
) -> list[str]:
    """The exact ``decode run`` argv the container executes — the laptop's surface, verbatim.

    ``--repo`` is passed through in ``modal`` mode ONLY: under ``none`` decode refuses a repo
    (ADR-0012 §3) and the harness has already cloned it into :data:`REPO_CLONE_DIR`, which is the
    child's cwd.
    """
    argv = [decode_bin, "run", task]
    if sandbox_mode == "modal" and repo:
        argv += ["--repo", repo]
    if model:
        argv += ["--model", model]
    return argv


def decode_cwd(
    *,
    sandbox_mode: str,
    repo: str | None = None,
    harness_home: str = HARNESS_HOME,
    clone_dir: str = REPO_CLONE_DIR,
) -> str:
    """The child's working directory: the harness clone under ``none`` + ``repo``, else Harness Home.

    Under ``none`` the cwd IS the tool scope, so a repo run has to launch inside the clone; every
    other case keeps the cwd outside any checkout, where the harness artifacts belong (ADR-0012 §6).
    """
    if sandbox_mode == "none" and repo:
        return clone_dir
    return harness_home


def clone_argv(repo: str, dest: str) -> list[str]:
    """The harness's own ``git clone`` for ``none`` mode — plain, ambient credentials only."""
    return ["git", "clone", repo, dest]


def git_credential_argv(env: Mapping[str, str]) -> list[str] | None:
    """The ``git config`` call that teaches the container's git to read ``$GITHUB_TOKEN``, or ``None``.

    ``None`` when no ``SANDBOX_GIT_TOKEN`` was handed to the container (ADR-0016: absent token → no
    credential at all, and Hand-back skips with its own friendly line). The argv carries the helper
    SCRIPT, never the token: the value is expanded by git at push time.
    """
    if not env.get(GIT_TOKEN_SETTING):
        return None
    return ["git", "config", "--global", "credential.helper", GIT_CREDENTIAL_HELPER_VALUE]


def decode_run_env(
    base_env: Mapping[str, str], *, sandbox_mode: str, log_file: str = LOG_FILE
) -> dict[str, str]:
    """The child's process env: the container's secrets plus this run's four decisions.

    ``SANDBOX_MODE`` selects the tool-execution seam, ``DECODE_ENV=local`` pins the config surface to
    the Secret's process env (ADR-0020 §4), ``DECODE_LOG_FILE`` puts the child's log where this
    Function reads the session id back out of it, and ``LOG_LEVEL`` defaults to DEBUG because that is
    the level the session-id line is logged at (an operator-set level still wins).

    ``SANDBOX_REPO`` is dropped in BOTH modes: a repo belongs to this invocation's ``--repo`` flag,
    and one left in the Secret would silently trip decode's ``--repo``-under-``none`` guard.
    """
    env = {key: value for key, value in base_env.items() if key != "SANDBOX_REPO"}
    env["SANDBOX_MODE"] = sandbox_mode
    env["DECODE_ENV"] = "local"
    env["DECODE_LOG_FILE"] = log_file
    env["LOG_LEVEL"] = base_env.get("LOG_LEVEL") or "DEBUG"
    env["GIT_TERMINAL_PROMPT"] = "0"  # a missing credential fails fast instead of hanging
    token = base_env.get(GIT_TOKEN_SETTING)
    if token:
        env[GIT_TOKEN_ENV] = token
    return env


def session_id_from_log(log_text: str) -> str | None:
    """The decode session id the child logged, or ``None`` — it names the Kitaru Session and branch."""
    match = _SESSION_ID_PATTERN.search(log_text)
    return match.group(1) if match else None


def session_branch_from_log(log_text: str) -> str | None:
    """The ``decode/<session-id>`` Session Branch the Hand-back secured, or ``None`` if it skipped."""
    matches = _SESSION_BRANCH_PATTERN.findall(log_text)
    return matches[-1] if matches else None


def build_result(
    *, sandbox_mode: str, repo: str | None, exit_code: int, stdout: str, log_text: str
) -> dict[str, object]:
    """The small payload one run returns: the answer, the ids, the exit code (ADR-0020 §1).

    ``stdout`` is the child's stdout, which is exactly the agent's answer (``decode run`` keeps
    diagnostics on stderr); a runaway transcript comes back as its TAIL, flagged, because the answer
    is at the end. The session id and branch are read out of the child's own log — the only place
    they exist.

    ``note`` carries the one thing the ids alone would misreport: work that did NOT come home. Under
    ``none`` + a repo there is no Hand-back at all, and a secured-but-unpushed branch is a branch
    that died with the container — both read as "shipped" if the payload only names the branch.
    """
    answer = stdout.strip()
    truncated = len(answer) > ANSWER_TAIL_CHARS
    branch = session_branch_from_log(log_text)
    note = ""
    if sandbox_mode == "none" and repo:
        note = NONE_MODE_NO_HANDBACK_NOTE
    elif branch is not None and _PUSH_FAILED_PATTERN.search(log_text):
        note = UNPUSHED_BRANCH_NOTE
    return {
        "exit_code": exit_code,
        "sandbox_mode": sandbox_mode,
        "answer": answer[-ANSWER_TAIL_CHARS:] if truncated else answer,
        "answer_truncated": truncated,
        "session_id": session_id_from_log(log_text),
        "session_branch": branch,
        "note": note,
    }


def stream_subprocess(
    argv: list[str], *, cwd: str, env: Mapping[str, str], timeout_seconds: int
) -> tuple[str, int]:
    """Run ``argv`` to completion, relaying its stdout live, and return ``(stdout, exit_code)``.

    The child's stderr is INHERITED, so decode's diagnostics stream straight into the Function log
    while stdout — the answer — is captured line by line and echoed as it arrives (a headless run is
    long; an operator watching ``modal run`` should see it move). A run past ``timeout_seconds`` is
    killed by a timer thread, which turns a hang into a normal non-zero exit with partial output
    instead of a container that dies at the Function ceiling with nothing to show.
    """
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1,
    )
    killer = threading.Timer(timeout_seconds, process.kill)
    killer.start()
    chunks: list[str] = []
    try:
        for line in process.stdout:  # ty: ignore[possibly-unbound-attribute]
            chunks.append(line)
            click.echo(line, nl=False)
        exit_code = process.wait()
    finally:
        killer.cancel()
    return "".join(chunks), exit_code


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
) -> dict[str, object]:
    """Run ONE headless decode task in this container and return its small result payload.

    The whole Function is: reject an unrunnable mode, give git the credential helper if the operator
    handed one over, clone the repo when the harness owns the clone (``none`` mode), then run the
    baked ``decode run`` console script as a subprocess and read its log back. Nothing about the
    agent is re-implemented here — that is the point of ADR-0020 §1.
    """
    mode_error = sandbox_mode_error(sandbox_mode)
    if mode_error is not None:
        # Defensive twin of the local_entrypoint guard, for a direct ``.remote()`` / ``.spawn()``
        # caller (task 143): ONE line, a non-zero code in the payload, no traceback.
        click.echo(mode_error, err=True)
        return build_result(
            sandbox_mode=sandbox_mode,
            repo=repo,
            exit_code=SANDBOX_MODE_REJECTED_EXIT,
            stdout=mode_error,
            log_text="",
        )

    env = decode_run_env(os.environ, sandbox_mode=sandbox_mode)
    Path(HARNESS_HOME).mkdir(parents=True, exist_ok=True)

    credential_argv = git_credential_argv(os.environ)
    if credential_argv is not None:
        subprocess.run(credential_argv, check=True)

    if sandbox_mode == "none" and repo:
        clone_for_none_mode(repo, env)

    stdout, exit_code = stream_subprocess(
        decode_argv(task=task, sandbox_mode=sandbox_mode, repo=repo, model=model),
        cwd=decode_cwd(sandbox_mode=sandbox_mode, repo=repo),
        env=env,
        timeout_seconds=timeout_seconds,
    )
    result = build_result(
        sandbox_mode=sandbox_mode,
        repo=repo,
        exit_code=exit_code,
        stdout=stdout,
        log_text=_read_child_log(),
    )
    click.echo(RUN_SUMMARY_FORMAT.format(**result), err=True)
    if result["note"]:
        click.echo(f"Decode: {result['note']}", err=True)
    return result


def clone_for_none_mode(repo: str, env: Mapping[str, str], *, dest: str = REPO_CLONE_DIR) -> None:
    """Clone ``repo`` into ``dest`` — the harness's clone, so decode never sees ``--repo``.

    A leftover clone is REPLACED: Modal re-uses warm containers, so ``dest`` may still hold the
    previous input's repo, and cloning "into" it would either fail or, worse, run the new task
    against the old tree. Fatal on failure, exactly like the headless runner's own clone
    (ADR-0012 §3): nobody is watching a remote run, so degrading to an empty directory would burn
    the whole paid run on nothing.
    """
    destination = Path(dest)
    if destination.exists() and any(destination.iterdir()):
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        clone_argv(repo, dest),
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"could not clone {repo!r} into {dest}, so this run has nothing to work on: "
            f"{completed.stderr.strip()}"
        )


def _read_child_log() -> str:
    """The child's log file, or ``""`` — best-effort: a missing log costs the ids, never the run."""
    try:
        return Path(LOG_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@app.local_entrypoint()
def main(
    task: str,
    repo: str | None = None,
    sandbox_mode: str = DEFAULT_SANDBOX_MODE,
    model: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Fire ONE synchronous remote run and print its answer; exit with the run's own code.

    The mode guard runs HERE first: ``--sandbox-mode docker`` costs one line on stderr and a non-zero
    exit, with no container started and nothing billed (ADR-0020 §3).
    """
    mode_error = sandbox_mode_error(sandbox_mode)
    if mode_error is not None:
        click.echo(mode_error, err=True)
        sys.exit(1)

    result = run_task.remote(
        task=task,
        repo=repo,
        sandbox_mode=sandbox_mode,
        model=model,
        timeout_seconds=timeout_seconds,
    )
    click.echo(result["answer"])
    click.echo(RUN_SUMMARY_FORMAT.format(**result), err=True)
    if result["exit_code"]:
        sys.exit(int(result["exit_code"]))
