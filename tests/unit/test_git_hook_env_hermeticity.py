"""Regression tests for the git-hook environment scrub (task 111).

The bug: git exports its repository location (``GIT_DIR`` & friends) into every hook it runs, our
pre-push hook runs ``pytest``, so every test that shells out to ``git`` inherited those variables and
operated against the **repository being pushed** instead of its own ``tmp_path`` repo. The fix is the
autouse ``_scrub_git_hook_env`` fixture in ``tests/conftest.py``.

Pinning it takes a nested pytest run, and that is the point: a plain in-process assertion that
``GIT_DIR`` is absent passes *trivially* when a developer runs the suite from a clean shell — it would
not fail if the fixture were deleted, so it would not be a regression test. So
:func:`test_a_polluted_git_hook_environment_does_not_break_a_git_test` re-creates the hook environment
in a subprocess and re-runs the canary below through the real ``tests/conftest.py``; delete the
fixture and it goes red.

The pollution points at a **throwaway victim repo** under ``tmp_path``, never at this checkout: an
unscrubbed ``GIT_INDEX_FILE`` is exactly how a real worktree ended up with 252 files staged as
deleted, and a regression test must not be able to do that to the developer running it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from support.git_env import GIT_HOOK_ENV_VARS

REPO_ROOT = Path(__file__).resolve().parents[2]

CANARY = f"{Path(__file__).name}::test_git_commands_target_their_own_repo"

# What git actually exports to a hook (absolute paths, as in a worktree). A subset of
# GIT_HOOK_ENV_VARS on purpose — this is the leak in the wild, not a synthetic worst case.
HOOK_EXPORTS = ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX")


def _git(*args: str, cwd: Path) -> str:
    """Run git with an explicit identity (CI images have none) and return stdout."""
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=path)
    (path / "seed.txt").write_text("seed\n")
    _git("add", "seed.txt", cwd=path)
    _git("commit", "-q", "-m", "seed", cwd=path)
    return path


def test_git_hook_vars_are_scrubbed_from_the_environment() -> None:
    """The autouse fixture removes every listed variable before the test body runs."""
    leaked = [name for name in GIT_HOOK_ENV_VARS if name in os.environ]

    assert leaked == []


def test_a_test_may_still_set_a_git_var_itself() -> None:
    """The scrub must not stop a test from pointing git somewhere on purpose.

    ``monkeypatch.setenv`` in a test body runs *after* every autouse fixture, so a deliberate value
    wins over the scrub. Asserted here with the plain fixture (function-scoped, so it is undone) so
    the ordering claim is checked, not assumed.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_DIR", "/deliberate/somewhere.git")

        assert os.environ["GIT_DIR"] == "/deliberate/somewhere.git"

    assert "GIT_DIR" not in os.environ


def test_git_commands_target_their_own_repo(tmp_path: Path) -> None:
    """CANARY — a git-invoking test operates on its own repo, whatever the ambient environment.

    Re-run in a polluted subprocess by
    :func:`test_a_polluted_git_hook_environment_does_not_break_a_git_test`; that is where it earns its
    keep. Standalone it is a cheap sanity check of the same property.
    """
    repo = _init_repo(tmp_path / "own")
    (repo / "canary.txt").write_text("canary\n")

    _git("add", "canary.txt", cwd=repo)
    _git("commit", "-q", "-m", "canary", cwd=repo)

    assert Path(_git("rev-parse", "--absolute-git-dir", cwd=repo)) == repo / ".git"
    assert _git("show", "--name-only", "--format=", "HEAD", cwd=repo) == "canary.txt"
    assert [name for name in GIT_HOOK_ENV_VARS if name in os.environ] == []


def test_a_polluted_git_hook_environment_does_not_break_a_git_test(tmp_path: Path) -> None:
    """REGRESSION — the canary passes under the environment git hands its hooks.

    Fails (7 failures in ``test_workspace.py`` was the original blast radius) if the autouse scrub in
    ``tests/conftest.py`` is removed.
    """
    victim = _init_repo(tmp_path / "victim")
    victim_head = _git("rev-parse", "HEAD", cwd=victim)
    hook_env = {
        "GIT_DIR": str(victim / ".git"),
        "GIT_COMMON_DIR": str(victim / ".git"),
        "GIT_WORK_TREE": str(victim),
        "GIT_INDEX_FILE": str(victim / ".git" / "index"),
        "GIT_PREFIX": "",
    }
    assert set(hook_env) == set(HOOK_EXPORTS) <= set(GIT_HOOK_ENV_VARS)

    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            CANARY,
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT / "tests" / "unit",
        env={**os.environ, **hook_env},
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, (
        f"canary failed under a git hook environment:\n{run.stdout}\n{run.stderr}"
    )
    # Nothing bled into the repo the pollution pointed at — no stray commit, no staged deletions.
    assert _git("rev-parse", "HEAD", cwd=victim) == victim_head
    assert _git("status", "--porcelain", cwd=victim) == ""
