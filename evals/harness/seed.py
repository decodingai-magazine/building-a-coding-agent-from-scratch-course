"""Build a task's Seed Repo — the ONE seeding path both the oracle gate and the trial use (ADR-0022 §5).

A Benchmark Task's ``environment/`` is not handed to the agent as a bare directory: it becomes the
BASE COMMIT of a fresh, origin-less git repo (the **Seed Repo**). That is what makes the rest of the
pipeline work — ``decode run --repo <seed>`` clones it into the sandbox, the Hand-back pushes the
agent's work back as the ``decode/<short-id>`` Session Branch, and the Verifier grades a pristine
clone of that branch (ADR-0022 §1,§3).

:func:`seed_task_repo` is used by BOTH the oracle-sanity gate (:mod:`evals.harness.oracle_sanity`,
keyless in ``make ci``) and the trial runner, so CI proves the seeding path itself, not a stand-in.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from evals.harness.task_loader import SETUP_SCRIPT_NAME, BenchmarkTask

# Wall clock for a task's ``environment/setup.sh``.
SETUP_TIMEOUT_S = 300.0

# The identity the base commit is authored with — fixed, so a Seed Repo is reproducible and a
# verifier that inspects history can tell the seed's own commit from the agent's.
GIT_USER_NAME = "decode-evals"
GIT_USER_EMAIL = "evals@decode.local"

# The single branch a Seed Repo lives on (adopted histories must be on it too).
SEED_BRANCH = "main"

# The base commit's message when the seeder creates the history itself.
BASE_COMMIT_MESSAGE = "base"


class SeedError(Exception):
    """Seeding a task's repo failed (setup.sh non-zero, a dirty or off-branch adopted ``.git``, …)."""


@dataclass(frozen=True, slots=True)
class SeedInfo:
    """What a caller needs about a freshly seeded repo: the sha the agent's work is measured from."""

    base_sha: str


def seed_task_repo(task: BenchmarkTask, dest: Path) -> SeedInfo:
    """Materialise ``task``'s Seed Repo at ``dest`` and return its base commit (ADR-0022 §2,§5).

    Copies ``environment/`` into ``dest`` (creating it if needed), runs the task's optional
    ``setup.sh`` there, then gives the tree a git history one of two ways:

    * **adopted** — ``setup.sh`` built its own ``.git`` (the git-history tasks, e.g.
      018-git-bisect-revert). It is kept verbatim and only checked: at least one commit, on
      ``main``, clean, no remote.
    * **created** — otherwise ``git init -b main`` plus a single ``base`` commit authored as
      ``decode-evals <evals@decode.local>``.

    ``setup.sh`` itself is deliberately NOT copied into the seed: it is scaffolding that CONSTRUCTS
    the task (018's buggy history, 013's database), so handing it to the agent would hand over the
    recipe — and an uncommitted copy would leave an adopted history dirty. It runs from the task
    folder with ``dest`` as its cwd, so ``$(dirname "$0")`` still reaches its own siblings.

    Raises :class:`SeedError` on a failing ``setup.sh`` or an adopted ``.git`` that breaks a rule.
    """
    dest.mkdir(parents=True, exist_ok=True)
    _copy_environment(task, dest)
    if task.setup_script.is_file():
        _run_setup_script(task, dest)
    if (dest / ".git").exists():
        return SeedInfo(base_sha=_adopt_history(task, dest))
    return SeedInfo(base_sha=_create_history(dest))


def _copy_environment(task: BenchmarkTask, dest: Path) -> None:
    """Overlay ``environment/`` onto ``dest`` — everything except the top-level ``setup.sh``."""
    if not task.environment_dir.is_dir():
        return
    environment_root = str(task.environment_dir)

    def _skip_setup_script(directory: str, names: list[str]) -> set[str]:
        if Path(directory) == Path(environment_root) and SETUP_SCRIPT_NAME in names:
            return {SETUP_SCRIPT_NAME}
        return set()

    shutil.copytree(task.environment_dir, dest, dirs_exist_ok=True, ignore=_skip_setup_script)


def _run_setup_script(task: BenchmarkTask, dest: Path) -> None:
    """Run the task's ``environment/setup.sh`` with ``dest`` as cwd; a non-zero exit is fatal."""
    try:
        result = subprocess.run(
            ["bash", str(task.setup_script)],
            cwd=dest,
            capture_output=True,
            text=True,
            timeout=SETUP_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SeedError(
            f"{task.id}: environment/setup.sh timed out after {SETUP_TIMEOUT_S:.0f}s in {dest}"
        ) from exc
    if result.returncode != 0:
        raise SeedError(
            f"{task.id}: environment/setup.sh failed (exit {result.returncode}) in {dest}: "
            f"{result.stderr.strip()}"
        )


def _create_history(dest: Path) -> str:
    """``git init -b main`` + one ``base`` commit by the fixed evals identity; return its sha."""
    _git(dest, "init", "-b", SEED_BRANCH, "-q")
    _git(dest, "add", "-A")
    # ``--allow-empty`` so a task with an empty seed tree still has a base commit to branch from.
    _git(dest, "commit", "-q", "--allow-empty", "-m", BASE_COMMIT_MESSAGE)
    return _git(dest, "rev-parse", "HEAD")


def _adopt_history(task: BenchmarkTask, dest: Path) -> str:
    """Keep a ``setup.sh``-built history verbatim after checking the four Seed Repo rules."""
    if not _git_succeeds(dest, "rev-parse", "--verify", "-q", "HEAD"):
        raise SeedError(f"{task.id}: setup.sh left a .git with no commit at all in {dest}")
    branch = _git(dest, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != SEED_BRANCH:
        raise SeedError(
            f"{task.id}: the .git setup.sh built is on {branch!r}; a Seed Repo must be on "
            f"{SEED_BRANCH!r}"
        )
    status = _git(dest, "status", "--porcelain")
    if status:
        raise SeedError(
            f"{task.id}: the .git setup.sh built must be clean, but {dest} still has:\n{status}"
        )
    remotes = _git(dest, "remote")
    if remotes:
        raise SeedError(
            f"{task.id}: a Seed Repo carries no remote, but setup.sh configured: "
            f"{remotes.splitlines()}"
        )
    return _git(dest, "rev-parse", "HEAD")


def _git(dest: Path, *args: str) -> str:
    """Run one git command in ``dest``, raising :class:`SeedError` on a non-zero exit."""
    result = _run_git(dest, *args)
    if result.returncode != 0:
        raise SeedError(
            f"git {' '.join(args)} failed in {dest} (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def _git_succeeds(dest: Path, *args: str) -> bool:
    """True when the git command exits 0 — for probes (does HEAD resolve?), never for output."""
    return _run_git(dest, *args).returncode == 0


def _run_git(dest: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke git in ``dest`` under the fixed evals identity, capturing output.

    The identity and ``commit.gpgsign=false`` ride ``-c`` flags rather than repo config, so the same
    call works on a laptop with a signing key configured globally and on a bare CI image with no
    identity at all.
    """
    return subprocess.run(
        [
            "git",
            "-C",
            str(dest),
            "-c",
            f"user.name={GIT_USER_NAME}",
            "-c",
            f"user.email={GIT_USER_EMAIL}",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
