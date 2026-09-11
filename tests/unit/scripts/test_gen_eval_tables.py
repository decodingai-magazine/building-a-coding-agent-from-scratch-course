"""The operator script that regenerates the eval READMEs' what-it-tests tables (ADR-0022 §2,8).

Hermetic and offline: the rows come from the REAL loaders (so a shipped case or task that lost its
description fails here too), and the block surgery is exercised on strings, never on the READMEs —
whether the shipped files are current is the two drift guards' job
(``tests/unit/evals/regression/test_case.py``, ``tests/unit/evals/benchmark/test_suite_shape.py``).

The property that matters for a generated doc block: everything a human wrote AROUND the markers
survives a regeneration, and a README with no markers is a loud failure rather than a table appended
wherever the script happened to land.
"""

from __future__ import annotations

import pytest
from click import ClickException

from scripts.gen_eval_tables import (
    BEGIN_MARKER,
    END_MARKER,
    benchmark_table,
    regression_table,
    replace_block,
)

_README = f"""# Title

Hand-written prose above.

{BEGIN_MARKER}

| Case | Tier | What it tests |
|---|---|---|
| `stale` | easy | Tests that this row is out of date. |

{END_MARKER}

Hand-written prose below.
"""


def test_replace_block_swaps_the_table_and_keeps_the_prose_around_it() -> None:
    regenerated = replace_block(_README, "| Case |\n|---|\n| `fresh` |")

    assert "Hand-written prose above." in regenerated
    assert "Hand-written prose below." in regenerated
    assert "`fresh`" in regenerated
    assert "`stale`" not in regenerated


def test_replace_block_is_idempotent() -> None:
    """Re-running the generator with unchanged input must leave the file byte-identical."""
    table = "| Case |\n|---|\n| `fresh` |"

    once = replace_block(_README, table)

    assert replace_block(once, table) == once


def test_a_readme_without_markers_is_a_loud_failure() -> None:
    """The table's PLACE is a human's choice — guessing one would append it to the wrong section."""
    with pytest.raises(ClickException, match="no <!-- BEGIN GENERATED TABLE"):
        replace_block("# Title\n\nNo markers here.\n", "| Case |\n|---|\n")


def test_the_regression_table_carries_every_case_including_the_skipped_ones() -> None:
    """A case that never runs is still declared — a reader looking for it must find its row."""
    from evals.regression.loader import load_cases

    table = regression_table()

    for case in load_cases():
        assert f"| `{case.id}` |" in table
        assert case.description in table
    assert table.count("*(skipped)*") == sum(1 for case in load_cases() if case.skip_reason)


def test_the_benchmark_table_carries_every_task_description() -> None:
    from evals.harness.task_loader import BENCHMARK_TASKS_DIR, load_benchmark_tasks

    table = benchmark_table()

    for task in load_benchmark_tasks(BENCHMARK_TASKS_DIR):
        assert f"| `{task.id}` |" in table
        assert task.task.description in table
