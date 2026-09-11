"""``run_verifier``: the host-side grade-time contract, on synthetic tasks (ADR-0022 §3; task 157).

``tests/unit/evals/benchmark/test_oracle_sanity.py`` is the GATE (every real task, both directions);
this file pins the mechanics the gate rests on, using throwaway tasks that can misbehave on purpose:
``VERIFIER_DIR`` is an existing empty dir, ``tests/`` is overlaid LAST (so the agent's own copy can
never grade itself), a missing / empty / non-numeric ``reward.txt`` is ``None`` (a verifier error,
never a zero), and a runaway Verifier is cut off at ``verifier.timeout_sec``.
"""

from __future__ import annotations

from pathlib import Path

from support.benchmark_tasks import CANARY_LINE, write_task_dir

from evals.harness.oracle_sanity import run_verifier
from evals.harness.task_loader import load_benchmark_task


def _task(tmp_path: Path, **overrides):
    return load_benchmark_task(write_task_dir(tmp_path / "001-synthetic", **overrides))


def _script(body: str) -> str:
    return f"#!/usr/bin/env bash\n# {CANARY_LINE}\nset -uo pipefail\n{body}\n"


def test_verifier_dir_is_an_existing_empty_dir(tmp_path: Path) -> None:
    """The Verifier may assume ``$VERIFIER_DIR`` exists and is empty — it never mkdirs it itself."""
    task = _task(
        tmp_path,
        test_script=_script(
            'ls -A "$VERIFIER_DIR" > listing.txt\n'
            'printf "%s\\n" "$VERIFIER_DIR" >> listing.txt\n'
            'printf "1\\n" > "$VERIFIER_DIR/reward.txt"'
        ),
    )

    result = run_verifier(task, tmp_path / "workspace", with_solution=False)

    listing = (tmp_path / "workspace" / "listing.txt").read_text(encoding="utf-8").splitlines()
    assert result.reward == 1.0
    assert listing[0] == str(tmp_path / "workspace" / ".verifier")  # empty dir, absolute path


def test_the_oracle_runs_with_the_allow_listed_host_env(tmp_path: Path, monkeypatch) -> None:
    """The gate claims the Oracle earns 1 from a bare shell — so ``solve.sh`` gets no operator env."""
    monkeypatch.setenv("SECRET_SENTINEL", "sk-sentinel-do-not-leak")
    task = _task(
        tmp_path,
        solve_script=_script('printf "%s" "${SECRET_SENTINEL:-}" > sentinel.txt'),
        test_script=_script('printf "1\\n" > "$VERIFIER_DIR/reward.txt"'),
    )

    run_verifier(task, tmp_path / "workspace", with_solution=True)

    assert (tmp_path / "workspace" / "sentinel.txt").read_text(encoding="utf-8") == ""


def test_hidden_tests_are_overlaid_last(tmp_path: Path) -> None:
    """An agent that plants its own ``tests/test.sh`` is inert: the hidden copy overwrites it."""
    task = _task(
        tmp_path,
        environment_files={"tests/test.sh": 'printf "1\\n" > "$VERIFIER_DIR/reward.txt"\n'},
        solve_script=_script("true"),
        test_script=_script('printf "0.5\\n" > "$VERIFIER_DIR/reward.txt"'),
    )

    result = run_verifier(task, tmp_path / "workspace", with_solution=True)

    assert result.reward == 0.5


def test_the_oracle_runs_from_its_own_directory(tmp_path: Path) -> None:
    """``solve.sh`` may ``cp`` its siblings — it is invoked by path, with cwd = the fresh seed."""
    task_dir = write_task_dir(
        tmp_path / "001-synthetic",
        solve_script=_script('cp "$(dirname "$0")/gold.txt" answer.txt'),
    )
    (task_dir / "solution" / "gold.txt").write_text("42\n", encoding="utf-8")

    result = run_verifier(load_benchmark_task(task_dir), tmp_path / "workspace", with_solution=True)

    assert result.reward == 1.0
    assert not (tmp_path / "workspace" / "gold.txt").exists()  # only what solve.sh copied


def test_a_missing_reward_file_is_none(tmp_path: Path) -> None:
    task = _task(tmp_path, test_script=_script('echo "I forgot to write a reward"'))

    result = run_verifier(task, tmp_path / "workspace", with_solution=False)

    assert result.reward is None
    assert "I forgot" in result.stdout


def test_an_empty_reward_file_is_none(tmp_path: Path) -> None:
    task = _task(tmp_path, test_script=_script(': > "$VERIFIER_DIR/reward.txt"'))

    assert run_verifier(task, tmp_path / "workspace", with_solution=False).reward is None


def test_a_non_numeric_reward_is_none(tmp_path: Path) -> None:
    task = _task(tmp_path, test_script=_script('printf "PASS\\n" > "$VERIFIER_DIR/reward.txt"'))

    assert run_verifier(task, tmp_path / "workspace", with_solution=False).reward is None


def test_a_runaway_verifier_is_timed_out(tmp_path: Path) -> None:
    task = _task(
        tmp_path,
        tables=None,
        test_script=_script('sleep 30\nprintf "1\\n" > "$VERIFIER_DIR/reward.txt"'),
    )
    task = task.model_copy(
        update={"verifier": task.verifier.model_copy(update={"timeout_sec": 0.5})}
    )

    result = run_verifier(task, tmp_path / "workspace", with_solution=False)

    assert result.timed_out
    assert result.reward is None


def test_stderr_is_captured(tmp_path: Path) -> None:
    task = _task(
        tmp_path,
        test_script=_script('echo "diagnostics" >&2\nprintf "0\\n" > "$VERIFIER_DIR/reward.txt"'),
    )

    result = run_verifier(task, tmp_path / "workspace", with_solution=False)

    assert result.reward == 0.0
    assert "diagnostics" in result.stderr


def test_a_stale_reward_never_grades_the_run(tmp_path: Path) -> None:
    """``VERIFIER_DIR`` is emptied before the Verifier runs, so no earlier verdict can survive.

    Without that, a Verifier that wrote nothing (an error, reward ``None``) would silently inherit
    whatever ``reward.txt`` was already lying in the workspace.
    """
    workspace = tmp_path / "workspace"
    (workspace / ".verifier").mkdir(parents=True)
    (workspace / ".verifier" / "reward.txt").write_text("1\n", encoding="utf-8")
    task = _task(tmp_path, test_script=_script('echo "no reward written"'))

    result = run_verifier(task, workspace, with_solution=False)

    assert result.reward is None
