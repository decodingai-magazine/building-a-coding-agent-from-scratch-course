"""Discover the regression cases declared under :mod:`evals.regression.cases` (ADR-0022 §8).

The case registry is "flat and readable": each case lives in its own module under
``evals/regression/cases/`` exposing a module-level ``CASE`` (a single
:class:`~evals.regression.case.RegressionCase`) or ``CASES`` (a list). :func:`load_cases` imports
each case module by name, collects them, and validates the set — every id non-blank and unique — so a
duplicate or empty id fails the whole scan loudly rather than shadowing a case silently. Dropping a
new ``cases/<name>.py`` is all it takes to register a case; no central list to keep in sync (the same
auto-discovery philosophy as the benchmark's folder scan). A MINED case is the same drop, named
``cases/mined_<slug>.py``.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

from evals.regression.case import RegressionCase

logger = logging.getLogger(__name__)

# The case modules live here (``evals/regression/cases/``). ``parent`` is ``evals/regression/``.
CASES_DIR = Path(__file__).resolve().parent / "cases"

# The import package the case modules are imported under.
CASES_PACKAGE = "evals.regression.cases"


class RegressionCaseError(Exception):
    """A case module or the assembled case set violates the contract (bad ``CASE``, dup id, …)."""


def load_cases(
    cases_dir: Path = CASES_DIR, *, package: str = CASES_PACKAGE
) -> list[RegressionCase]:
    """Import every case module under ``cases_dir`` and return the validated case list (§8).

    Scans ``cases_dir`` for ``*.py`` modules (skipping ``__init__`` and dunder files), imports each as
    ``<package>.<stem>``, and collects its ``CASE`` / ``CASES``. A module with neither, a ``CASE``
    that is not a :class:`RegressionCase`, and a module that fails to import at all (its
    ``RegressionCase(...)`` raises, or a dependency is missing) each raise
    :class:`RegressionCaseError` naming the module's path. The result is sorted
    by id and checked for uniqueness (a duplicate id is a loud failure). A missing ``cases_dir``
    yields an empty list.
    """
    if not cases_dir.is_dir():
        return []

    cases: list[RegressionCase] = []
    for path in sorted(cases_dir.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        cases.extend(_cases_from_module(f"{package}.{path.stem}", path))

    _reject_duplicate_ids(cases)
    return sorted(cases, key=lambda case: case.id)


def case_by_id(case_id: str, cases: list[RegressionCase] | None = None) -> RegressionCase:
    """Return the case with ``case_id`` from ``cases`` (defaults to :func:`load_cases`).

    Raises :class:`RegressionCaseError` naming ``case_id`` when no case matches — a friendly stop for
    a mistyped ``--case`` id.
    """
    catalog = cases if cases is not None else load_cases()
    for case in catalog:
        if case.id == case_id:
            return case
    known = ", ".join(sorted(case.id for case in catalog)) or "<none>"
    raise RegressionCaseError(f"no regression case with id {case_id!r}; known cases: {known}")


def select_cases(
    cases: list[RegressionCase], *, case_id: str | None = None, difficulty: str | None = None
) -> list[RegressionCase]:
    """Filter ``cases`` by exact ``case_id`` and/or ``difficulty`` — both optional, AND-combined.

    The ONE selection rule both regression surfaces share (the metric gate and the Test Suite), so
    ``--case`` / ``--difficulty`` mean exactly the same thing on ``regression`` and on ``suite``.
    ``None`` on both selects the whole suite; no match is an empty list the caller turns into a loud,
    friendly stop.
    """
    selected = cases
    if case_id is not None:
        selected = [case for case in selected if case.id == case_id]
    if difficulty is not None:
        selected = [case for case in selected if case.difficulty == difficulty]
    return selected


def runnable_cases(cases: list[RegressionCase]) -> list[RegressionCase]:
    """Drop the skip-guarded cases from a run or a sync, logging each reason (ADR-0017 §10).

    A ``skip_reason`` case is DECLARED (discoverable via :func:`load_cases`) but not yet runnable —
    decode's MCP tool factory has not shipped, so case 12 would grade a behavior the agent cannot
    perform. It is excluded here rather than at load time so the registry still lists it and it
    activates the moment its ``skip_reason`` is removed. Both Opik surfaces carry what actually runs;
    the registry is what keeps the declared-but-blocked case visible.
    """
    runnable: list[RegressionCase] = []
    for case in cases:
        if case.skip_reason is not None:
            logger.info("[eval] skipping regression case %s: %s", case.id, case.skip_reason)
            continue
        runnable.append(case)
    return runnable


def _cases_from_module(module_name: str, path: Path) -> list[RegressionCase]:
    """Import one case module and pull its ``CASE`` / ``CASES`` — each a :class:`RegressionCase`.

    A case module builds its ``CASE`` at import time, so a typo in the declaration (an unknown
    ``difficulty``, a blank ``symptom`` / ``assertion``, an omitted required field) raises inside the
    import — as does a genuinely missing dependency. Bare, those leak a ``ValueError`` / ``TypeError``
    / ``ImportError`` naming at most the case's own id, which tells a reader nothing about WHICH of
    the registry's modules to open. One wrapper turns every one of them into the same
    :class:`RegressionCaseError` naming ``path`` that a structurally bad module already gets, with the
    original exception chained for the detail. Only the import is wrapped: the extraction step below
    raises its own :class:`RegressionCaseError`, which must not be re-wrapped into a doubled message.
    """
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise RegressionCaseError(f"{path}: case module failed to construct: {exc}") from exc
    return _cases_from_object(module, path)


def _cases_from_object(module: object, path: Path) -> list[RegressionCase]:
    """Pull ``CASE`` / ``CASES`` off an imported case module, validating each is a case.

    Split from the import step so the extraction + validation is unit-testable with a plain namespace.
    A module exposing neither, or a non-:class:`RegressionCase` value, raises
    :class:`RegressionCaseError` naming ``path``.
    """
    found: list[RegressionCase] = []
    if hasattr(module, "CASE"):
        found.append(module.CASE)
    if hasattr(module, "CASES"):
        found.extend(module.CASES)
    if not found:
        raise RegressionCaseError(f"{path}: case module defines neither CASE nor CASES")
    for case in found:
        if not isinstance(case, RegressionCase):
            raise RegressionCaseError(
                f"{path}: CASE/CASES must be RegressionCase(s), got {type(case).__name__}"
            )
    return found


def _reject_duplicate_ids(cases: list[RegressionCase]) -> None:
    """Raise if two cases share an id — a dup would silently shadow one case's scores."""
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise RegressionCaseError(f"duplicate regression case id: {case.id!r}")
        seen.add(case.id)
