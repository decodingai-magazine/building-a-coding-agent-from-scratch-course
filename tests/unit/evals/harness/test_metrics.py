"""Offline unit tests for the custom Opik code metrics (ADR-0017 §4,7; task 104).

Every metric is scored against crafted inputs in BOTH outcomes plus a malformed / missing-field
case, proving the contract from the task: a :class:`ScoreResult` with ``value`` in ``[0, 1]`` and a
non-empty ``reason``, and a graceful ``0.0`` (never a raise) when the field the metric needs is
absent or the wrong shape. No network, no keys, no Opik backend.
"""

from __future__ import annotations

import pytest
from opik.evaluation.metrics.score_result import ScoreResult

from evals.harness.driver import ToolCallRecord
from evals.harness.metrics import (
    DiffLinesMetric,
    FileDiffLinesMetric,
    MaxStepsMetric,
    OutputContainsMetric,
    ToolCalledMetric,
    ToolNotCalledMetric,
    ToolNotSucceededMetric,
    VerifyOracleMetric,
)


def _assert_well_formed(result: ScoreResult) -> None:
    """Every metric must return a named ScoreResult in [0, 1] with a non-empty reason."""
    assert isinstance(result, ScoreResult)
    assert result.name
    assert 0.0 <= result.value <= 1.0
    assert result.reason


# --- Hermeticity: metrics never phone Opik ---------------------------------------------------


@pytest.mark.parametrize(
    "metric",
    [
        ToolCalledMetric("read"),
        ToolNotCalledMetric("read"),
        VerifyOracleMetric(),
        MaxStepsMetric(),
        DiffLinesMetric(max_lines=5),
        FileDiffLinesMetric(path="config.py", baseline="PORT = 8000\n", max_lines=2),
        OutputContainsMetric("broken.py"),
        ToolNotSucceededMetric("write"),
    ],
)
def test_metrics_disable_opik_tracking(metric: object) -> None:
    # track=False keeps score() from wrapping in opik.track(...), which would open a real outbound
    # HTTPS round-trip to comet.com — breaking the "all tests offline" AC (ADR-0017 §9).
    assert metric.track is False


def test_metrics_never_install_the_opik_track_decorator(mocker) -> None:
    # BaseMetric wraps score/ascore in opik.track(...) ONLY when track=True; that decorator is what
    # flushes a span to comet.com. Patch it and prove constructing our metrics never reaches it —
    # a direct, deterministic guard (the SDK flushes on a background thread, so watching sockets is
    # not reliable).
    track = mocker.patch("opik.track")

    ToolCalledMetric("read")
    ToolNotCalledMetric("write")
    VerifyOracleMetric()
    MaxStepsMetric()
    DiffLinesMetric(max_lines=5)
    FileDiffLinesMetric(path="config.py", baseline="PORT = 8000\n", max_lines=2)
    OutputContainsMetric("broken.py")
    ToolNotSucceededMetric("write")

    track.assert_not_called()


# --- ToolCalledMetric ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_calls",
    [
        [{"name": "read", "args": {}}, {"name": "bash", "args": {}}],
        [ToolCallRecord(name="read", args={}), ToolCallRecord(name="bash", args={})],
        ["read", "bash"],
    ],
)
def test_tool_called_hit_across_shapes(tool_calls: list) -> None:
    result = ToolCalledMetric("read").score(tool_calls=tool_calls)
    _assert_well_formed(result)
    assert result.value == 1.0
    assert "read" in result.reason


def test_tool_called_miss() -> None:
    result = ToolCalledMetric("write").score(tool_calls=[{"name": "read", "args": {}}])
    _assert_well_formed(result)
    assert result.value == 0.0


def test_tool_called_missing_field_is_graceful_zero() -> None:
    result = ToolCalledMetric("read").score()
    _assert_well_formed(result)
    assert result.value == 0.0


def test_tool_called_malformed_field_is_graceful_zero() -> None:
    result = ToolCalledMetric("read").score(tool_calls="not-a-list")
    _assert_well_formed(result)
    assert result.value == 0.0


