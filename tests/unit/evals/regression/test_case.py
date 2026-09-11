"""Contract tests for the ``RegressionCase`` declaration (ADR-0022 §8; ADR-0017 §6).

Pure and offline: constructing a case validates its own invariants (non-blank id / prompt, at least
one metric, a real difficulty tier, a non-blank description, symptom and assertion) and defaults to
the headless ``BYPASS`` posture, so the simplest case is the eight fields every case must answer for.

The shipped registry's ``description`` house style — ONE ``Tests that …`` sentence, ≤ 160 characters,
no ``|`` — is held at the bottom of this file, over every loaded case: the contract only refuses a
BLANK description, the style is what keeps the README's case table readable and generatable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decode.permissions.types import PermissionMode
from evals.regression.case import DIFFICULTIES, RegressionCase
from evals.regression.loader import load_cases


def _noop_fixture(_workspace: Path) -> None:
    """A do-nothing fixture — enough to construct a case in these contract tests."""


def _case_fields() -> dict[str, object]:
    """The minimal field set every contract test builds on — one per REQUIRED field."""
    return {
        "id": "c1",
        "prompt": "do the thing",
        "fixture": _noop_fixture,
        "metrics": [object()],
        "difficulty": "easy",
        "description": "Tests that the agent uses the tool instead of shelling out.",
        "symptom": "harness invariant: the agent shells out instead of using the tool.",
        "assertion": "The response reports the file's contents.",
    }


def _case(**overrides: object) -> RegressionCase:
    base = _case_fields()
    base.update(overrides)
    return RegressionCase(**base)  # type: ignore[arg-type]


def test_defaults_are_the_headless_bypass_posture() -> None:
    case = _case()

    assert case.gate_mode == PermissionMode.BYPASS
    assert case.permission_rules is None
    assert case.resolve_permission is None
    assert case.resolve_user_question is None
    assert case.message_history is None
    assert case.context is None
    assert case.max_requests is None
    assert case.tags == []
    assert case.skip_reason is None


def test_provenance_fields_default_to_none_and_are_carried_when_set() -> None:
    """A mined case carries where it came from; an invented one leaves all three empty (§8)."""
    invented = _case()

    assert invented.source_trace_id is None
    assert invented.thread_id is None
    assert invented.fixed_in is None

    mined = _case(source_trace_id="trace-1", thread_id="thread-1", fixed_in="abc1234")

    assert (mined.source_trace_id, mined.thread_id, mined.fixed_in) == (
        "trace-1",
        "thread-1",
        "abc1234",
    )


def test_skip_reason_is_carried_when_set() -> None:
    case = _case(skip_reason="not ready")

    assert case.skip_reason == "not ready"


def test_blank_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="id must not be blank"):
        _case(id="   ")


def test_blank_prompt_is_rejected() -> None:
    with pytest.raises(ValueError, match="prompt must not be blank"):
        _case(prompt="")


def test_a_case_without_metrics_is_rejected() -> None:
    """A metric-less case would run the agent and score nothing — a silent suite no-op."""
    with pytest.raises(ValueError, match="at least one metric"):
        _case(metrics=[])


def test_the_case_tiers_are_the_benchmark_tiers() -> None:
    """ONE Difficulty Tier vocabulary across both eval tracks (the case contract spells it out so it
    stays importable without the Opik harness — see the module comment)."""
    from typing import get_args

    from evals.harness.task_loader import Difficulty

    assert get_args(Difficulty) == DIFFICULTIES


def test_importing_the_case_contract_pulls_no_opik() -> None:
    """ADR-0017 §1: the contract is data, so the CLI can read it without the Opik harness."""
    import subprocess
    import sys

    probe = "import sys; import evals.regression.case; print('opik' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "False"


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_every_difficulty_tier_is_accepted(difficulty: str) -> None:
    assert _case(difficulty=difficulty).difficulty == difficulty


def test_an_unknown_difficulty_is_rejected() -> None:
    """``--difficulty`` slices the suite, so a typo'd tier must fail at declaration, not silently."""
    with pytest.raises(ValueError, match="difficulty must be one of"):
        _case(difficulty="trivial")


def test_blank_symptom_is_rejected() -> None:
    """The symptom is what a reader sees first — a blank one makes the case unreadable (§8)."""
    with pytest.raises(ValueError, match="symptom must not be blank"):
        _case(symptom="  ")


def test_blank_assertion_is_rejected() -> None:
    """The assertion IS the Test Suite item's quality bar; blank would register an ungraded item."""
    with pytest.raises(ValueError, match="assertion must not be blank"):
        _case(assertion="")


def test_blank_description_is_rejected() -> None:
    """A blank description leaves the README's case table with an empty row (the error names the id)."""
    with pytest.raises(ValueError, match="'c1': description must not be blank"):
        _case(description="   ")


def test_a_case_without_a_description_cannot_be_constructed() -> None:
    """``description`` is REQUIRED — omitting it is a TypeError, not a silently empty column."""
    base = {key: value for key, value in _case_fields().items() if key != "description"}

    with pytest.raises(TypeError, match="description"):
        RegressionCase(**base)  # type: ignore[arg-type]


# --- the shipped registry's description house style -------------------------------------------------

# Long enough for a real sentence, short enough to sit in a README table cell without wrapping it.
MAX_DESCRIPTION_CHARS = 160

# The two openings a what-it-tests line may take. Anything else ("Checks…", "The agent…") drifts the
# table into mixed voice.
DESCRIPTION_PREFIXES = ("Tests that ", "Tests whether ")

# The human-readable half of the registry: ``scripts/gen_eval_tables.py`` writes one row per case
# here, so a case added without regenerating it fails below.
REGRESSION_README = Path(__file__).resolve().parents[4] / "evals" / "regression" / "README.md"


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case.id)
def test_every_shipped_case_describes_itself_in_one_short_sentence(case: RegressionCase) -> None:
    """One sentence, ``Tests that …``, ≤ 160 chars, no ``|`` — the README table's contract (§8)."""
    description = case.description

    assert description.startswith(DESCRIPTION_PREFIXES), (
        f"{case.id}: description must start with one of {DESCRIPTION_PREFIXES}, got {description!r}"
    )
    assert len(description) <= MAX_DESCRIPTION_CHARS, (
        f"{case.id}: description is {len(description)} chars, over {MAX_DESCRIPTION_CHARS}"
    )
    assert description.endswith("."), f"{case.id}: description must end in a full stop"
    assert ". " not in description, (
        f"{case.id}: description must be ONE sentence, got {description!r}"
    )
    assert "|" not in description, f"{case.id}: a pipe would break the README's markdown table row"


def test_every_shipped_case_description_is_unique() -> None:
    """Two cases sharing a line means one of them is not described — the table would repeat itself."""
    descriptions = [case.description for case in load_cases()]

    assert len(set(descriptions)) == len(descriptions)


def test_every_shipped_case_description_appears_in_the_readme_table() -> None:
    """The README's case table is generated from this registry; it must not go stale (§8)."""
    readme = REGRESSION_README.read_text(encoding="utf-8")

    missing = [case.id for case in load_cases() if case.description not in readme]

    assert not missing, (
        f"{REGRESSION_README.name} has no description row for: {missing} — "
        "run `uv run python scripts/gen_eval_tables.py`"
    )
