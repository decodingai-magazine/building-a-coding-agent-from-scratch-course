"""The regression ritual's own pytest options — ``--difficulty`` slices the gate (ADR-0022 §8).

This conftest exists so ``uv run pytest evals/regression/test_thresholds.py --difficulty hard`` runs
the eight hard Regression Cases instead of all twenty runnable ones — the same tier vocabulary
``python -m evals regression --difficulty`` uses, so ``make eval-regression ARGS='--difficulty hard'``
forwards ONE flag to both halves of the ritual (the sync and the gate).

It is scoped to ``evals/regression/`` on purpose: ``testpaths`` is ``tests/unit`` + ``tests/integration``,
so plain ``pytest`` / ``make ci`` never collects this directory and never sees the option.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--difficulty`` (``None`` = the whole suite) for the threshold-gate ritual."""
    parser.addoption(
        "--difficulty",
        action="store",
        default=None,
        choices=("easy", "medium", "hard"),
        help="Run only the Regression Cases of this difficulty tier (default: all of them).",
    )
