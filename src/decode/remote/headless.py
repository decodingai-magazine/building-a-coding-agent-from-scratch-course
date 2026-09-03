"""The Modal Headless App's pure helpers — everything one remote run is decided by (ADR-0020 §1-4).

Modal-free on purpose: no ``modal`` import, no container, no network. This module turns one
operator invocation into the exact ``decode run`` subprocess the container executes, and turns that
subprocess's output back into the small payload the laptop reads. Two callers:

* :mod:`decode.remote.app` — the Modal Functions (``run_task`` / ``nightly`` / ``webhook``) run
  :func:`execute_run` inside the container and validate their inputs with the same guards;
* :mod:`decode.remote.cli` — the ``decode remote`` subcommands validate on the LAPTOP, before any
  ``.remote()`` / ``.spawn()``, so an unrunnable request costs one line and no container.

Both read the same constants, so the laptop's rejection and the container's are one message.

* **Sandbox modes: ``none`` and ``modal`` only** (ADR-0020 §3). ``none`` — the gVisor container is
  the isolation; a ``repo`` is cloned by the HARNESS into :data:`REPO_CLONE_DIR` and decode launches
  with that cwd, so decode never sees ``--repo`` and its ADR-0012 §3 guard stays intact (and nothing
  ships back: the clone dies with the container). ``modal`` — ``--repo`` passes through to decode,
  which clones natively into a nested Modal Sandbox and hands ``decode/<session-id>`` back.
  ``docker`` is rejected with ONE friendly line, client-side, before a container ever starts.
* **The token is never an argument.** ``SANDBOX_GIT_TOKEN`` is mirrored to ``$GITHUB_TOKEN`` and read
  at push time by the same credential helper decode itself uses (ADR-0016 §2) — no argv, no log line.
* **``--max-requests``** rides through every surface to ``decode run --max-requests``: a run nobody
  watches gets a request ceiling, not just Modal's wall-clock one.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

import click
from pydantic import BaseModel, Field

from decode.remote.image import DECODE_BIN, HARNESS_HOME

# --- the app, and the fixed layout of its image ----------------------------------------------------

APP_NAME = "decode-headless"

# The Modal Secret the run Function runs with (ADR-0020 §4); operator-created, never committed.
SECRET_NAME = "decode-headless"

# The child's log file — read back after the run for the session id and the shipped branch.
LOG_FILE = f"{HARNESS_HOME}/decode-run.log"

# Where the HARNESS clones ``--repo`` in ``none`` mode (decode never sees the repo there).
REPO_CLONE_DIR = "/scratch/repo"

# What the ``webhook`` endpoint needs on top of decode's own deps: Modal serves a
# ``fastapi_endpoint`` through FastAPI, which decode does not depend on.
WEB_PACKAGES = ("fastapi[standard]>=0.115",)

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

# What the timer kill says for itself. Without it the operator reads an unexplained ``exit=-9`` and
# blames the agent for what was actually ``--timeout-seconds`` (task 147).
TIMEOUT_KILL_FORMAT = (
    "Decode: the run passed --timeout-seconds {timeout_seconds} and its decode process was killed; "
    "the output above is partial. Re-run with a larger --timeout-seconds if the task needs longer."
)

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
# at PUSH time — copied verbatim from ``decode.sandbox.workspace`` (this module must stay free of
# the sandbox package's imports); a unit test asserts the two never drift. The token is expanded by
# the helper's own shell, so it is never an argv element and never a formatted log line
# (ADR-0016 §2).
GIT_TOKEN_ENV = "GITHUB_TOKEN"
GIT_CREDENTIAL_HELPER_VALUE = (
    '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'
)

# The decode setting an operator hands the container its git credential under (ADR-0016 §2).
GIT_TOKEN_SETTING = "SANDBOX_GIT_TOKEN"

# --- triggers without a laptop: the nightly cron and the webhook (ADR-0020 Amendment §8) ------------

# The nightly job is DEPLOY-TIME configuration, read from the laptop's env by ``decode remote
# deploy``: the schedule lands on the Function, the job's parameters travel as a
# ``modal.Secret.from_dict`` env (so the container reads the SAME names back). Absent on the laptop
# → no schedule is registered and the deployment is exactly what it was before this trigger existed.
NIGHTLY_CRON_ENV = "DECODE_NIGHTLY_CRON"  # crontab syntax, UTC — e.g. "0 2 * * *"
NIGHTLY_TASK_ENV = "DECODE_NIGHTLY_TASK"
NIGHTLY_REPO_ENV = "DECODE_NIGHTLY_REPO"
NIGHTLY_SANDBOX_MODE_ENV = "DECODE_NIGHTLY_SANDBOX_MODE"
NIGHTLY_MODEL_ENV = "DECODE_NIGHTLY_MODEL"
NIGHTLY_MAX_REQUESTS_ENV = "DECODE_NIGHTLY_MAX_REQUESTS"
NIGHTLY_TIMEOUT_ENV = "DECODE_NIGHTLY_TIMEOUT_SECONDS"
NIGHTLY_JOB_ENVS = (
    NIGHTLY_TASK_ENV,
    NIGHTLY_REPO_ENV,
    NIGHTLY_SANDBOX_MODE_ENV,
    NIGHTLY_MODEL_ENV,
    NIGHTLY_MAX_REQUESTS_ENV,
    NIGHTLY_TIMEOUT_ENV,
)

NIGHTLY_NO_TASK_FORMAT = (
    "Decode: {cron_env} is set but {task_env} is not — a schedule needs a task to run. Export "
    "{task_env} (and optionally {repo_env} / {mode_env}) alongside it, or unset {cron_env}."
)
NIGHTLY_BAD_COUNT_FORMAT = "Decode: {env} must be a positive integer, got {value!r}."
NIGHTLY_UNCONFIGURED_MESSAGE = (
    "Decode: the nightly job has no task — it was deployed without DECODE_NIGHTLY_TASK; nothing to "
    "run."
)
NIGHTLY_REGISTERED_FORMAT = "Decode: nightly job registered — cron={cron!r} (UTC) task={task!r}."

# The webhook's request body: the same knobs as ``decode remote run``, as JSON. ``task`` is the only
# required key.
WEBHOOK_EMPTY_TASK_MESSAGE = 'Decode: the webhook body needs a non-empty "task".'

# --- N attempts at one task (ADR-0020 §1) -----------------------------------------------------------

# Appended to every attempt's task, verbatim from the retired ``demo-multiple-attempts.sh``. A model
# that ships its own work names its own branch — and sometimes forgets — so the attempts stop being
# comparable. Banning the push leaves the Hand-back (ADR-0012 §8) as the ONLY ship path, and every
# attempt lands identically as ``decode/<session-id>``.
PUSH_BAN_PARAGRAPH = (
    "Commit your work when you are done. Do NOT push and do NOT open a pull request."
)

# What one row of the comparison table can say about an attempt.
SHIPPED_STATUS = "shipped"
NOT_SHIPPED_STATUS = "NOT SHIPPED"
FAILED_STATUS = "FAILED"

# The attempt never returned a payload at all (the container died, the input was cancelled): a
# sentinel exit code that cannot collide with a child's own — subprocess exit codes are 0-255 or the
# negated signal number.
ATTEMPT_CRASHED_EXIT = 1000

_TABLE_ROW = "{index:<5}{session:<38}{branch:<18}{status:<13}{exit_code}"
_TABLE_HEADER = _TABLE_ROW.format(
    index="#", session="session", branch="branch", status="shipped?", exit_code="exit"
)
_TABLE_RULE = "-" * len(_TABLE_HEADER.rstrip())
_TABLE_EMPTY = "—"

ATTEMPTS_MIN = 1
TOO_FEW_ATTEMPTS_FORMAT = "Decode: --attempts must be at least {minimum}, got {attempts}."
NO_REPO_FORMAT = (
    "Decode: --attempts {attempts} needs --repo <url> — attempts are compared as the "
    "decode/<session-id> branches they ship, and a run without a repo ships nothing."
)
NONE_MODE_ATTEMPTS_WARNING = (
    "Decode: --sandbox-mode none has no Hand-back, so these attempts will produce answers only and "
    "no branch to compare; use --sandbox-mode modal to ship one branch per attempt."
)

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
    max_requests: int | None = None,
    decode_bin: str = DECODE_BIN,
) -> list[str]:
    """The exact ``decode run`` argv the container executes — the laptop's surface, verbatim.

    ``--repo`` is passed through in ``modal`` mode ONLY: under ``none`` decode refuses a repo
    (ADR-0012 §3) and the harness has already cloned it into :data:`REPO_CLONE_DIR`, which is the
    child's cwd. ``max_requests`` becomes decode's own ``--max-requests`` — the request ceiling a run
    nobody watches should have.
    """
    argv = [decode_bin, "run", task]
    if sandbox_mode == "modal" and repo:
        argv += ["--repo", repo]
    if model:
        argv += ["--model", model]
    if max_requests is not None:
        argv += ["--max-requests", str(max_requests)]
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
    """The decode session id the child logged, or ``None`` — it names the Kitaru Session and branch.

    The LAST one logged, like :func:`session_branch_from_log`: if a log ever holds two runs, the one
    that just finished is the later of them (see :func:`reset_child_log` for why it should not).
    """
    matches = _SESSION_ID_PATTERN.findall(log_text)
    return matches[-1] if matches else None


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


# --- pure helpers: everything a FAN-OUT of attempts is decided by ----------------------------------


def attempts_input_error(
    *, attempts: int, repo: str | None, sandbox_mode: str = DEFAULT_SANDBOX_MODE
) -> str | None:
    """ONE friendly line if this fan-out cannot be run, else ``None``.

    Checked on the LAPTOP before anything is spawned: N attempts cost N agents' worth of tokens and
    container minutes, so an unrunnable request has to die before the money, not after it. ``--repo``
    is mandatory past one attempt because attempts are compared as the branches they ship — without a
    repo there is nothing to compare (a single attempt is a plain fire-and-forget run and needs none).
    """
    mode_error = sandbox_mode_error(sandbox_mode)
    if mode_error is not None:
        return mode_error
    if attempts < ATTEMPTS_MIN:
        return TOO_FEW_ATTEMPTS_FORMAT.format(minimum=ATTEMPTS_MIN, attempts=attempts)
    if attempts > 1 and not repo:
        return NO_REPO_FORMAT.format(attempts=attempts)
    return None


def attempts_input_warning(*, repo: str | None, sandbox_mode: str) -> str | None:
    """ONE line when the fan-out is legal but will ship nothing (``none`` + a repo, ADR-0020 §3).

    Not an error — an answer-only fan-out is a real thing to want — but an operator who expected N
    branches should learn it now rather than from an all-``NOT SHIPPED`` table N paid runs later.
    """
    if repo and sandbox_mode == "none":
        return NONE_MODE_ATTEMPTS_WARNING
    return None


def attempt_task(task: str) -> str:
    """The operator's task plus the push ban — the text EVERY attempt is given (see the constant)."""
    return f"{task.rstrip()}\n\n{PUSH_BAN_PARAGRAPH}"