# --- ToolNotCalledMetric ---------------------------------------------------------------------


def test_tool_not_called_absent_scores_one() -> None:
    result = ToolNotCalledMetric("write").score(tool_calls=[{"name": "read", "args": {}}])
    _assert_well_formed(result)
    assert result.value == 1.0


def test_tool_not_called_present_scores_zero() -> None:
    result = ToolNotCalledMetric("read").score(tool_calls=[{"name": "read", "args": {}}])
    _assert_well_formed(result)
    assert result.value == 0.0


def test_tool_not_called_missing_field_scores_one() -> None:
    # No tool calls recorded means the forbidden tool was, trivially, not called.
    result = ToolNotCalledMetric("read").score()
    _assert_well_formed(result)
    assert result.value == 1.0


# --- VerifyOracleMetric ----------------------------------------------------------------------


def test_verify_oracle_pass_on_exit_zero() -> None:
    result = VerifyOracleMetric().score(verify={"exit_code": 0, "stdout": "PASS: all checks"})
    _assert_well_formed(result)
    assert result.value == 1.0
    assert "PASS" in result.reason


def test_verify_oracle_fail_on_nonzero_exit() -> None:
    result = VerifyOracleMetric().score(verify={"exit_code": 1, "stdout": "FAIL: missing file"})
    _assert_well_formed(result)
    assert result.value == 0.0


def test_verify_oracle_missing_field_is_graceful_zero() -> None:
    result = VerifyOracleMetric().score()
    _assert_well_formed(result)
    assert result.value == 0.0


def test_verify_oracle_malformed_field_is_graceful_zero() -> None:
    result = VerifyOracleMetric().score(verify={"stdout": "no exit code here"})
    _assert_well_formed(result)
    assert result.value == 0.0


# --- MaxStepsMetric --------------------------------------------------------------------------


def test_max_steps_within_budget() -> None:
    result = MaxStepsMetric().score(steps=3, max_steps=5)
    _assert_well_formed(result)
    assert result.value == 1.0
    assert "3" in result.reason


def test_max_steps_over_budget() -> None:
    result = MaxStepsMetric().score(steps=8, max_steps=5)
    _assert_well_formed(result)
    assert result.value == 0.0
    assert "8" in result.reason


def test_max_steps_at_boundary_is_pass() -> None:
    result = MaxStepsMetric().score(steps=5, max_steps=5)
    _assert_well_formed(result)
    assert result.value == 1.0


def test_max_steps_missing_field_is_graceful_zero() -> None:
    result = MaxStepsMetric().score(steps=3)
    _assert_well_formed(result)
    assert result.value == 0.0


# --- DiffLinesMetric -------------------------------------------------------------------------


DIFF_TWO_CHANGED = """--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,2 @@
-old line
+new line
 unchanged
+added line
"""


def test_diff_lines_within_threshold() -> None:
    result = DiffLinesMetric(max_lines=5).score(diff=DIFF_TWO_CHANGED)
    _assert_well_formed(result)
    assert result.value == 1.0
    assert "3" in result.reason  # two '+' plus one '-'


def test_diff_lines_over_threshold() -> None:
    result = DiffLinesMetric(max_lines=2).score(diff=DIFF_TWO_CHANGED)
    _assert_well_formed(result)
    assert result.value == 0.0


def test_diff_lines_empty_diff_scores_one() -> None:
    result = DiffLinesMetric(max_lines=5).score(diff="")
    _assert_well_formed(result)
    assert result.value == 1.0
    assert "0" in result.reason


def test_diff_lines_missing_field_is_graceful_zero() -> None:
    result = DiffLinesMetric(max_lines=5).score()
    _assert_well_formed(result)
    assert result.value == 0.0


# --- FileDiffLinesMetric ---------------------------------------------------------------------


_CONFIG_BASELINE = "HOST = 'localhost'\nPORT = 8000\nDEBUG = False\n"


