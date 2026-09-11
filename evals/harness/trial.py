"""One Benchmark Trial: seed → ``decode run`` → hand-back → host-side verify → Trial Dir (ADR-0022).

A Trial measures the SHIPPED product, not a private driver: it launches ``decode run`` as a
subprocess (§1), so the sandbox seam, the request cap, the Recording Seam and the Hand-back are all
inside the measurement. Five phases, in this order:

1. **seed** — :func:`~evals.harness.seed.seed_task_repo` materialises the task's Seed Repo and its
   base commit;
2. **run** — ``decode run "<instruction>" --repo <seed> --local --max-requests <max_steps>
   --summary-json <path>`` in its OWN process group, capped at the task's wall clock; past it the
   group gets a SIGINT (so decode's ``finally`` reaps the sandbox and hands back), a grace period,
   then SIGKILL;
3. **collect** — the run's summary file is ground truth: which Session Branch carries the work, how
   the run ended, what it spent (no trace parsing, ADR-0022 §1);
4. **verify** — host-side, on a PRISTINE ``git clone`` of that branch (the base commit when the
   Hand-back skipped) with the hidden ``tests/`` overlaid LAST
   (:func:`~evals.harness.verifier.grade_checkout`). No sandbox seam at grade time: the agent never
   saw the Verifier, its edits to ``tests/`` are inert, and a reward is reproducible from the Trial
   Dir with a bare ``bash``;
5. **record** — the Trial Dir, written in a ``finally`` so an Infra Error still leaves evidence.

The status taxonomy (§4) is the point of all of it: ``agent_ok`` ⇔ reward 1; ``agent_fail`` is a
graded loss — a wrong answer, a request ceiling, a timeout, a skipped Hand-back (base graded) — and
counts in the denominator; ``infra_error`` is the harness failing (seed, clone, a Verifier that
crashed or wrote nothing readable) and is excluded from both numerator and denominator. **A timeout
is never an Infra Error**, even when the wedged run never wrote a summary: a hung agent is the worst
agent behaviour there is, and dropping it would flatter every score.

This module deliberately imports nothing from ``decode.sandbox`` / ``decode.tools`` — the sandbox
lives inside the subprocess and the Verifier runs host-side, so a trial needs neither (a unit test
asserts it).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr

from decode.config.settings import Settings, settings
from evals.harness.seed import seed_task_repo
from evals.harness.task_loader import BenchmarkTask
from evals.harness.verifier import (
    REWARD_FILE_NAME,
    VERIFIER_DIR_NAME,
    VerifierResult,
    grade_checkout,
)

logger = logging.getLogger(__name__)

# How a trial ended (ADR-0022 §4). Only ``agent_*`` counts in a pass rate.
TrialStatus = Literal["agent_ok", "agent_fail", "infra_error"]

# The child command a trial drives. ``-m decode`` (not the ``decode`` script) on the PARENT's
# interpreter, so the subprocess is guaranteed to run the harness's own venv.
DEFAULT_DECODE_COMMAND: tuple[str, ...] = (sys.executable, "-m", "decode")

# Seconds the interrupted run gets to reap its sandbox, hand back and write its summary before the
# trial escalates to SIGKILL. Generous: a docker rm + a git push is the work being waited on.
SIGINT_GRACE_S = 60.0

# Seconds to reap the process after a SIGKILL — the kernel's, not the process's, so it is short.
SIGKILL_REAP_S = 10.0

# Env vars a trial must NOT inherit: a Kitaru Worker Task id or replay id would make the child think
# it is a replay (and hard-fail on recording), and an ambient SANDBOX_REPO would clone the wrong repo
# over the Seed Repo the trial just built (ADR-0022 §1).
STRIPPED_ENV_VARS: tuple[str, ...] = ("KITARU_TASK_ID", "SANDBOX_REPO", "KITARU_REPLAY_ID")

# The Trial Dir's fixed layout (ADR-0022 §7); ``home`` is the subprocess's throwaway Harness Home.
SEED_DIR_NAME = "seed"
HOME_DIR_NAME = "home"
PRISTINE_DIR_NAME = "pristine"
AGENT_DIR_NAME = "agent"
VERIFIER_OUTPUT_DIR_NAME = "verifier"
RESULT_FILE_NAME = "result.json"


class _InfraError(Exception):
    """The harness failed, not the agent — carries the one-line ``reason`` for ``result.json``."""


@dataclass(frozen=True, slots=True)
class TrialResult:
    """The verdict of one Trial, mirroring ``result.json`` (ADR-0022 §7).

    ``reward`` is ``None`` on an Infra Error only: a graded trial always has a number, and a
    ``None`` reward must never be read as a zero. ``trial_dir`` is the evidence directory and is the
    one field not in ``result.json`` (the file already knows where it lives).
    """

    task_id: str
    trial_id: str
    status: TrialStatus
    reason: str | None
    reward: float | None
    timed_out: bool
    base_sha: str | None
    branch: str | None
    agent: dict[str, Any]
    summary: dict[str, Any] | None
    timings: dict[str, float | None]
    trial_dir: Path = field(compare=False)

    @property
    def passed(self) -> bool:
        """True only for ``agent_ok`` — the single definition of a won trial."""
        return self.status == "agent_ok"

    def as_json_dict(self) -> dict[str, Any]:
        """The ``result.json`` payload: plain types only, no ``Settings`` value beyond provenance."""
        return {
            "task_id": self.task_id,
            "trial_id": self.trial_id,
            "status": self.status,
            "reason": self.reason,
            "reward": self.reward,
            "timed_out": self.timed_out,
            "base_sha": self.base_sha,
            "branch": self.branch,
            "agent": dict(self.agent),
            "summary": self.summary,
            "timings": dict(self.timings),
        }


def run_trial(
    task: BenchmarkTask,
    *,
    sandbox: Literal["docker", "modal"],
    job_dir: Path,
    trial_id: str,
    model: str | None = None,
    decode_command: Sequence[str] = DEFAULT_DECODE_COMMAND,
) -> TrialResult:
    """Run and grade ONE trial of ``task``, returning its verdict — never raising (ADR-0022 §1,§3,§4).

    The Trial Dir is ``<job_dir>/<task-id>__<trial_id>/``, resolved absolute; every phase writes its
    evidence there and
    ``result.json`` lands in a ``finally``, so even a trial that fell over on its first phase leaves
    a readable record.

    ``run_trial`` NEVER raises. Opik's ``evaluate`` runs task functions with no per-item isolation —
    one raised trial aborts the whole experiment — so an unexpected exception becomes an
    ``infra_error`` with its message as the ``reason`` and a full traceback in the log.
    """
    # RESOLVED once, up front: the subprocess runs from ``home/`` and every path the trial hands it
    # (the Seed Repo, the summary file) must survive that cwd change (the same rule
    # ``load_benchmark_task`` follows for a task folder).
    trial_dir = (job_dir / f"{task.id}__{trial_id}").resolve()
    timings: dict[str, float | None] = {"seed": None, "run": None, "verify": None}
    status: TrialStatus = "infra_error"
    reason: str | None = "the trial did not complete"
    base_sha: str | None = None
    branch: str | None = None
    summary: dict[str, Any] | None = None
    reward: float | None = None
    timed_out = False

    try:
        agent_dir, home_dir, seed_dir = _prepare_trial_dir(trial_dir)

        with _phase(timings, "seed"):
            try:
                base_sha = seed_task_repo(task, seed_dir).base_sha
            except Exception as exc:
                raise _InfraError(f"seeding the task repo failed: {exc}") from exc

        with _phase(timings, "run"):
            timed_out = _run_agent(
                task,
                seed_dir=seed_dir,
                home_dir=home_dir,
                agent_dir=agent_dir,
                sandbox=sandbox,
                model=model,
                decode_command=decode_command,
            )

        summary = _read_summary(agent_dir / "summary.json")
        if summary is None and not timed_out:
            # A run that died before reporting cannot be attributed: the harness lost it.
            raise _InfraError("`decode run` exited without writing a summary")
        branch = _handback_branch(summary)

        with _phase(timings, "verify"):
            reward = _verify(task, trial_dir=trial_dir, branch=branch)

        status, reason = _classify(
            task, reward=reward, summary=summary, timed_out=timed_out, branch=branch
        )
    except _InfraError as exc:
        logger.warning("[eval] trial %s/%s is an infra error: %s", task.id, trial_id, exc)
        status, reason = "infra_error", str(exc)
        reward = None
    except Exception as exc:  # a raised trial would abort the WHOLE Opik experiment
        logger.exception("[eval] trial %s/%s raised", task.id, trial_id)
        status, reason = "infra_error", f"the trial raised: {exc}"
        reward = None
    finally:
        result = TrialResult(
            task_id=task.id,
            trial_id=trial_id,
            status=status,
            reason=reason,
            reward=reward,
            timed_out=timed_out,
            base_sha=base_sha,
            branch=branch,
            agent=agent_info(sandbox=sandbox, model=model),
            summary=summary,
            timings=timings,
            trial_dir=trial_dir,
        )
        _write_result(trial_dir, result)
    return result


def child_env(
    *,
    sandbox: str,
    env: Mapping[str, str] | None = None,
    config: Settings | None = None,
) -> dict[str, str]:
    """The environment the ``decode run`` subprocess gets — pure, so it is testable on its own.

    The parent's environment plus every EXPLICITLY SET ``Settings`` field exported under its env-var
    name (``Settings`` declares no ``env_prefix`` and no aliases, so the name is simply the field
    name upper-cased). That export is what lets a trial launch from a throwaway Harness Home with no
    ``.env`` in it and still reach the provider key that lives only in the repo's ``.env``. It is
    NOT the ADR-0016 anti-pattern: that forbids pouring config into the sandbox WORKER; this is the
    harness handing its own child process its own config.

    Three overrides and three removals make the child a trial rather than a normal run: the sandbox
    mode is the trial's rung, tracing goes to the eval project (live tracing is never polluted) and
    the headless runtime is on; a Kitaru Worker Task / replay id and an ambient ``SANDBOX_REPO``
    would each make the child do something other than this trial. ``KITARU_AGENT_ID`` passes
    through untouched — recording rides the Recording Seam exactly as it does for a user's run.
    """
    config = config if config is not None else settings
    child = dict(os.environ if env is None else env)
    child.update(_settings_env(config))
    child["SANDBOX_MODE"] = sandbox
    child["OPIK_PROJECT_NAME"] = config.eval_project_name
    child["RUNTIME_ENABLED"] = "true"
    for name in STRIPPED_ENV_VARS:
        child.pop(name, None)
    return child


def agent_info(*, sandbox: str, model: str | None) -> dict[str, Any]:
    """What produced this trial: the code, the package version, the model, provider and rung.

    Enough to tell two Trial Dirs apart by what actually changed between them — the same provenance
    the Opik ``experiment_config`` carries (ADR-0022 §6,§7).
    """
    return {
        "git_sha": git_sha(),
        "decode_version": decode_version(),
        "model": model or settings.active_model,
        "provider": settings.llm_provider,
        "sandbox": sandbox,
    }


def git_sha() -> str:
    """The current commit sha, or ``"unknown"`` if git is unavailable (never crash a trial on it)."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def decode_version() -> str:
    """The installed ``decode`` version, or ``"unknown"`` — provenance never fails a trial."""
    try:
        return version("decode")
    except PackageNotFoundError:
        return "unknown"


