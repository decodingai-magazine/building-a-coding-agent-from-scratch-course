"""Adversarial probes for the medium benchmark oracles 008-014 (task 109, "attack your own oracles").

The two-direction oracle-sanity sweep only proves each oracle PASSes on the gold ``solution/`` and
FAILs on the untouched ``setup/``. That leaves two blind spots an oracle can hide in: rejecting a
valid ALTERNATIVE-correct answer (over-fit to the author's exact solution) and accepting a plausible
WRONG answer. Each task below pins one of each, so a grader that is too strict or too loose can never
land silently. Tasks whose state is built by ``setup.sh`` (010 git history, 012 Makefile, 013
sqlite) run it here too, exactly as the runner and oracle-sanity harness do.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from evals.harness.task_loader import (
    BENCHMARK_TASKS_DIR,
    VERIFY_SCRIPT_NAME,
    load_benchmark_task,
)

SETUP_SCRIPT_NAME = "setup.sh"


def _grade(
    task_slug: str,
    tmp_path: Path,
    *,
    overlay_files: dict[str, str] | None = None,
    post_setup: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Reproduce the grade-time Workspace, then drop in an arbitrary answer and run ``verify.sh``.

    Seeds ``setup/``, runs ``setup.sh`` if present, writes ``overlay_files`` (submitted answer), runs
    an optional ``post_setup`` bash snippet (for git-state answers), injects ``verify/`` and grades.
    """
    task = load_benchmark_task(BENCHMARK_TASKS_DIR / task_slug)
    subprocess.run(["cp", "-R", f"{task.setup_dir}/.", str(tmp_path)], check=True)

    if (tmp_path / SETUP_SCRIPT_NAME).is_file():
        setup = subprocess.run(
            ["bash", SETUP_SCRIPT_NAME], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert setup.returncode == 0, f"setup.sh failed: {setup.stderr}"

    for name, content in (overlay_files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    if post_setup is not None:
        acted = subprocess.run(
            ["bash", "-c", post_setup], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert acted.returncode == 0, f"post_setup failed: {acted.stderr}"

    subprocess.run(["cp", "-R", f"{task.verify_script.parent}/.", str(tmp_path)], check=True)
    return subprocess.run(
        ["bash", VERIFY_SCRIPT_NAME],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_pass(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, f"expected PASS, got exit {result.returncode}\n{result.stdout}"
    assert "PASS" in result.stdout


def _assert_fail(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode != 0, f"expected FAIL, got exit 0\n{result.stdout}"


# --- 008-dependency-repair ------------------------------------------------------------------------


def test_008_passes_when_repaired_by_adding_a_shim_module(tmp_path: Path) -> None:
    # Alternative-correct: instead of editing main.py, add the missing ``calc`` module as a shim.
    _assert_pass(
        _grade(
            "008-dependency-repair",
            tmp_path,
            overlay_files={"calc.py": "from arithmetic import factorial\n"},
        )
    )


def test_008_fails_when_program_runs_but_prints_wrong_values(tmp_path: Path) -> None:
    # Wrong: the import is fixed so it runs, but the printed factorial is wrong.
    wrong_main = (
        "from arithmetic import factorial\n"
        "from geometry import circle_area\n\n\n"
        "def main() -> None:\n"
        '    print(f"area={circle_area(2):.2f}")\n'
        '    print("fact=999")\n\n\n'
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    _assert_fail(_grade("008-dependency-repair", tmp_path, overlay_files={"main.py": wrong_main}))


# --- 009-multi-file-rename ------------------------------------------------------------------------


def test_009_passes_on_a_correct_rename_with_cosmetic_differences(tmp_path: Path) -> None:
    # Alternative-correct: fully renamed, but reformatted (extra comment/blank lines) vs the gold.
    _assert_pass(
        _grade(
            "009-multi-file-rename",
            tmp_path,
            overlay_files={
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
                    "from billing import calculate_total\n\n\n"
                    "def test_sum_without_tax():\n"
                    "    assert calculate_total([1.0, 2.0, 3.0]) == 6.0\n\n\n"
                    "def test_sum_with_tax():\n"
                    "    assert calculate_total([100.0], 0.1) == 110.0\n"
                ),
            },
        )
    )


def test_009_fails_when_one_call_site_keeps_the_old_name(tmp_path: Path) -> None:
    # Wrong: definition + tests renamed, but cli.py still references compute_total (leftover name).
    _assert_fail(
        _grade(
            "009-multi-file-rename",
            tmp_path,
            overlay_files={
                "billing.py": (
                    "def calculate_total(prices, tax_rate=0.0):\n"
                    "    return round(sum(prices) * (1 + tax_rate), 2)\n"
                ),
                "test_billing.py": (
                    "from billing import calculate_total\n\n\n"
                    "def test_sum_without_tax():\n"
                    "    assert calculate_total([1.0, 2.0, 3.0]) == 6.0\n"
                ),
            },
        )
    )


# --- 010-git-hygiene ------------------------------------------------------------------------------


def test_010_passes_with_a_different_conventional_type_and_scope(tmp_path: Path) -> None:
    # Alternative-correct: a different (valid) Conventional-Commits type/scope, staged via `git add src`.
    _assert_pass(
        _grade(
            "010-git-hygiene",
            tmp_path,
            post_setup=(
                "git checkout -q -b add-search && "
                "git add src && "
                'git commit -q -m "fix(search): add search module"'
            ),
        )
    )


def test_010_fails_when_scratch_files_are_committed_too(tmp_path: Path) -> None:
    # Wrong: right branch and message, but `git add -A` sweeps the scratch files into the commit.
    _assert_fail(
        _grade(
            "010-git-hygiene",
            tmp_path,
            post_setup=(
                "git checkout -q -b add-search && "
                "git add -A && "
                'git commit -q -m "feat: add search feature"'
            ),
        )
    )


# --- 011-json-schema-migration --------------------------------------------------------------------


def test_011_passes_with_compact_json_and_reordered_keys(tmp_path: Path) -> None:
    # Alternative-correct: same data, but compact and with keys/records in a different order.
    compact = (
        '{"records":['
        '{"created":"2021-03-15","email":"alan@example.com","last_name":"Turing","first_name":"Alan","id":3},'
        '{"id":2,"first_name":"Grace","last_name":"Hopper","email":"grace@example.com","created":"2021-02-10"},'
        '{"id":1,"first_name":"Ada","last_name":"Lovelace","email":"ada@example.com","created":"2021-01-05"}'
        '],"version":2}'
    )
    _assert_pass(
        _grade("011-json-schema-migration", tmp_path, overlay_files={"records.json": compact})
    )


def test_011_fails_when_a_record_keeps_the_old_mail_field(tmp_path: Path) -> None:
    # Wrong: shaped as v2 but one record kept ``mail`` instead of renaming it to ``email``.
    bad = (
        '{"version": 2, "records": ['
        '{"id": 1, "first_name": "Ada", "last_name": "Lovelace", "mail": "ada@example.com", "created": "2021-01-05"},'
        '{"id": 2, "first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com", "created": "2021-02-10"},'
        '{"id": 3, "first_name": "Alan", "last_name": "Turing", "email": "alan@example.com", "created": "2021-03-15"}'
        "]}"
    )
    _assert_fail(_grade("011-json-schema-migration", tmp_path, overlay_files={"records.json": bad}))


# --- 012-makefile-doctor -------------------------------------------------------------------------


def test_012_passes_when_the_data_step_is_inlined_into_build(tmp_path: Path) -> None:
    # Alternative-correct: no ``prepare`` prerequisite — ``build`` creates its own input inline.
    inlined = 'build:\n\tprintf "payload\\n" > data.txt\n\tcat data.txt > artifact.txt\n'
    _assert_pass(_grade("012-makefile-doctor", tmp_path, overlay_files={"Makefile": inlined}))


def test_012_passes_with_a_colon_comment_between_header_and_recipe(tmp_path: Path) -> None:
    # Regression (QA round 1): a `#` comment containing a colon, sitting between a target header and
    # its tab recipe, must NOT be misparsed as a target — the correct repair still builds.
    commented = (
        "build: prepare\n"
        "# Fixed: use a tab now\n"
        "\tcat data.txt > artifact.txt\n\n"
        "prepare:\n"
        '\tprintf "payload\\n" > data.txt\n'
    )
    _assert_pass(_grade("012-makefile-doctor", tmp_path, overlay_files={"Makefile": commented}))


def test_012_fails_when_only_the_tabs_are_fixed_but_the_dep_is_missing(tmp_path: Path) -> None:
    # Wrong: recipe is now tab-indented, but ``build`` still lacks the ``prepare`` prerequisite, so
    # data.txt never exists and the build fails.
    tabs_only = (
        'build:\n\tcat data.txt > artifact.txt\n\nprepare:\n\tprintf "payload\\n" > data.txt\n'
    )
    _assert_fail(_grade("012-makefile-doctor", tmp_path, overlay_files={"Makefile": tabs_only}))


# --- 013-sqlite-analyst ---------------------------------------------------------------------------


def test_013_passes_with_a_trailing_newline_in_the_answer(tmp_path: Path) -> None:
    # Alternative-correct: correct name, but written with surrounding whitespace/newline.
    _assert_pass(_grade("013-sqlite-analyst", tmp_path, overlay_files={"answer.txt": "  Bob\n"}))


def test_013_fails_on_the_wrong_customer(tmp_path: Path) -> None:
    # Wrong: Carol is second by revenue, not the top customer.
    _assert_fail(_grade("013-sqlite-analyst", tmp_path, overlay_files={"answer.txt": "Carol\n"}))


# --- 014-cli-flag-add -----------------------------------------------------------------------------


def test_014_passes_with_pretty_sorted_json_output(tmp_path: Path) -> None:
    # Alternative-correct: JSON emitted indented + sort_keys — parsed content is identical.
    pretty_cli = (
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
    _assert_pass(_grade("014-cli-flag-add", tmp_path, overlay_files={"cli.py": pretty_cli}))


def test_014_fails_when_json_mode_omits_a_required_key(tmp_path: Path) -> None:
    # Wrong: --json works but drops the ``greeting`` key.
    missing_key_cli = (
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
    _assert_fail(_grade("014-cli-flag-add", tmp_path, overlay_files={"cli.py": missing_key_cli}))