def test_file_diff_lines_single_line_edit_within_threshold() -> None:
    # One line changed (PORT 8000 -> 9000) is one '-' plus one '+' = two changed lines.
    final = "HOST = 'localhost'\nPORT = 9000\nDEBUG = False\n"
    result = FileDiffLinesMetric(path="config.py", baseline=_CONFIG_BASELINE, max_lines=2).score(
        file_state={"config.py": final}
    )
    _assert_well_formed(result)
    assert result.value == 1.0
    assert "2 changed line" in result.reason


def test_file_diff_lines_over_threshold_scores_zero() -> None:
    final = "HOST = 'example.com'\nPORT = 9000\nDEBUG = True\n"  # three lines changed
    result = FileDiffLinesMetric(path="config.py", baseline=_CONFIG_BASELINE, max_lines=2).score(
        file_state={"config.py": final}
    )
    _assert_well_formed(result)
    assert result.value == 0.0


def test_file_diff_lines_unchanged_file_scores_one() -> None:
    result = FileDiffLinesMetric(path="config.py", baseline=_CONFIG_BASELINE, max_lines=2).score(
        file_state={"config.py": _CONFIG_BASELINE}
    )
    _assert_well_formed(result)
    assert result.value == 1.0
    assert "0 changed line" in result.reason


def test_file_diff_lines_missing_file_is_graceful_zero() -> None:
    result = FileDiffLinesMetric(path="config.py", baseline=_CONFIG_BASELINE, max_lines=2).score(
        file_state={"other.py": "x = 1\n"}
    )
    _assert_well_formed(result)
    assert result.value == 0.0


def test_file_diff_lines_missing_field_is_graceful_zero() -> None:
    result = FileDiffLinesMetric(path="config.py", baseline=_CONFIG_BASELINE, max_lines=2).score()
    _assert_well_formed(result)
    assert result.value == 0.0


# --- OutputContainsMetric --------------------------------------------------------------------


def test_output_contains_hit_is_case_insensitive() -> None:
    result = OutputContainsMetric("Broken.py").score(output="I found a type error in broken.py")
    _assert_well_formed(result)
    assert result.value == 1.0
    assert "found" in result.reason


def test_output_contains_miss_scores_zero() -> None:
    result = OutputContainsMetric("broken.py").score(output="nothing relevant here")
    _assert_well_formed(result)
    assert result.value == 0.0


def test_output_contains_case_sensitive_respects_case() -> None:
    result = OutputContainsMetric("PORT", case_sensitive=True).score(output="the port is set")
    _assert_well_formed(result)
    assert result.value == 0.0


def test_output_contains_missing_field_is_graceful_zero() -> None:
    result = OutputContainsMetric("broken.py").score()
    _assert_well_formed(result)
    assert result.value == 0.0


# --- ToolNotSucceededMetric ------------------------------------------------------------------


def test_tool_not_succeeded_never_called_scores_one() -> None:
    result = ToolNotSucceededMetric("write").score(tool_calls=[{"name": "read", "args": {}}])
    _assert_well_formed(result)
    assert result.value == 1.0


def test_tool_not_succeeded_called_but_denied_scores_one() -> None:
    # The model attempted the write, but the gate denied it — it never landed, so this passes.
    result = ToolNotSucceededMetric("write").score(
        tool_calls=[{"name": "write", "args": {}}], denied_tools=["write"]
    )
    _assert_well_formed(result)
    assert result.value == 1.0


def test_tool_not_succeeded_landed_call_scores_zero() -> None:
    result = ToolNotSucceededMetric("edit").score(
        tool_calls=[{"name": "edit", "args": {}}], denied_tools=[]
    )
    _assert_well_formed(result)
    assert result.value == 0.0


def test_tool_not_succeeded_missing_field_scores_one() -> None:
    # No calls recorded means the tool trivially never succeeded.
    result = ToolNotSucceededMetric("write").score()
    _assert_well_formed(result)
    assert result.value == 1.0
