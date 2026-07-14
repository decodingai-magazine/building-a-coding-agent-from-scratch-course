"""Discover the regression probes declared under :mod:`evals.regression.cases` (ADR-0017 §6).

The probe registry is "flat and readable": each probe lives in its own module under
``evals/regression/cases/`` exposing a module-level ``PROBE`` (a single
:class:`~evals.regression.probe.RegressionProbe`) or ``PROBES`` (a list). :func:`load_probes` imports
each case module by name, collects them, and validates the set — every id non-blank and unique — so a
duplicate or empty id fails the whole scan loudly rather than shadowing a probe silently. Dropping a
new ``cases/<name>.py`` is all it takes to register a probe; no central list to keep in sync (the same
auto-discovery philosophy as the benchmark's folder scan).
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

from evals.regression.probe import RegressionProbe

logger = logging.getLogger(__name__)

# The probe modules live here (``evals/regression/cases/``). ``parent`` is ``evals/regression/``.
CASES_DIR = Path(__file__).resolve().parent / "cases"

# The import package the case modules are imported under.
CASES_PACKAGE = "evals.regression.cases"


class RegressionProbeError(Exception):
    """A probe module or the assembled probe set violates the contract (bad ``PROBE``, dup id, …)."""


def load_probes(
    cases_dir: Path = CASES_DIR, *, package: str = CASES_PACKAGE
) -> list[RegressionProbe]:
    """Import every probe module under ``cases_dir`` and return the validated probe list (ADR-0017 §6).

    Scans ``cases_dir`` for ``*.py`` modules (skipping ``__init__`` and dunder files), imports each as
    ``<package>.<stem>``, and collects its ``PROBE`` / ``PROBES``. A module with neither, or a
    ``PROBE`` that is not a :class:`RegressionProbe`, raises :class:`RegressionProbeError`. The result
    is sorted by id and checked for uniqueness (a duplicate id is a loud failure). A missing
    ``cases_dir`` yields an empty list — the real behavior probes land in tasks 112-114.
    """
    if not cases_dir.is_dir():
        return []

    probes: list[RegressionProbe] = []
    for path in sorted(cases_dir.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        probes.extend(_probes_from_module(f"{package}.{path.stem}", path))

    _reject_duplicate_ids(probes)
    return sorted(probes, key=lambda probe: probe.id)


def probe_by_id(probe_id: str, probes: list[RegressionProbe] | None = None) -> RegressionProbe:
    """Return the probe with ``probe_id`` from ``probes`` (defaults to :func:`load_probes`).

    Raises :class:`RegressionProbeError` naming ``probe_id`` when no probe matches — a friendly stop
    for a mistyped ``--probe`` id.
    """
    catalog = probes if probes is not None else load_probes()
    for probe in catalog:
        if probe.id == probe_id:
            return probe
    known = ", ".join(sorted(probe.id for probe in catalog)) or "<none>"
    raise RegressionProbeError(f"no regression probe with id {probe_id!r}; known probes: {known}")


def _probes_from_module(module_name: str, path: Path) -> list[RegressionProbe]:
    """Import one case module and pull its ``PROBE`` / ``PROBES`` — each a :class:`RegressionProbe`."""
    return _probes_from_object(importlib.import_module(module_name), path)


def _probes_from_object(module: object, path: Path) -> list[RegressionProbe]:
    """Pull ``PROBE`` / ``PROBES`` off an imported case module, validating each is a probe.

    Split from the import step so the extraction + validation is unit-testable with a plain namespace.
    A module exposing neither, or a non-:class:`RegressionProbe` value, raises
    :class:`RegressionProbeError` naming ``path``.
    """
    found: list[RegressionProbe] = []
    if hasattr(module, "PROBE"):
        found.append(module.PROBE)
    if hasattr(module, "PROBES"):
        found.extend(module.PROBES)
    if not found:
        raise RegressionProbeError(f"{path}: case module defines neither PROBE nor PROBES")
    for probe in found:
        if not isinstance(probe, RegressionProbe):
            raise RegressionProbeError(
                f"{path}: PROBE/PROBES must be RegressionProbe(s), got {type(probe).__name__}"
            )
    return found


def _reject_duplicate_ids(probes: list[RegressionProbe]) -> None:
    """Raise if two probes share an id — a dup would silently shadow one probe's scores."""
    seen: set[str] = set()
    for probe in probes:
        if probe.id in seen:
            raise RegressionProbeError(f"duplicate regression probe id: {probe.id!r}")
        seen.add(probe.id)