def failed_attempt_result(error: BaseException, *, sandbox_mode: str) -> dict[str, object]:
    """The stand-in payload for an attempt that never returned one, shaped like :func:`build_result`.

    One attempt's exception must not cost the operator the N-1 that finished: it becomes a ``FAILED``
    row carrying its reason, and the table still prints.
    """
    return {
        "exit_code": ATTEMPT_CRASHED_EXIT,
        "sandbox_mode": sandbox_mode,
        "answer": "",
        "answer_truncated": False,
        "session_id": None,
        "session_branch": None,
        "note": f"the attempt never returned a result: {error}",
        "error": True,
    }


def attempt_status(result: Mapping[str, object]) -> str:
    """``shipped`` only when a Session Branch actually reached origin.

    A branch with a ``note`` is one of the two lies the ids alone would tell (``none`` mode's
    discarded clone, or a secured-but-unpushed branch that died with its container, ADR-0016 §4) — an
    operator reads this column to decide what to `git diff`, so it must never over-promise.
    """
    if result.get("error"):
        return FAILED_STATUS
    if result.get("session_branch") and not result.get("note"):
        return SHIPPED_STATUS
    return NOT_SHIPPED_STATUS


def attempt_row(index: int, result: Mapping[str, object]) -> str:
    """One table line: attempt #, decode session id, Session Branch, shipped?, exit code."""
    return _TABLE_ROW.format(
        index=index,
        session=result.get("session_id") or _TABLE_EMPTY,
        branch=result.get("session_branch") or _TABLE_EMPTY,
        status=attempt_status(result),
        exit_code=result.get("exit_code"),
    )


