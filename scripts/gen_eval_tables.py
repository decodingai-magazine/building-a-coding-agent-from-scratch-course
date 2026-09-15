"""Regenerate the what-it-tests tables in the two eval READMEs, from the loaders (ADR-0022 §2,8).

    uv run python scripts/gen_eval_tables.py            # rewrite both tables in place
    uv run python scripts/gen_eval_tables.py --check    # print a diff-less verdict, change nothing

Both eval tracks describe every entry in one sentence — a Regression Case in its ``description``
field, a Benchmark Task in its ``task.toml`` ``[task] description`` — and both READMEs list those
sentences so a reader can see the whole suite without opening twenty-four modules. Hand-maintained,
those tables rot the first time a case is added. So they are GENERATED here from the same loaders the
harness runs on, written between HTML markers, and guarded by
``tests/unit/evals/regression/test_case.py`` / ``tests/unit/evals/benchmark/test_suite_shape.py``,
which fail ``make ci`` when a loaded description is missing from its README.

An operator script, not library code: it prints with ``click.echo`` and edits only the block between
its markers, so everything a human wrote around the table survives a regeneration.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

# ``python scripts/gen_eval_tables.py`` puts only ``scripts/`` on ``sys.path``; this script reads the
# real loaders, so the repo root goes on it first (``evals`` is a repo-root package, not installed).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.harness.task_loader import (  # noqa: E402  (after the sys.path bootstrap above)
    BENCHMARK_TASKS_DIR,
    load_benchmark_tasks,
)
from evals.regression.loader import load_cases  # noqa: E402

REGRESSION_README = REPO_ROOT / "evals" / "regression" / "README.md"
BENCHMARK_README = BENCHMARK_TASKS_DIR / "README.md"

# The table lives between these two lines; everything outside them is hand-written prose.
BEGIN_MARKER = "<!-- BEGIN GENERATED TABLE — scripts/gen_eval_tables.py -->"
END_MARKER = "<!-- END GENERATED TABLE -->"


def regression_table() -> str:
    """The markdown table of every declared case: id, tier, one-sentence description.

    Every case the loader sees, INCLUDING the skipped ones — a case that never runs is still part of
    what the suite declares, and a reader looking for it must find it here.
    """
    rows = [
        f"| `{case.id}` | {case.difficulty} | {case.description}"
        f"{' *(skipped)*' if case.skip_reason else ''} |"
        for case in load_cases()
    ]
    return "\n".join(["| Case | Tier | What it tests |", "|---|---|---|", *rows])


def benchmark_table() -> str:
    """The markdown table of every task: id, tier, category, its ``task.toml`` description."""
    rows = [
        f"| `{task.id}` | {task.difficulty} | {task.category} | {task.task.description} |"
        for task in load_benchmark_tasks(BENCHMARK_TASKS_DIR)
    ]
    return "\n".join(["| Task | Tier | Category | What it tests |", "|---|---|---|---|", *rows])


def replace_block(readme: str, table: str) -> str:
    """Swap the text between the markers for ``table``, leaving every other line untouched.

    A README missing either marker is an error, not a silent append: the table's PLACE is a human's
    choice, and guessing one would drop it at the bottom of a file it belongs in the middle of.
    """
    begin = readme.find(BEGIN_MARKER)
    end = readme.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        raise click.ClickException(
            f"the README has no {BEGIN_MARKER} … {END_MARKER} block to regenerate"
        )
    return readme[: begin + len(BEGIN_MARKER)] + f"\n\n{table}\n\n" + readme[end:]


def render(path: Path, table: str) -> str:
    """The README's full text with its generated block refreshed (the file is not written here)."""
    return replace_block(path.read_text(encoding="utf-8"), table)


@click.command()
@click.option("--check", is_flag=True, help="Fail if a table is stale instead of rewriting it.")
def main(check: bool) -> None:
    """Rewrite (or verify) the generated table in both eval READMEs."""
    stale: list[Path] = []
    for path, table in (
        (REGRESSION_README, regression_table()),
        (BENCHMARK_README, benchmark_table()),
    ):
        current = path.read_text(encoding="utf-8")
        regenerated = render(path, table)
        if current == regenerated:
            click.echo(f"unchanged: {path.relative_to(REPO_ROOT)}")
            continue
        stale.append(path)
        if check:
            click.echo(f"STALE: {path.relative_to(REPO_ROOT)}")
        else:
            path.write_text(regenerated, encoding="utf-8")
            click.echo(f"rewrote: {path.relative_to(REPO_ROOT)}")

    if check and stale:
        raise click.ClickException("run `uv run python scripts/gen_eval_tables.py` and commit")


if __name__ == "__main__":
    main()
