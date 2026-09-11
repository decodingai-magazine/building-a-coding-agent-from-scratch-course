"""Contract tests for the ``RegressionCase`` declaration (ADR-0022 §8; ADR-0017 §6).

Pure and offline: constructing a case validates its own invariants (non-blank id / prompt, at least
one metric, a real difficulty tier, a non-blank symptom and assertion) and defaults to the headless
``BYPASS`` posture, so the simplest case is the seven fields every case must answer for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decode.permissions.types import PermissionMode
from evals.regression.case import DIFFICULTIES, RegressionCase


def _noop_fixture(_workspace: Path) -> None:
    """A do-nothing fixture — enough to construct a case in these contract tests."""


def _case(**overrides: object) -> RegressionCase:
    base = {
        "id": "c1",
        "prompt": "do the thing",
        "fixture": _noop_fixture,
        "metrics": [object()],
        "difficulty": "easy",
        "symptom": "harness invariant: the agent shells out instead of using the tool.",
        "assertion": "The response reports the file's contents.",
    }
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