def attempts_table(results: Sequence[Mapping[str, object]]) -> str:
    """The whole comparison table — header, rule, one row per attempt, in launch order."""
    rows = [attempt_row(index, result) for index, result in enumerate(results, start=1)]
    return "\n".join([_TABLE_HEADER, _TABLE_RULE, *rows])


def attempts_notes(results: Sequence[Mapping[str, object]]) -> list[str]:
    """The per-attempt notes, under the table — the *why* behind every row that is not ``shipped``."""
    return [
        f"  attempt {index}: {result['note']}"
        for index, result in enumerate(results, start=1)
        if result.get("note")
    ]


def shipped_branches(results: Sequence[Mapping[str, object]]) -> list[str]:
    """The Session Branches that actually reached origin — the only ones worth a ``git diff``."""
    return [
        str(result["session_branch"])
        for result in results
        if attempt_status(result) == SHIPPED_STATUS
    ]


def compare_commands(results: Sequence[Mapping[str, object]], *, repo: str | None) -> list[str]:
    """The copy-paste tail: read the branches, then diff them against the base and each other.

    A fresh ``git clone`` already carries every remote branch, so the diffs need no explicit fetch.
    Empty without a repo — there is nothing to clone and nothing was shipped.
    """
    if not repo:
        return []
    branches = shipped_branches(results)
    lines = [
        "Compare them:",
        f"  git ls-remote {repo} 'refs/heads/decode/*'",
        f"  git clone {repo} decode-attempts && cd decode-attempts",
    ]
    lines += [f"  git diff origin/HEAD..origin/{branch}" for branch in branches]
    if len(branches) >= 2:
        lines.append("  # one attempt against another:")
        lines.append(f"  git diff origin/{branches[0]}..origin/{branches[1]}")
    return lines


