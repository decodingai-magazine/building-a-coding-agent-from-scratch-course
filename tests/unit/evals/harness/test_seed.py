"""The Seed Repo builder: both branches, host-side, no network (ADR-0022 §2,§5; task 157).

``seed_task_repo`` is the ONE seeding path — the keyless oracle gate and the trial runner share it,
so these tests are what proves the path CI exercises is the path a paid run uses. Two branches:

* **created** — a task whose ``environment/`` is plain files gets ``git init -b main`` plus one
  ``base`` commit authored by the fixed evals identity;
* **adopted** — a task whose ``setup.sh`` BUILDS git history (018-git-bisect-revert) keeps that
  history verbatim; the seeder only checks it is clean, non-empty, on ``main`` and origin-less.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support.benchmark_tasks import CANARY_LINE, write_task_dir

from evals.harness.seed import GIT_USER_EMAIL, GIT_USER_NAME, SeedError, seed_task_repo
from evals.harness.task_loader import load_benchmark_task

# A ``setup.sh`` that builds its own two-commit history — the 018-git-bisect-revert shape.
_HISTORY_SETUP = f"""#!/usr/bin/env bash
# {CANARY_LINE}
set -euo pipefail
git init -b main -q .
git -c user.name=seed -c user.email=seed@example.com -c commit.gpgsign=false add -A
git -c user.name=seed -c user.email=seed@example.com -c commit.gpgsign=false commit -q -m "first"
echo "second" > second.txt
git -c user.name=seed -c user.email=seed@example.com -c commit.gpgsign=false add -A
git -c user.name=seed -c user.email=seed@example.com -c commit.gpgsign=false commit -q -m "second"
"""


def _git(dest: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(dest), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_setup_sh_runs_with_the_allow_listed_host_env(tmp_path: Path, monkeypatch) -> None:
    """A seed reads no operator secret: ``setup.sh`` gets the Verifier's allow-list, not ``.env``."""
    monkeypatch.setenv("SECRET_SENTINEL", "sk-sentinel-do-not-leak")
    task = load_benchmark_task(
        write_task_dir(
            tmp_path / "001-synthetic",
            setup_script=(
                f"#!/usr/bin/env bash\n# {CANARY_LINE}\nset -uo pipefail\n"
                'printf "%s" "${SECRET_SENTINEL:-}" > sentinel.txt\n'
                'printf "%s" "${PATH:-}" > path.txt\n'
            ),
        )
    )

    seed_task_repo(task, tmp_path / "seed")

    assert (tmp_path / "seed" / "sentinel.txt").read_text(encoding="utf-8") == ""
    assert (tmp_path / "seed" / "path.txt").read_text(encoding="utf-8") != ""


# --- the created branch -----------------------------------------------------------------------


def test_seeds_a_fresh_repo_with_one_base_commit(greeting_task_dir: Path, tmp_path: Path) -> None:
    task = load_benchmark_task(greeting_task_dir)

    info = seed_task_repo(task, tmp_path / "seed")

    dest = tmp_path / "seed"
    assert (dest / "README.md").is_file()  # a committed environment/ file
    assert _git(dest, "rev-parse", "HEAD") == info.base_sha
    assert _git(dest, "rev-list", "--count", "HEAD") == "1"
    assert _git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(dest, "log", "-1", "--format=%s") == "base"
    assert _git(dest, "log", "-1", "--format=%an <%ae>") == f"{GIT_USER_NAME} <{GIT_USER_EMAIL}>"
    assert _git(dest, "status", "--porcelain") == ""
    assert _git(dest, "remote") == ""  # origin-less: the trial's Hand-back adds the only remote


def test_runs_setup_sh_and_removes_it_from_the_seed(
    greeting_task_dir: Path, tmp_path: Path
) -> None:
    """``setup.sh`` is scaffolding, never delivered seed — the agent must not read the recipe."""
    task = load_benchmark_task(greeting_task_dir)

    seed_task_repo(task, tmp_path / "seed")

    dest = tmp_path / "seed"
    assert (dest / "seeded.txt").read_text(encoding="utf-8") == "seeded\n"  # setup.sh ran
    assert not (dest / "setup.sh").exists()
    assert "setup.sh" not in _git(dest, "ls-files")


