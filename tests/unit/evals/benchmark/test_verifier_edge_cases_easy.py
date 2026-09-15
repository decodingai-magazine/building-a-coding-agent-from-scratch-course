"""Adversarial probes for the EASY Verifiers 001-007 (ADR-0022 §2,§3; task 158).

The oracle gate only proves each Verifier says ``1`` to the gold ``solution/`` and ``0`` to silence.
That leaves two blind spots a grader can hide in: rejecting an ALTERNATIVE-correct answer (over-fit
to the author's exact solution) and accepting a plausible WRONG one. Each case here pins one of them
against the v2 Verifier, on the reward — not on an exit code, which v2 makes informational.

Ported from the v1 ``test_oracle_edge_cases.py`` (deleted with the v1 format in task 157):

* ``test_002_passes_without_a_trailing_newline`` -> :func:`test_002_accepts_an_answer_with_no_trailing_newline`
* ``test_006_fails_on_duplicate_ips`` -> :func:`test_006_rejects_a_script_that_lists_an_offender_twice`

Plus two cases the v1 suite had no way to state:

* :func:`test_005_commits_files_that_are_not_valid_utf8` — 005's premise is invisible to the gate
  (the Oracle overwrites both files and the nop direction earns ``0`` either way), so if seeding ever
  mangled the legacy bytes the benchmark would silently grade a task that no longer exists;
* :func:`test_007_runs_exactly_the_node_ids_it_declares` — without it ``[verifier.tests]`` is
  decorative and can drift from what ``tests/test.sh`` actually runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from evals.harness.task_loader import BENCHMARK_TASKS_DIR, load_benchmark_task

from .conftest import AssertReward, GradeWorkspace, SeedWorkspace

# A content-correct answer for 002 built as "\n".join(sorted(emails)) — no final newline.
_EMAILS_WITHOUT_TRAILING_NEWLINE = (
    "alice@example.com\nbilling@corp.net\nbob@work.io\ncarol@example.com\ndave@example.com"
)

# A 006 script whose SET of offenders is right but that lists 10.0.0.1 twice.
_DUPLICATE_IP_SCRIPT = 'import json\nprint(json.dumps(["10.0.0.1", "10.0.0.1", "10.0.0.3"]))\n'


def test_002_accepts_an_answer_with_no_trailing_newline(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "002-regex-extraction", files={"emails.txt": _EMAILS_WITHOUT_TRAILING_NEWLINE}
    )

    assert_reward(result, 1.0)


def test_006_rejects_a_script_that_lists_an_offender_twice(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("006-log-forensics", files={"ban_ips.py": _DUPLICATE_IP_SCRIPT})

    assert_reward(result, 0.0)
    assert "duplicate" in result.stdout.lower()


def test_005_commits_files_that_are_not_valid_utf8(seed_workspace: SeedWorkspace) -> None:
    """The premise the two-direction gate structurally cannot see: the seed really is legacy bytes.

    Read out of the OBJECT STORE, not off the working tree: a trial hands the agent a ``git clone``
    of the Seed Repo, so the committed blob is what it starts from. A checkout filter that
    "helpfully" normalised those bytes would leave the gate green while the task quietly evaporated.
    """
    workspace = seed_workspace("005-encoding-normalize")

    for name in ("cafe.txt", "zurich.txt"):
        blob = subprocess.run(
            ["git", "-C", str(workspace), "show", f"HEAD:{name}"],
            capture_output=True,
            check=True,
        ).stdout
        try:
            blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        raise AssertionError(
            f"{name} was committed as valid UTF-8 — the task has no work left in it"
        )


def test_007_runs_exactly_the_node_ids_it_declares() -> None:
    """``[verifier.tests]`` is the contract; ``tests/test.sh`` is what runs. They must not drift."""
    task = load_benchmark_task(BENCHMARK_TASKS_DIR / "007-fix-failing-test")

    script = Path(task.verifier_script).read_text(encoding="utf-8")

    assert task.fail_to_pass, "007 is the SWE-bench shape: it must declare a fail_to_pass node id"
    for node_id in task.fail_to_pass + task.pass_to_pass:
        assert node_id in script, f"{node_id} is declared in task.toml but never run by test.sh"
