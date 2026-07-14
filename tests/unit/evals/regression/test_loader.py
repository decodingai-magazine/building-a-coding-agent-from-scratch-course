"""Probe registry discovery + validation (ADR-0017 §6).

Offline: exercises the real ``evals/regression/cases`` registry (the reference probe loads and
validates) plus the extraction / validation helpers with constructed inputs, so a duplicate id, a
module with no ``PROBE``, or a non-probe value all fail loudly.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.regression.loader import (
    RegressionProbeError,
    _probes_from_object,
    _reject_duplicate_ids,
    load_probes,
    probe_by_id,
)
from evals.regression.probe import RegressionProbe


def _noop_fixture(_workspace: Path) -> None:
    """A do-nothing fixture for constructed test probes."""


def _probe(probe_id: str) -> RegressionProbe:
    return RegressionProbe(id=probe_id, prompt="p", fixture=_noop_fixture, metrics=[object()])


def test_real_registry_loads_and_includes_the_reference_probe() -> None:
    """The shipped ``cases/`` registry discovers the reference probe and validates the set."""
    probes = load_probes()

    ids = [probe.id for probe in probes]
    assert "smoke-read-tool" in ids
    assert len(ids) == len(set(ids)), "probe ids must be unique across the suite"
    assert all(isinstance(probe, RegressionProbe) for probe in probes)


def test_probes_are_sorted_by_id() -> None:
    probes = load_probes()

    assert [probe.id for probe in probes] == sorted(probe.id for probe in probes)


def test_a_missing_cases_dir_yields_no_probes(tmp_path: Path) -> None:
    """A missing registry is an empty list, not a crash — the real probes land in tasks 112-114."""
    assert load_probes(tmp_path / "does-not-exist") == []


def test_probe_by_id_finds_the_reference_probe() -> None:
    probe = probe_by_id("smoke-read-tool")

    assert probe.id == "smoke-read-tool"


def test_probe_by_id_raises_a_friendly_error_for_an_unknown_id() -> None:
    with pytest.raises(RegressionProbeError, match="no regression probe with id 'nope'"):
        probe_by_id("nope", probes=[_probe("smoke-read-tool")])


def test_extract_reads_both_probe_and_probes() -> None:
    module = SimpleNamespace(PROBE=_probe("a"), PROBES=[_probe("b"), _probe("c")])

    found = _probes_from_object(module, Path("case.py"))

    assert [probe.id for probe in found] == ["a", "b", "c"]


def test_extract_rejects_a_module_with_neither_probe_nor_probes() -> None:
    with pytest.raises(RegressionProbeError, match="neither PROBE nor PROBES"):
        _probes_from_object(SimpleNamespace(), Path("case.py"))


def test_extract_rejects_a_non_probe_value() -> None:
    with pytest.raises(RegressionProbeError, match="must be RegressionProbe"):
        _probes_from_object(SimpleNamespace(PROBE="not a probe"), Path("case.py"))


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(RegressionProbeError, match="duplicate regression probe id: 'dup'"):
        _reject_duplicate_ids([_probe("dup"), _probe("dup")])
