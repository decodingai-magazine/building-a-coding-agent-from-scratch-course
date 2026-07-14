"""Contract tests for the ``RegressionProbe`` declaration (ADR-0017 §6).

Pure and offline: constructing a probe validates its own invariants (non-blank id / prompt, at least
one metric) and defaults to the headless ``BYPASS`` posture so the simplest probe is three fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decode.permissions.types import PermissionMode
from evals.regression.probe import RegressionProbe


def _noop_fixture(_workspace: Path) -> None:
    """A do-nothing fixture — enough to construct a probe in these contract tests."""


def _probe(**overrides: object) -> RegressionProbe:
    base = {
        "id": "p1",
        "prompt": "do the thing",
        "fixture": _noop_fixture,
        "metrics": [object()],
    }
    base.update(overrides)
    return RegressionProbe(**base)  # type: ignore[arg-type]


def test_defaults_are_the_headless_bypass_posture() -> None:
    probe = _probe()

    assert probe.gate_mode == PermissionMode.BYPASS
    assert probe.permission_rules is None
    assert probe.resolve_permission is None
    assert probe.resolve_user_question is None
    assert probe.message_history is None
    assert probe.context is None
    assert probe.max_requests is None
    assert probe.tags == []


def test_blank_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="id must not be blank"):
        _probe(id="   ")


def test_blank_prompt_is_rejected() -> None:
    with pytest.raises(ValueError, match="prompt must not be blank"):
        _probe(prompt="")


def test_a_probe_without_metrics_is_rejected() -> None:
    """A metric-less probe would run the agent and score nothing — a silent suite no-op."""
    with pytest.raises(ValueError, match="at least one metric"):
        _probe(metrics=[])