def test_seeds_a_task_without_a_setup_script(tmp_path: Path) -> None:
    task_dir = write_task_dir(tmp_path / "001-synthetic")

    info = seed_task_repo(load_benchmark_task(task_dir), tmp_path / "seed")

    assert (tmp_path / "seed" / "seed.txt").is_file()
    assert info.base_sha


def test_creates_the_destination_when_absent(tmp_path: Path) -> None:
    task_dir = write_task_dir(tmp_path / "001-synthetic")

    seed_task_repo(load_benchmark_task(task_dir), tmp_path / "nested" / "seed")

    assert (tmp_path / "nested" / "seed" / ".git").is_dir()


def test_no_harness_home_leaks_into_the_seed(tmp_path: Path) -> None:
    """The Seed Repo carries the task's files only — ``.decode/`` belongs to Harness Home (ADR-0012 §6)."""
    task_dir = write_task_dir(tmp_path / "001-synthetic")

    seed_task_repo(load_benchmark_task(task_dir), tmp_path / "seed")

    assert not (tmp_path / "seed" / ".decode").exists()


# --- the adopted branch -----------------------------------------------------------------------


def test_adopts_history_built_by_setup_sh(tmp_path: Path) -> None:
    task_dir = write_task_dir(tmp_path / "018-history", setup_script=_HISTORY_SETUP)

    info = seed_task_repo(load_benchmark_task(task_dir), tmp_path / "seed")

    dest = tmp_path / "seed"
    assert _git(dest, "rev-list", "--count", "HEAD") == "2"  # the task's own history, verbatim
    assert _git(dest, "log", "--format=%s") == "second\nfirst"
    assert _git(dest, "rev-parse", "HEAD") == info.base_sha
    assert _git(dest, "status", "--porcelain") == ""


def test_dirty_adopted_repo_is_rejected(tmp_path: Path) -> None:
    task_dir = write_task_dir(
        tmp_path / "018-history",
        setup_script=_HISTORY_SETUP + 'echo "stray" > untracked.txt\n',
    )

    with pytest.raises(SeedError, match="clean"):
        seed_task_repo(load_benchmark_task(task_dir), tmp_path / "seed")


def test_adopted_repo_on_another_branch_is_rejected(tmp_path: Path) -> None:
    task_dir = write_task_dir(
        tmp_path / "018-history",
        setup_script=_HISTORY_SETUP + "git checkout -q -b feature\n",
    )

    with pytest.raises(SeedError, match="main"):
        seed_task_repo(load_benchmark_task(task_dir), tmp_path / "seed")


def test_adopted_repo_with_a_remote_is_rejected(tmp_path: Path) -> None:
    task_dir = write_task_dir(
        tmp_path / "018-history",
        setup_script=_HISTORY_SETUP + "git remote add origin https://example.com/x.git\n",
    )

    with pytest.raises(SeedError, match="remote"):
        seed_task_repo(load_benchmark_task(task_dir), tmp_path / "seed")


def test_empty_adopted_repo_is_rejected(tmp_path: Path) -> None:
    task_dir = write_task_dir(
        tmp_path / "018-history",
        setup_script=f"#!/usr/bin/env bash\n# {CANARY_LINE}\nset -euo pipefail\ngit init -b main -q .\n",
    )

    with pytest.raises(SeedError, match="commit"):
        seed_task_repo(load_benchmark_task(task_dir), tmp_path / "seed")


# --- failure ----------------------------------------------------------------------------------


def test_failing_setup_script_raises(tmp_path: Path) -> None:
    task_dir = write_task_dir(
        tmp_path / "001-synthetic",
        setup_script='#!/usr/bin/env bash\nset -euo pipefail\necho "boom" >&2\nexit 3\n',
    )

    with pytest.raises(SeedError, match=r"setup\.sh"):
        seed_task_repo(load_benchmark_task(task_dir), tmp_path / "seed")