def detach_lines(call_ids: Sequence[str], *, repo: str | None = None) -> list[str]:
    """What a fire-and-forget launch leaves the operator with: the call ids and where to look later."""
    lines = [f"Decode: spawned {len(call_ids)} attempt(s) and stopped waiting (--detach)."]
    lines += [f"  attempt {index}: {call_id}" for index, call_id in enumerate(call_ids, start=1)]
    lines.append("Come back to them with:")
    lines.append("  decode remote logs")
    if repo:
        lines.append(f"  git ls-remote {repo} 'refs/heads/decode/*'")
    lines.append("  uv run kitaru session list --agent decode --origin recorded")
    return lines


def attempts_exit_code(results: Sequence[Mapping[str, object]]) -> int:
    """0 while at least ONE attempt came home clean — the fan-out's whole promise is redundancy."""
    return 0 if any(result.get("exit_code") == 0 for result in results) else 1


# --- pure helpers: everything the nightly cron and the webhook are decided by -----------------------


def nightly_cron(env: Mapping[str, str]) -> str | None:
    """The crontab string to register at deploy — from :data:`NIGHTLY_CRON_ENV` on the LAPTOP — or none.

    Read at import time of the app module, which is deploy time; inside the container the variable
    is absent (it is not part of the job env), which is fine: the schedule already lives on the
    deployed Function. The app wraps it in ``modal.Cron``; this stays a string so it is testable
    without ``modal``.
    """
    cron = env.get(NIGHTLY_CRON_ENV, "").strip()
    return cron or None


def nightly_job_env(env: Mapping[str, str]) -> dict[str, str]:
    """The job's parameters to ship with the deployment — every :data:`NIGHTLY_JOB_ENVS` that is set.

    This is what becomes the ``Secret.from_dict`` env of the nightly Function, so the container reads
    the same names back. Values are configuration, not credentials — a Modal Secret is simply the
    mechanism that carries a deploy-time env into a container.
    """
    return {name: env[name] for name in NIGHTLY_JOB_ENVS if env.get(name, "").strip()}


