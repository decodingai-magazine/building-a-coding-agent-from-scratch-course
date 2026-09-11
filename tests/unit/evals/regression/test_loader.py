"""Case registry discovery, selection + validation (ADR-0022 §8; ADR-0017 §6).

Offline: exercises the real ``evals/regression/cases`` registry (the 21-case invented floor loads,
tiered 5/8/8, with the mined cases beside it and the skip-guarded ones still discoverable) plus the extraction / selection / validation helpers with
constructed inputs, so a duplicate id, a module with no ``CASE``, or a non-case value all fail loudly.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.regression.case import RegressionCase
from evals.regression.loader import (
    RegressionCaseError,
    _cases_from_object,
    _reject_duplicate_ids,
    case_by_id,
    load_cases,
    runnable_cases,
    select_cases,
)

# The tier assignment ADR-0022 §8 fixes: easy = single-tool discipline, medium = planning /
# delegation / skills / memory / web / lsp, hard = compaction, the gate, destructive caution, the
# judged answers, the json contract.
EXPECTED_TIER_COUNTS = {"easy": 5, "medium": 8, "hard": 8}

EXPECTED_EASY_IDS = {
    "smoke-read-tool",
    "01-read-vs-cat",
    "02-grep-vs-bash",
    "03-edit-precision",
    "11-step-efficiency",
}

# Mined cases (task 164) land BESIDE the invented floor, tagged ``mined`` — the counts above are the
# floor's, never the registry's total, so mining one more case never edits a tier count.
EXPECTED_MINED_EASY_IDS = {"21-empty-model-response", "22-guessed-file-path"}

# Declared but never run: the MCP case (no tool factory yet) and mined case zero (no 400 to reproduce).
EXPECTED_SKIPPED_IDS = {"12-mcp-tool-usage", "23-bad-request-400"}


# A case module the loader will import for the error-path test: the ``CASE`` is constructed at import
# time (exactly as every shipped case module does), so a bad field raises inside ``import_module``.
_CASE_MODULE_TEMPLATE = '''\
"""A throwaway case module written to a temp package by the loader's error-path test."""

from pathlib import Path

from evals.regression.case import RegressionCase


def _fixture(_workspace: Path) -> None:
    """Seed nothing."""