def _prepare_trial_dir(trial_dir: Path) -> tuple[Path, Path, Path]:
    """Create the Trial Dir's fixed layout and return ``(agent, home, seed)``."""
    agent_dir = trial_dir / AGENT_DIR_NAME
    home_dir = trial_dir / HOME_DIR_NAME
    seed_dir = trial_dir / SEED_DIR_NAME
    for directory in (agent_dir, home_dir, seed_dir, trial_dir / VERIFIER_OUTPUT_DIR_NAME):
        directory.mkdir(parents=True, exist_ok=True)
    return agent_dir, home_dir, seed_dir


def _run_agent(
    task: BenchmarkTask,
    *,
    seed_dir: Path,
    home_dir: Path,
    agent_dir: Path,
    sandbox: str,
    model: str | None,
    decode_command: Sequence[str],
) -> bool:
    """Drive ``decode run`` to completion (or to its wall clock); ``True`` if it had to be stopped.

    The child runs from the throwaway Harness Home, so everything decode anchors to Harness Home —
    its sessions, its logs, its ``.decode/sandbox`` Workspace clone — lands inside the Trial Dir as
    evidence rather than in the operator's repo (ADR-0012 §6).
    """
    command = [
        *decode_command,
        "run",
        task.instruction,
        "--repo",
        str(seed_dir),
        "--local",
        "--max-requests",
        str(task.max_steps),
        "--summary-json",
        str(agent_dir / "summary.json"),
        *(["--model", model] if model else []),
    ]
    logger.debug("[eval] trial command: %s (cwd=%s)", command, home_dir)
    with (
        (agent_dir / "stdout.txt").open("w", encoding="utf-8") as stdout,
        (agent_dir / "stderr.txt").open("w", encoding="utf-8") as stderr,
    ):
        process = subprocess.Popen(
            command,
            cwd=home_dir,
            stdout=stdout,
            stderr=stderr,
            env=child_env(sandbox=sandbox),
            # Its OWN process group, so the timeout's SIGINT reaches decode AND everything it
            # spawned (a docker exec, a git push) rather than just the interpreter.
            start_new_session=True,
        )
        return _wait_with_timeout(process, task)