def _positive_int_error(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    if not value:
        return None
    if not value.isdigit() or int(value) < 1:
        return NIGHTLY_BAD_COUNT_FORMAT.format(env=name, value=value)
    return None


def nightly_config_error(env: Mapping[str, str]) -> str | None:
    """ONE friendly line if the nightly job cannot be deployed as configured, else ``None``.

    Checked on the LAPTOP at deploy, because the alternative is a schedule that fires at 2am into a
    container that immediately exits — money and a night wasted before anyone reads the log.
    Only enforced when a schedule is requested: without :data:`NIGHTLY_CRON_ENV` the job env is inert.
    """
    if nightly_cron(env) is None:
        return None
    if not env.get(NIGHTLY_TASK_ENV, "").strip():
        return NIGHTLY_NO_TASK_FORMAT.format(
            cron_env=NIGHTLY_CRON_ENV,
            task_env=NIGHTLY_TASK_ENV,
            repo_env=NIGHTLY_REPO_ENV,
            mode_env=NIGHTLY_SANDBOX_MODE_ENV,
        )
    mode_error = sandbox_mode_error(env.get(NIGHTLY_SANDBOX_MODE_ENV, DEFAULT_SANDBOX_MODE))
    if mode_error is not None:
        return mode_error
    for name in (NIGHTLY_MAX_REQUESTS_ENV, NIGHTLY_TIMEOUT_ENV):
        count_error = _positive_int_error(env, name)
        if count_error is not None:
            return count_error
    return None


def nightly_run_kwargs(env: Mapping[str, str]) -> dict[str, object] | None:
    """The ``run_task`` kwargs the nightly job runs with, read back from its env — or ``None``.

    ``None`` means the deployment carried no task (the guard above only fires when a schedule was
    requested, so a Function deployed without one can still be invoked by hand and must say why it
    did nothing).
    """
    task = env.get(NIGHTLY_TASK_ENV, "").strip()
    if not task:
        return None
    max_requests = env.get(NIGHTLY_MAX_REQUESTS_ENV, "").strip()
    timeout = env.get(NIGHTLY_TIMEOUT_ENV, "").strip()
    return {
        "task": task,
        "repo": env.get(NIGHTLY_REPO_ENV, "").strip() or None,
        "sandbox_mode": env.get(NIGHTLY_SANDBOX_MODE_ENV, "").strip() or DEFAULT_SANDBOX_MODE,
        "model": env.get(NIGHTLY_MODEL_ENV, "").strip() or None,
        "max_requests": int(max_requests) if max_requests else None,
        "timeout_seconds": int(timeout) if timeout else DEFAULT_TIMEOUT_SECONDS,
    }


class WebhookRequest(BaseModel):
    """One POSTed run: the same knobs ``decode remote run`` takes on the command line, as JSON."""

    task: str
    repo: str | None = None
    sandbox_mode: str = DEFAULT_SANDBOX_MODE
    model: str | None = None
    max_requests: int | None = Field(default=None, ge=1)
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, ge=1)


def webhook_request_error(request: WebhookRequest) -> str | None:
    """ONE friendly line if the POSTed run cannot be spawned, else ``None``.

    The same mode guard as every other surface (``docker`` costs one line, no container) plus an
    empty task — the one thing the schema cannot rule out.
    """
    if not request.task.strip():
        return WEBHOOK_EMPTY_TASK_MESSAGE
    return sandbox_mode_error(request.sandbox_mode)


def webhook_spawn_kwargs(request: WebhookRequest) -> dict[str, object]:
    """The ``run_task`` kwargs one webhook request spawns with."""
    return {
        "task": request.task.strip(),
        "repo": request.repo,
        "sandbox_mode": request.sandbox_mode,
        "model": request.model,
        "max_requests": request.max_requests,
        "timeout_seconds": request.timeout_seconds,
    }


def webhook_response(call_id: str, request: WebhookRequest) -> dict[str, object]:
    """What the webhook answers at once: the call id, the run's shape, and where to look for it."""
    return {
        "call_id": call_id,
        "sandbox_mode": request.sandbox_mode,
        "repo": request.repo,
        "status": "spawned",
        "watch": [
            f"modal app logs {APP_NAME}",
            "uv run kitaru session list --agent decode --origin recorded",
        ]
        + ([f"git ls-remote {request.repo} 'refs/heads/decode/*'"] if request.repo else []),
    }


# --- inside the container: the subprocess one run is ------------------------------------------------


