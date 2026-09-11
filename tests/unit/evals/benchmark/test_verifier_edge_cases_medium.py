"""Adversarial probes for the MEDIUM Verifiers 008-014 (ADR-0022 §2,§3; task 158).

Same contract as the easy suite: for each task one ALTERNATIVE-correct answer that must earn ``1``
(so a grader over-fit to the author's own solution cannot land) and one plausible WRONG answer that
must earn ``0`` (so a grader too loose cannot either). Asserted on the reward, never on an exit code.

Ported from the v1 ``test_oracle_edge_cases_medium.py`` (deleted with the v1 format in task 157) —
every case except 010's two, which died with ``010-git-hygiene`` (ADR-0022 §3: a premise resting on
uncommitted files and a branch NAME is unobservable through the Hand-back):

* ``test_008_passes_when_repaired_by_adding_a_shim_module`` -> :func:`test_008_accepts_a_repair_that_adds_the_missing_module`
* ``test_008_fails_when_program_runs_but_prints_wrong_values`` -> :func:`test_008_rejects_a_program_that_runs_but_prints_wrong_values`
* ``test_009_passes_on_a_correct_rename_with_cosmetic_differences`` -> :func:`test_009_accepts_a_correct_rename_with_cosmetic_differences`
* ``test_009_fails_when_one_call_site_keeps_the_old_name`` -> :func:`test_009_rejects_a_rename_that_leaves_one_call_site_behind`
* ``test_010_passes_with_a_different_conventional_type_and_scope`` -> DROPPED with 010
* ``test_010_fails_when_scratch_files_are_committed_too`` -> DROPPED with 010
* ``test_011_passes_with_compact_json_and_reordered_keys`` -> :func:`test_011_accepts_compact_json_with_reordered_keys`
* ``test_011_fails_when_a_record_keeps_the_old_mail_field`` -> :func:`test_011_rejects_a_record_that_keeps_the_old_mail_field`
* ``test_012_passes_when_the_data_step_is_inlined_into_build`` -> :func:`test_012_accepts_a_build_that_inlines_the_data_step`
* ``test_012_passes_with_a_colon_comment_between_header_and_recipe`` -> :func:`test_012_accepts_a_colon_comment_between_header_and_recipe`
* ``test_012_fails_when_only_the_tabs_are_fixed_but_the_dep_is_missing`` -> :func:`test_012_rejects_a_makefile_with_tabs_fixed_but_the_prerequisite_missing`
* ``test_013_passes_with_a_trailing_newline_in_the_answer`` -> :func:`test_013_accepts_an_answer_padded_with_whitespace`
* ``test_013_fails_on_the_wrong_customer`` -> :func:`test_013_rejects_the_runner_up_customer`
* ``test_014_passes_with_pretty_sorted_json_output`` -> :func:`test_014_accepts_pretty_sorted_json_output`
* ``test_014_fails_when_json_mode_omits_a_required_key`` -> :func:`test_014_rejects_json_mode_that_omits_a_required_key`

Plus :func:`test_the_swe_bench_tasks_run_exactly_the_node_ids_they_declare`, so 009's and 014's
``[verifier.tests]`` cannot drift away from what ``tests/test.sh`` runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.task_loader import BENCHMARK_TASKS_DIR, load_benchmark_task

from .conftest import AssertReward, GradeWorkspace

# --- 008-dependency-repair ------------------------------------------------------------------------

# Alternative-correct: instead of editing main.py, supply the missing ``calc`` module as a shim.
_CALC_SHIM = "from arithmetic import factorial\n"

# Wrong: the import is repaired so the program runs, but the printed factorial is made up.
_MAIN_WITH_WRONG_VALUES = (
    "from arithmetic import factorial\n"
    "from geometry import circle_area\n\n\n"
    "def main() -> None:\n"
    '    print(f"area={circle_area(2):.2f}")\n'
    '    print("fact=999")\n\n\n'
    'if __name__ == "__main__":\n'
    "    main()\n"
)

# --- 009-multi-file-rename ------------------------------------------------------------------------

# Alternative-correct: fully renamed, but reformatted (extra comments, different quoting) vs the gold.
_RENAMED_WITH_COSMETIC_DIFFERENCES = {
    "billing.py": (
        "# billing math\n\n\n"
        "def calculate_total(prices, tax_rate=0.0):\n"
        "    subtotal = sum(prices)\n"
        "    return round(subtotal * (1 + tax_rate), 2)\n"
    ),
    "report.py": (
        "from billing import calculate_total\n\n\n"
        "def format_receipt(prices, tax_rate=0.0):\n"
        "    return f'Total: {calculate_total(prices, tax_rate)}'\n"
    ),
    "cli.py": (
        "from billing import calculate_total\n\n\n"
        "def run():\n"
        "    return calculate_total([4.0, 5.5, 0.5])\n"
    ),
    "test_billing.py": (
        "import unittest\n\n"
        "from billing import calculate_total\n\n\n"
        "class TestBilling(unittest.TestCase):\n"
        "    def test_sum_without_tax(self):\n"
        "        self.assertEqual(calculate_total([1.0, 2.0, 3.0]), 6.0)\n"
    ),
}

# Wrong: definition and tests renamed, but cli.py still references the old name.
_RENAME_MISSING_ONE_CALL_SITE = {
    "billing.py": (
        "def calculate_total(prices, tax_rate=0.0):\n"
        "    return round(sum(prices) * (1 + tax_rate), 2)\n"
    ),
    "test_billing.py": (
        "import unittest\n\n"
        "from billing import calculate_total\n\n\n"
        "class TestBilling(unittest.TestCase):\n"
        "    def test_sum_without_tax(self):\n"
        "        self.assertEqual(calculate_total([1.0, 2.0, 3.0]), 6.0)\n"
    ),
}

# --- 011-json-schema-migration --------------------------------------------------------------------

# Alternative-correct: same data, compact, with keys and records in a different order.
_COMPACT_REORDERED_V2 = (
    '{"records":['
    '{"created":"2021-03-15","email":"alan@example.com","last_name":"Turing",'
    '"first_name":"Alan","id":3},'
    '{"id":2,"first_name":"Grace","last_name":"Hopper","email":"grace@example.com",'
    '"created":"2021-02-10"},'
    '{"id":1,"first_name":"Ada","last_name":"Lovelace","email":"ada@example.com",'
    '"created":"2021-01-05"}'
    '],"version":2}'
)

# Wrong: shaped as v2, but one record kept ``mail`` instead of renaming it to ``email``.
_V2_KEEPING_ONE_MAIL_FIELD = (
    '{"version": 2, "records": ['
    '{"id": 1, "first_name": "Ada", "last_name": "Lovelace", "mail": "ada@example.com",'
    ' "created": "2021-01-05"},'
    '{"id": 2, "first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com",'
    ' "created": "2021-02-10"},'
    '{"id": 3, "first_name": "Alan", "last_name": "Turing", "email": "alan@example.com",'
    ' "created": "2021-03-15"}'
    "]}"
)

# --- 012-makefile-doctor --------------------------------------------------------------------------

# Alternative-correct: no ``prepare`` prerequisite — ``build`` creates its own input inline.
_MAKEFILE_INLINED = 'build:\n\tprintf "payload\\n" > data.txt\n\tcat data.txt > artifact.txt\n'

# Regression: a `#` comment CONTAINING A COLON, between a target header and its tab recipe, must not
# be misparsed as a target that swallows the recipe — the repair below is correct and must earn 1.
_MAKEFILE_WITH_COLON_COMMENT = (
    "build: prepare\n"
    "# Fixed: use a tab now\n"
    "\tcat data.txt > artifact.txt\n\n"
    "prepare:\n"
    '\tprintf "payload\\n" > data.txt\n'
)

# Wrong: the recipe is tab-indented now, but ``build`` still lacks its ``prepare`` prerequisite, so
# data.txt never exists on a clean checkout.
_MAKEFILE_TABS_ONLY = (
    'build:\n\tcat data.txt > artifact.txt\n\nprepare:\n\tprintf "payload\\n" > data.txt\n'
)

# --- 014-cli-flag-add -----------------------------------------------------------------------------

# Alternative-correct: JSON emitted indented + sort_keys — the parsed content is identical.
_CLI_PRETTY_JSON = (
    "import argparse\n"
    "import json\n\n\n"
    "def main() -> None:\n"
    "    parser = argparse.ArgumentParser()\n"
    '    parser.add_argument("name")\n'
    '    parser.add_argument("--times", type=int, default=1)\n'
    '    parser.add_argument("--json", action="store_true")\n'
    "    args = parser.parse_args()\n"
    '    greeting = f"Hello, {args.name}!"\n'
    "    if args.json:\n"
    '        print(json.dumps({"greeting": greeting, "name": args.name, "times": args.times}, '
    "indent=2, sort_keys=True))\n"
    "        return\n"
    "    for _ in range(args.times):\n"
    "        print(greeting)\n\n\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
)

# Wrong: --json works but drops the ``greeting`` key.
_CLI_JSON_MISSING_KEY = (
    "import argparse\n"
    "import json\n\n\n"
    "def main() -> None:\n"
    "    parser = argparse.ArgumentParser()\n"
    '    parser.add_argument("name")\n'
    '    parser.add_argument("--times", type=int, default=1)\n'
    '    parser.add_argument("--json", action="store_true")\n'
    "    args = parser.parse_args()\n"
    "    if args.json:\n"
    '        print(json.dumps({"name": args.name, "times": args.times}))\n'
    "        return\n"
    "    for _ in range(args.times):\n"
    '        print(f"Hello, {args.name}!")\n\n\n'
    'if __name__ == "__main__":\n'
    "    main()\n"
)


def test_008_accepts_a_repair_that_adds_the_missing_module(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("008-dependency-repair", files={"calc.py": _CALC_SHIM})

    assert_reward(result, 1.0)


def test_008_rejects_a_program_that_runs_but_prints_wrong_values(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("008-dependency-repair", files={"main.py": _MAIN_WITH_WRONG_VALUES})

    assert_reward(result, 0.0)


def test_009_accepts_a_correct_rename_with_cosmetic_differences(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("009-multi-file-rename", files=_RENAMED_WITH_COSMETIC_DIFFERENCES)

    assert_reward(result, 1.0)


def test_009_rejects_a_rename_that_leaves_one_call_site_behind(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("009-multi-file-rename", files=_RENAME_MISSING_ONE_CALL_SITE)

    assert_reward(result, 0.0)


def test_011_accepts_compact_json_with_reordered_keys(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "011-json-schema-migration", files={"records.json": _COMPACT_REORDERED_V2}
    )

    assert_reward(result, 1.0)


def test_011_rejects_a_record_that_keeps_the_old_mail_field(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "011-json-schema-migration", files={"records.json": _V2_KEEPING_ONE_MAIL_FIELD}
    )

    assert_reward(result, 0.0)


def test_012_accepts_a_build_that_inlines_the_data_step(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("012-makefile-doctor", files={"Makefile": _MAKEFILE_INLINED})

    assert_reward(result, 1.0)


def test_012_accepts_a_colon_comment_between_header_and_recipe(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "012-makefile-doctor", files={"Makefile": _MAKEFILE_WITH_COLON_COMMENT}
    )

    assert_reward(result, 1.0)


def test_012_rejects_a_makefile_with_tabs_fixed_but_the_prerequisite_missing(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("012-makefile-doctor", files={"Makefile": _MAKEFILE_TABS_ONLY})

    assert_reward(result, 0.0)


def test_013_accepts_an_answer_padded_with_whitespace(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("013-sqlite-analyst", files={"answer.txt": "  Bob\n"})

    assert_reward(result, 1.0)


def test_013_rejects_the_runner_up_customer(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("013-sqlite-analyst", files={"answer.txt": "Carol\n"})

    assert_reward(result, 0.0)


def test_014_accepts_pretty_sorted_json_output(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("014-cli-flag-add", files={"cli.py": _CLI_PRETTY_JSON})

    assert_reward(result, 1.0)


def test_014_rejects_json_mode_that_omits_a_required_key(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("014-cli-flag-add", files={"cli.py": _CLI_JSON_MISSING_KEY})

    assert_reward(result, 0.0)


@pytest.mark.parametrize("task_id", ["009-multi-file-rename", "014-cli-flag-add"])
def test_the_swe_bench_tasks_run_exactly_the_node_ids_they_declare(task_id: str) -> None:
    """``[verifier.tests]`` is the contract; ``tests/test.sh`` is what runs. They must not drift."""
    task = load_benchmark_task(BENCHMARK_TASKS_DIR / task_id)

    script = Path(task.verifier_script).read_text(encoding="utf-8")

    assert task.fail_to_pass, f"{task_id} is the SWE-bench shape: it must declare a fail_to_pass id"
    for node_id in task.fail_to_pass + task.pass_to_pass:
        assert node_id in script, f"{node_id} is declared in task.toml but never run by test.sh"