def _wait_with_timeout(process: subprocess.Popen[bytes], task: BenchmarkTask) -> bool:
    """Wait for the run; on the wall clock SIGINT the group, grace, then SIGKILL. ``True`` if late.

    SIGINT first (never SIGKILL first) because decode's own ``finally`` is what reaps the sandbox and
    hands the Workspace back as a Session Branch — the partial work a timed-out trial is graded on.
    """
    try:
        process.wait(timeout=task.agent_timeout_sec)
        return False
    except subprocess.TimeoutExpired:
        logger.warning(
            "[eval] %s exceeded its %.0fs budget; interrupting the run",
            task.id,
            task.agent_timeout_sec,
        )
    _signal_group(process, signal.SIGINT)
    try:
        process.wait(timeout=SIGINT_GRACE_S)
    except subprocess.TimeoutExpired:
        logger.warning(
            "[eval] %s ignored the interrupt for %.0fs; killing it", task.id, SIGINT_GRACE_S
        )
        _signal_group(process, signal.SIGKILL)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=SIGKILL_REAP_S)
    return True


def _signal_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    """Signal the child's whole process group, tolerating a child that just exited on its own."""
    with suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(process.pid), sig)


def _read_summary(path: Path) -> dict[str, Any] | None:
    """The run's ``--summary-json`` object, or ``None`` when the run never wrote one."""
    if not path.is_file():
        return None
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _InfraError(f"`decode run` wrote an unreadable summary: {exc}") from exc
    if not isinstance(summary, dict):
        raise _InfraError("`decode run` wrote a summary that is not a json object")
    return summary