def stream_subprocess(
    argv: list[str], *, cwd: str, env: Mapping[str, str], timeout_seconds: int
) -> tuple[str, int]:
    """Run ``argv`` to completion, relaying its stdout live, and return ``(stdout, exit_code)``.

    The child's stderr is INHERITED, so decode's diagnostics stream straight into the Function log
    while stdout — the answer — is captured line by line and echoed as it arrives (a headless run is
    long; an operator watching ``decode remote run`` should see it move). A run past
    ``timeout_seconds`` is killed by a timer thread — with ONE line saying so, since a bare
    ``exit=-9`` reads like an agent failure — which turns a hang into a normal non-zero exit with
    partial output instead of a container that dies at the Function ceiling with nothing to show.
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
    timed_out = threading.Event()

    def kill_on_timeout() -> None:
        timed_out.set()
        process.kill()

    killer = threading.Timer(timeout_seconds, kill_on_timeout)
    killer.start()
    chunks: list[str] = []
    try:
        for line in process.stdout:  # ty: ignore[possibly-unbound-attribute]
            chunks.append(line)
            click.echo(line, nl=False)
        exit_code = process.wait()
    finally:
        killer.cancel()
    if timed_out.is_set():
        click.echo(TIMEOUT_KILL_FORMAT.format(timeout_seconds=timeout_seconds), err=True)
    return "".join(chunks), exit_code


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


def reset_child_log(path: str = LOG_FILE) -> None:
    """Delete a leftover child log before a run — the ids must belong to THIS attempt.

    decode appends to ``DECODE_LOG_FILE`` and Modal re-uses warm containers, so a second input in the
    same container would otherwise read the FIRST input's ``session_id=`` line back out of the file
    (found live: a fan-out row whose session and branch named two different agents). Best-effort: a
    log that cannot be removed costs the ids, never the run.
    """
    with contextlib.suppress(OSError):
        Path(path).unlink(missing_ok=True)


def read_child_log(path: str = LOG_FILE) -> str:
    """The child's log file, or ``""`` — best-effort: a missing log costs the ids, never the run."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def execute_run(
    *,
    task: str,
    repo: str | None = None,
    sandbox_mode: str = DEFAULT_SANDBOX_MODE,
    model: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_requests: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Run ONE headless decode task in THIS process's container and return its small payload.

    The body of the ``run_task`` Function, kept ``modal``-free so it is unit-testable on a laptop:
    reject an unrunnable mode, give git the credential helper if the operator handed one over, clone
    the repo when the harness owns the clone (``none`` mode), then run the baked ``decode run``
    console script as a subprocess and read its log back. Nothing about the agent is re-implemented
    here — that is the point of ADR-0020 §1. ``environ`` is the container's process env (the
    Secret); it defaults to ``os.environ`` at call time.
    """
    base_env = os.environ if environ is None else environ
    mode_error = sandbox_mode_error(sandbox_mode)
    if mode_error is not None:
        # Defensive twin of the laptop-side guard, for a direct ``.remote()`` / ``.spawn()``
        # caller (task 143): ONE line, a non-zero code in the payload, no traceback.
        click.echo(mode_error, err=True)
        return build_result(
            sandbox_mode=sandbox_mode,
            repo=repo,
            exit_code=SANDBOX_MODE_REJECTED_EXIT,
            stdout=mode_error,
            log_text="",
        )

    env = decode_run_env(base_env, sandbox_mode=sandbox_mode)
    Path(HARNESS_HOME).mkdir(parents=True, exist_ok=True)
    reset_child_log()

    credential_argv = git_credential_argv(base_env)
    if credential_argv is not None:
        subprocess.run(credential_argv, check=True)

    if sandbox_mode == "none" and repo:
        clone_for_none_mode(repo, env)

    stdout, exit_code = stream_subprocess(
        decode_argv(
            task=task, sandbox_mode=sandbox_mode, repo=repo, model=model, max_requests=max_requests
        ),
        cwd=decode_cwd(sandbox_mode=sandbox_mode, repo=repo),
        env=env,
        timeout_seconds=timeout_seconds,
    )
    result = build_result(
        sandbox_mode=sandbox_mode,
        repo=repo,
        exit_code=exit_code,
        stdout=stdout,
        log_text=read_child_log(),
    )
    click.echo(RUN_SUMMARY_FORMAT.format(**result), err=True)
    if result["note"]:
        click.echo(f"Decode: {result['note']}", err=True)
    return result
