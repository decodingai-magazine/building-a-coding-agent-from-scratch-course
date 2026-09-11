"""The suite-wide audit, machine-checked: 19 tasks, 7/6/6, tier ceilings, prompts (ADR-0022 §2; task 159).

The per-task checklist in ``evals/benchmark/tasks/README.md`` is read by a human once, when the task
lands. This file is the part of it a machine can hold forever, so the twentieth task cannot land with
a frontier-model prompt, a 90-step budget or a leftover ``judges:`` block:

* the set is exactly the nineteen tasks ADR-0022 §2 converts, tiered 7 easy / 6 medium / 6 hard;
* every budget is at or under its tier's ceiling (a task may go lower, never higher);
* every ``category`` is in Terminal-Bench's seven-value taxonomy;
* every ``instruction.md`` is ≤ 250 words and never leaks the grader's vocabulary — a prompt that
  says ``tests/`` or "reward" tells a model where to look instead of what to build;
* every test-bearing task declares the SWE-bench ``fail_to_pass`` node ids it is graded on;
* no ``task.yaml`` and no ``judges:`` survive anywhere under the tasks tree (ADR-0022 §6: a judge
  audits a verifier, it never grades of record).
"""

from __future__ import annotations

import pytest

from evals.harness.task_loader import (
    BENCHMARK_TASKS_DIR,
    BenchmarkTask,
    load_benchmark_tasks,
)

_TASKS = load_benchmark_tasks(BENCHMARK_TASKS_DIR)

# The tier table of ``evals/benchmark/tasks/README.md``: max_steps, agent timeout, verifier timeout.
_TIER_CEILINGS: dict[str, tuple[int, float, float]] = {
    "easy": (15, 600.0, 120.0),
    "medium": (25, 900.0, 120.0),
    "hard": (40, 1500.0, 180.0),
}

# ADR-0022 §2: nineteen tasks, 7 easy / 6 medium / 6 hard (010-git-hygiene is the one deletion).
_EXPECTED_TIER_COUNTS = {"easy": 7, "medium": 6, "hard": 6}

# Terminal-Bench's taxonomy, verbatim.
_CATEGORIES = {"Science", "Software", "ML", "Operations", "Security", "Hardware", "Media"}

# The instruction budget for a 35B MoE: past ~250 words the task itself starts getting lost.
_MAX_INSTRUCTION_WORDS = 250

# Grader vocabulary an instruction must never carry: it names the hidden assets, the Oracle folder,
# or the score — all of which invite the agent to aim at the grader instead of the deliverable.
_FORBIDDEN_IN_INSTRUCTIONS = ("tests/", "solution/", "reward", "verif")

# The tasks whose grade rests on a hidden test file, so their node ids are the contract (SWE-bench
# shape). 018 runs hidden tests too, but its reward is gated by git shape as well, so declaring node
# ids there would misstate the README's "reward 1 iff every declared test passes".
_TEST_BEARING_TASK_IDS = (
    "007-fix-failing-test",
    "009-multi-file-rename",
    "014-cli-flag-add",
    "016-implement-from-spec",
    "017-flaky-test-hunt",
    "019-patch-conflict-resolve",
    "020-build-small-tool",
)


def test_the_suite_is_the_nineteen_tasks_of_the_adr() -> None:
    assert len(_TASKS) == 19, f"expected 19 benchmark tasks, loaded {[task.id for task in _TASKS]}"


def test_the_tiers_are_seven_six_six() -> None:
    counts = {tier: sum(task.difficulty == tier for task in _TASKS) for tier in _TIER_CEILINGS}

    assert counts == _EXPECTED_TIER_COUNTS


@pytest.mark.parametrize("task", _TASKS, ids=lambda task: task.id)
def test_every_budget_is_at_or_under_its_tier_ceiling(task: BenchmarkTask) -> None:
    max_steps, agent_timeout, verifier_timeout = _TIER_CEILINGS[task.difficulty]

    assert task.max_steps <= max_steps, f"{task.id}: max_steps {task.max_steps} > {max_steps}"
    assert task.agent_timeout_sec <= agent_timeout, (
        f"{task.id}: agent timeout {task.agent_timeout_sec} > {agent_timeout}"
    )
    assert task.verifier_timeout_sec <= verifier_timeout, (
        f"{task.id}: verifier timeout {task.verifier_timeout_sec} > {verifier_timeout}"
    )


@pytest.mark.parametrize("task", _TASKS, ids=lambda task: task.id)
def test_every_category_is_in_the_taxonomy(task: BenchmarkTask) -> None:
    assert task.category in _CATEGORIES


@pytest.mark.parametrize("task", _TASKS, ids=lambda task: task.id)
def test_every_instruction_fits_the_word_budget(task: BenchmarkTask) -> None:
    words = len(task.instruction.split())

    assert words <= _MAX_INSTRUCTION_WORDS, f"{task.id}: instruction is {words} words"


@pytest.mark.parametrize("task", _TASKS, ids=lambda task: task.id)
def test_no_instruction_leaks_the_graders_vocabulary(task: BenchmarkTask) -> None:
    lowered = task.instruction.lower()

    leaked = [word for word in _FORBIDDEN_IN_INSTRUCTIONS if word in lowered]

    assert not leaked, f"{task.id}: instruction.md mentions {leaked}"


@pytest.mark.parametrize("task_id", _TEST_BEARING_TASK_IDS)
def test_every_test_bearing_task_declares_its_fail_to_pass_ids(task_id: str) -> None:
    task = next(candidate for candidate in _TASKS if candidate.id == task_id)

    assert task.fail_to_pass, f"{task_id} is graded on hidden tests: it must declare fail_to_pass"


def test_no_task_yaml_or_judge_survives_in_the_suite() -> None:
    """The v1 layout and its per-task G-Eval judges are gone, tree-wide (ADR-0022 §6)."""
    leftovers = sorted(str(path) for path in BENCHMARK_TASKS_DIR.rglob("task.yaml"))
    judged = sorted(
        str(path)
        for path in BENCHMARK_TASKS_DIR.rglob("*")
        if path.is_file()
        and path.suffix in {".toml", ".yaml", ".yml"}
        and "judges" in path.read_text(encoding="utf-8")
    )

    assert not leftovers, f"legacy task.yaml files remain: {leftovers}"
    assert not judged, f"per-task judges remain: {judged}"


def test_every_task_folder_the_tree_holds_is_a_loaded_task() -> None:
    """A half-converted folder (no ``task.toml``) would be skipped by the loader — and by the audit."""
    folders = sorted(
        path.name
        for path in BENCHMARK_TASKS_DIR.iterdir()
        if path.is_dir() and not path.name.startswith((".", "__"))
    )

    assert folders == [task.id for task in _TASKS], (
        f"folders under {BENCHMARK_TASKS_DIR} that the loader does not see: "
        f"{sorted(set(folders) - {task.id for task in _TASKS})}"
    )


def test_the_audit_table_in_the_readme_covers_every_task() -> None:
    """The README's audit table is the human-readable half of this file; it must not go stale."""
    readme = (BENCHMARK_TASKS_DIR / "README.md").read_text(encoding="utf-8")

    missing = [task.id for task in _TASKS if f"`{task.id}`" not in readme]

    assert not missing, f"the audit table has no row for: {missing}"