def _handback_branch(summary: Mapping[str, Any] | None) -> str | None:
    """The Session Branch the Hand-back pushed, or ``None`` — a skip grades the base commit."""
    if summary is None:
        return None
    handback = summary.get("handback")
    if not isinstance(handback, Mapping):
        return None
    branch = handback.get("branch")
    return branch if isinstance(branch, str) and branch else None


def _verify(task: BenchmarkTask, *, trial_dir: Path, branch: str | None) -> float:
    """Grade a pristine clone of ``branch`` (or of the base commit) host-side (ADR-0022 §3)."""
    pristine = trial_dir / PRISTINE_DIR_NAME
    _clone_pristine(trial_dir / SEED_DIR_NAME, pristine, branch)
    result = grade_checkout(task, pristine)
    _record_verifier_output(result, pristine, trial_dir / VERIFIER_OUTPUT_DIR_NAME)
    if result.timed_out:
        raise _InfraError(f"the Verifier timed out after {task.verifier_timeout_sec:.0f}s")
    if result.reward is None:
        raise _InfraError(
            "the Verifier wrote no readable reward "
            f"({VERIFIER_DIR_NAME}/{REWARD_FILE_NAME} missing, empty or non-numeric)"
        )
    if not 0.0 <= result.reward <= 1.0:
        # Catches NaN for free (every comparison with NaN is False).
        raise _InfraError(f"the Verifier wrote a reward outside [0, 1]: {result.reward!r}")
    return result.reward