CASE = RegressionCase(
    id="bad-case",
    prompt="p",
    fixture=_fixture,
    metrics=[object()],
    description="Tests that it behaves.",
    symptom="harness invariant: it behaves.",
{fields}
)
'''

# The three ways a hand-written case module fails to construct: an unknown tier and a blank assertion
# trip ``RegressionCase.__post_init__`` (``ValueError``); an omitted required field never reaches it
# (``TypeError``). All three must surface as one ``RegressionCaseError`` naming the module path.
BAD_CASE_MODULES = {
    "bad_tier": '    difficulty="ultra",\n    assertion="The response behaves.",',
    "blank_assertion": '    difficulty="easy",\n    assertion="   ",',
    "missing_difficulty": '    assertion="The response behaves.",',
}


def _noop_fixture(_workspace: Path) -> None:
    """A do-nothing fixture for constructed test cases."""


def _case(case_id: str, **overrides: object) -> RegressionCase:
    fields: dict[str, object] = {
        "id": case_id,
        "prompt": "p",
        "fixture": _noop_fixture,
        "metrics": [object()],
        "difficulty": "easy",
        "description": "Tests that it behaves.",
        "symptom": "harness invariant: it behaves.",
        "assertion": "The response behaves.",
    }
    fields.update(overrides)
    return RegressionCase(**fields)  # type: ignore[arg-type]


def test_real_registry_loads_and_includes_the_reference_case() -> None:
    """The shipped ``cases/`` registry discovers the reference case and validates the set."""
    cases = load_cases()

    ids = [case.id for case in cases]
    assert "smoke-read-tool" in ids
    assert len(ids) == len(set(ids)), "case ids must be unique across the suite"
    assert all(isinstance(case, RegressionCase) for case in cases)


def test_cases_are_sorted_by_id() -> None:
    cases = load_cases()

    assert [case.id for case in cases] == sorted(case.id for case in cases)


def test_a_missing_cases_dir_yields_no_cases(tmp_path: Path) -> None:
    """A missing registry is an empty list, not a crash."""
    assert load_cases(tmp_path / "does-not-exist") == []


def test_the_invented_floor_is_21_cases_tiered_five_eight_eight() -> None:
    """Every ADR-0017 probe is KEPT and tiered — nothing was dropped, for v2 or for a mined case (§8)."""
    invented = [case for case in load_cases() if "mined" not in case.tags]

    counts = {tier: 0 for tier in EXPECTED_TIER_COUNTS}
    for case in invented:
        counts[case.difficulty] += 1

    assert len(invented) == 21
    assert counts == EXPECTED_TIER_COUNTS


def test_mined_cases_land_beside_the_floor_never_inside_it() -> None:
    """A mined case is additive: the registry grows, the invented floor does not move (§8)."""
    cases = load_cases()
    mined = [case for case in cases if "mined" in case.tags]

    assert mined, "the mining session's cases are part of the registry"
    assert len(cases) == 21 + len(mined)
    for case in mined:
        assert case.fixed_in, case.id


def test_every_case_declares_a_symptom_and_an_assertion() -> None:
    """A case a reader cannot read, or a suite item with no bar, is not shippable (§8)."""
    for case in load_cases():
        assert case.symptom.strip(), case.id
        assert case.assertion.strip(), case.id


def test_the_skip_guarded_case_is_still_discoverable_but_never_runnable() -> None:
    """Case 12 (MCP) stays in the registry so it activates the moment its blocker clears."""
    cases = load_cases()

    mcp = case_by_id("12-mcp-tool-usage", cases)

    assert mcp.skip_reason
    assert mcp.difficulty == "medium"
    assert mcp not in runnable_cases(cases)
    assert {case.id for case in cases if case.skip_reason} == EXPECTED_SKIPPED_IDS
    assert len(runnable_cases(cases)) == len(cases) - len(EXPECTED_SKIPPED_IDS)


def test_difficulty_easy_selects_the_five_easy_cases_plus_the_mined_ones() -> None:
    """``--difficulty easy`` is the tier slice the CLI and the gate ritual both run on (§8)."""
    selected = select_cases(load_cases(), difficulty="easy")

    assert {case.id for case in selected} == EXPECTED_EASY_IDS | EXPECTED_MINED_EASY_IDS


def test_select_cases_filters_by_id_and_tier_and_defaults_to_everything() -> None:
    cases = [_case("a"), _case("b", difficulty="hard")]

    assert select_cases(cases) == cases
    assert select_cases(cases, case_id="a") == [cases[0]]
    assert select_cases(cases, difficulty="hard") == [cases[1]]
    assert select_cases(cases, case_id="a", difficulty="hard") == []


def test_case_by_id_finds_the_reference_case() -> None:
    case = case_by_id("smoke-read-tool")

    assert case.id == "smoke-read-tool"


def test_case_by_id_raises_a_friendly_error_for_an_unknown_id() -> None:
    with pytest.raises(RegressionCaseError, match="no regression case with id 'nope'"):
        case_by_id("nope", cases=[_case("smoke-read-tool")])


def test_extract_reads_both_case_and_cases() -> None:
    module = SimpleNamespace(CASE=_case("a"), CASES=[_case("b"), _case("c")])

    found = _cases_from_object(module, Path("case.py"))

    assert [case.id for case in found] == ["a", "b", "c"]


def test_extract_rejects_a_module_with_neither_case_nor_cases() -> None:
    with pytest.raises(RegressionCaseError, match="neither CASE nor CASES"):
        _cases_from_object(SimpleNamespace(), Path("case.py"))


def test_extract_rejects_a_non_case_value() -> None:
    with pytest.raises(RegressionCaseError, match="must be RegressionCase"):
        _cases_from_object(SimpleNamespace(CASE="not a case"), Path("case.py"))


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(RegressionCaseError, match="duplicate regression case id: 'dup'"):
        _reject_duplicate_ids([_case("dup"), _case("dup")])


@pytest.mark.parametrize("shape", sorted(BAD_CASE_MODULES))
def test_a_case_module_that_fails_to_construct_names_the_module_path(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A case whose own ``RegressionCase(...)`` raises stops the scan with the module path (§8).

    The three shapes a typo actually takes — an unknown tier, a blank ``assertion``, a missing
    required field — all blow up inside the case module's import, not in the loader's extraction
    step. Without a wrapper the scan leaks a bare ``ValueError``/``TypeError`` naming only the case
    id, so a 21-module registry gives a reader nothing to open. The loader owes the same contract
    here as for a structurally bad module: one :class:`RegressionCaseError` naming the file.
    """
    package = f"badcases_{shape}"
    cases_dir = tmp_path / package
    cases_dir.mkdir()
    (cases_dir / "__init__.py").write_text("", encoding="utf-8")
    module_path = cases_dir / "bad_case.py"
    module_path.write_text(
        _CASE_MODULE_TEMPLATE.format(fields=BAD_CASE_MODULES[shape]), encoding="utf-8"
    )
    monkeypatch.syspath_prepend(tmp_path)

    with pytest.raises(RegressionCaseError) as excinfo:
        load_cases(cases_dir, package=package)

    message = str(excinfo.value)
    assert str(module_path) in message, message
    assert "failed to construct" in message, message