def _clone_pristine(seed_dir: Path, pristine: Path, branch: str | None) -> None:
    """``git clone [--branch <branch>] <seed> <pristine>`` — a fresh checkout the agent never touched."""
    shutil.rmtree(pristine, ignore_errors=True)
    command = ["git", "clone", "--quiet"]
    if branch is not None:
        command += ["--branch", branch]
    command += [str(seed_dir), str(pristine)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise _InfraError(
            f"the pristine clone of {branch or 'the base commit'} failed "
            f"(exit {completed.returncode}): {completed.stderr.strip()}"
        )


def _record_verifier_output(result: VerifierResult, pristine: Path, verifier_dir: Path) -> None:
    """Copy the Verifier's evidence into the Trial Dir — before any error is raised on it."""
    verifier_dir.mkdir(parents=True, exist_ok=True)
    (verifier_dir / "test-stdout.txt").write_text(result.stdout, encoding="utf-8")
    (verifier_dir / "test-stderr.txt").write_text(result.stderr, encoding="utf-8")
    written = pristine / VERIFIER_DIR_NAME / REWARD_FILE_NAME
    if written.is_file():
        shutil.copyfile(written, verifier_dir / REWARD_FILE_NAME)


def _classify(
    task: BenchmarkTask,
    *,
    reward: float,
    summary: Mapping[str, Any] | None,
    timed_out: bool,
    branch: str | None,
) -> tuple[TrialStatus, str | None]:
    """The ADR-0022 §4 taxonomy for a trial that reached a reward: won, or lost with a reason."""
    if reward == 1.0:
        return "agent_ok", None
    return "agent_fail", _failure_reason(
        task, reward=reward, summary=summary, timed_out=timed_out, branch=branch
    )


def _failure_reason(
    task: BenchmarkTask,
    *,
    reward: float,
    summary: Mapping[str, Any] | None,
    timed_out: bool,
    branch: str | None,
) -> str:
    """One line naming everything that shaped a losing grade, for a human reading ``result.json``."""
    parts: list[str] = []
    if timed_out:
        parts.append(f"the agent timed out after {task.agent_timeout_sec:.0f}s")
    if summary is None:
        parts.append("it never wrote a summary")
    elif summary.get("exit_reason") == "request_limit":
        parts.append(f"the run hit its {task.max_steps}-request ceiling")
    elif summary.get("exit_reason") == "error":
        parts.append(f"the run ended with an error: {summary.get('error')}")
    if branch is None:
        parts.append("the Hand-back shipped no branch, so the base commit was graded")
    parts.append(f"the Verifier scored {reward}")
    return "; ".join(parts)


def _write_result(trial_dir: Path, result: TrialResult) -> None:
    """Write ``result.json`` — best-effort, because it runs in the trial's ``finally``.

    A failure here (an unwritable dir, a summary value nothing can serialise) must not replace the
    verdict the trial already reached with an exception nobody can attribute.
    """
    try:
        trial_dir.mkdir(parents=True, exist_ok=True)
        (trial_dir / RESULT_FILE_NAME).write_text(
            json.dumps(result.as_json_dict(), indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        logger.warning("[eval] could not write %s", trial_dir / RESULT_FILE_NAME, exc_info=True)


@contextmanager
def _phase(timings: dict[str, float | None], name: str) -> Iterator[None]:
    """Time one phase into ``timings`` — on the failing path too, so a slow failure is visible."""
    started = time.monotonic()
    try:
        yield
    finally:
        timings[name] = round(time.monotonic() - started, 3)


def _settings_env(config: Settings) -> dict[str, str]:
    """Every explicitly-set ``Settings`` field as ``NAME=value``, ready for the child's own parser.

    Rendering matches what pydantic-settings reads back: JSON for the complex types (a ``list``
    field), plain strings for paths and scalars, lowercase booleans, unwrapped secrets. An unset
    (``None``) value and an empty secret are exported as NOTHING — ``GEMINI_API_KEY=`` in the child
    would look "set" to a presence check and then fail at the provider.
    """
    exported: dict[str, str] = {}
    for name in sorted(config.model_fields_set):
        rendered = _render(getattr(config, name, None))
        if rendered is not None:
            exported[name.upper()] = rendered
    return exported


def _render(value: Any) -> str | None:
    """One ``Settings`` value as an env-var string, or ``None`` when it must not be exported."""
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value.get_secret_value() or None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value)
    return str(value)
