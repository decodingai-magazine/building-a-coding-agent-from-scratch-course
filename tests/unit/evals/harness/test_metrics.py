"""Offline unit tests for the custom Opik code metrics (ADR-0017 §4,7; task 104).

Every metric is scored against crafted inputs in BOTH outcomes plus a malformed / missing-field
case, proving the contract from the task: a :class:`ScoreResult` with ``value`` in ``[0, 1]`` and a
non-empty ``reason``, and a graceful ``0.0`` (never a raise) when the field the metric needs is
absent or the wrong shape. No network, no keys, no Opik backend.
"""

from __future__ import annotations

import pytest
from opik.evaluation.metrics.score_result import ScoreResult
from pydantic import BaseModel

from evals.harness.driver import ToolCallRecord
from evals.harness.metrics import (
    AnsweredWithoutErrorMetric,
    DiffLinesMetric,
    FileDiffLinesMetric,
    FileEqualsMetric,
    JsonSchemaMetric,
    MaxStepsMetric,
    NewFileNameMetric,
    OutputContainsMetric,
    RewardMetric,
    ToolArgsMetric,
    ToolArgsNeverMetric,
    ToolCalledMetric,
    ToolNotCalledMetric,
    ToolNotSucceededMetric,
)


class _SampleSchema(BaseModel):
    """A tiny pydantic model the JsonSchemaMetric tests validate output against."""

    file: str
    issue_count: int


def _prefix_dc(name: str) -> bool:
    return name.startswith("dc_")


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
        MaxStepsMetric(),
        DiffLinesMetric(max_lines=5),
        FileDiffLinesMetric(path="config.py", baseline="PORT = 8000\n", max_lines=2),
        OutputContainsMetric("broken.py"),
        ToolNotSucceededMetric("write"),
        ToolArgsMetric("skill", lambda a: True, description="x", name="args_metric"),
        FileEqualsMetric(path="hello.txt", expected="hi"),
        NewFileNameMetric(".py", _prefix_dc, description="dc_ prefix", name="new_file"),
        JsonSchemaMetric(_SampleSchema),
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


# --- RewardMetric ----------------------------------------------------------------------------


def test_reward_metric_scores_a_won_trial() -> None:
    """``agent_ok`` ⇒ the Verifier's reward IS the score of record (ADR-0022 §3)."""
    result = RewardMetric().score(status="agent_ok", reward=1.0, reason=None)

    assert result.name == "reward"
    assert result.value == 1.0
    assert result.scoring_failed is False


def test_reward_metric_scores_a_lost_trial_with_its_reason() -> None:
    """An agent failure is a real 0 in the denominator, and the row says WHY (ADR-0022 §4)."""
    result = RewardMetric().score(
        status="agent_fail", reward=0.0, reason="the run hit its 15-request ceiling"
    )

    assert result.value == 0.0
    assert result.scoring_failed is False
    assert "ceiling" in result.reason


def test_reward_metric_marks_an_infra_error_as_scoring_failed() -> None:
    """An Infra Error is excluded, not zeroed — Opik's own axis for "this was not measurable"."""
    result = RewardMetric().score(
        status="infra_error", reward=None, reason="the Verifier timed out after 600s"
    )

    assert result.value == 0.0
    assert result.scoring_failed is True
    assert "Verifier" in result.reason


def test_reward_metric_marks_a_missing_reward_as_scoring_failed() -> None:
    """A graded status with no number cannot be read as a 0 — it is unscorable (ADR-0022 §3)."""
    result = RewardMetric().score(status="agent_fail", reward=None, reason=None)

    assert result.scoring_failed is True


def test_reward_metric_is_graceful_on_a_missing_payload() -> None:
    """Opik hands the metric whatever the task fn returned; a shapeless payload never raises."""
    result = RewardMetric().score()

    assert result.value == 0.0
    assert result.scoring_failed is True


def test_opik_excludes_a_failed_reward_from_the_aggregate() -> None:
    """The ADR-0022 §4 rule, proven against the INSTALLED opik: a failed score is not a 0.

    ``calculate_aggregated_statistics`` is what feeds Opik's per-experiment / per-item score view;
    it skips ``scoring_failed`` results entirely, so an Infra Error leaves the mean of the two graded
    trials at 0.5 rather than dragging it to 0.33.
    """
    from opik.evaluation.score_statistics import calculate_aggregated_statistics
    from opik.evaluation.test_case import TestCase
    from opik.evaluation.test_result import TestResult

    metric = RewardMetric()
    scores = [
        metric.score(status="agent_ok", reward=1.0, reason=None),
        metric.score(status="agent_fail", reward=0.0, reason="wrong answer"),
        metric.score(status="infra_error", reward=None, reason="the sandbox did not start"),
    ]
    results = [
        TestResult(
            test_case=TestCase(trace_id=f"t{i}", dataset_item_id="item-1", task_output={}),
            score_results=[score],
            trial_id=i,
        )
        for i, score in enumerate(scores)
    ]

    aggregated = calculate_aggregated_statistics(results)

    assert aggregated["reward"].values == [1.0, 0.0]  # the infra trial is absent entirely
    assert aggregated["reward"].mean == 0.5


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


# --- ToolArgsMetric ---------------------------------------------------------------------------


def _min_three_items(args: dict) -> bool:
    tasks = args.get("tasks")
    return isinstance(tasks, list) and len(tasks) >= 3


def test_tool_args_predicate_satisfied_scores_one() -> None:
    result = ToolArgsMetric(
        "todo_write", _min_three_items, description="3+ items", name="todo_3"
    ).score(tool_calls=[{"name": "todo_write", "args": {"tasks": [1, 2, 3]}}])
    _assert_well_formed(result)
    assert result.value == 1.0


def test_tool_args_predicate_unsatisfied_scores_zero() -> None:
    result = ToolArgsMetric(
        "todo_write", _min_three_items, description="3+ items", name="todo_3"
    ).score(tool_calls=[{"name": "todo_write", "args": {"tasks": [1]}}])
    _assert_well_formed(result)
    assert result.value == 0.0


def test_tool_args_matches_a_named_argument() -> None:
    metric = ToolArgsMetric(
        "skill", lambda a: a.get("name") == "greet", description="greet skill", name="skill_greet"
    )
    assert metric.score(tool_calls=[{"name": "skill", "args": {"name": "greet"}}]).value == 1.0
    assert metric.score(tool_calls=[{"name": "skill", "args": {"name": "other"}}]).value == 0.0


def test_tool_args_reads_tool_call_record_shape() -> None:
    # The driver's own ToolCallRecord (name + args attributes) is a valid input shape.
    result = ToolArgsMetric(
        "skill", lambda a: a.get("name") == "greet", description="greet", name="skill_greet"
    ).score(tool_calls=[ToolCallRecord(name="skill", args={"name": "greet"})])
    assert result.value == 1.0


def test_tool_args_decodes_json_string_args() -> None:
    # When the driver records raw JSON string args, the metric decodes them before the predicate.
    result = ToolArgsMetric(
        "skill", lambda a: a.get("name") == "greet", description="greet", name="skill_greet"
    ).score(tool_calls=[{"name": "skill", "args": '{"name": "greet"}'}])
    assert result.value == 1.0


def test_tool_args_tool_not_called_scores_zero() -> None:
    result = ToolArgsMetric("skill", lambda a: True, description="any", name="skill_any").score(
        tool_calls=[{"name": "read", "args": {}}]
    )
    _assert_well_formed(result)
    assert result.value == 0.0


def test_tool_args_missing_field_is_graceful_zero() -> None:
    result = ToolArgsMetric("skill", lambda a: True, description="any", name="skill_any").score()
    _assert_well_formed(result)
    assert result.value == 0.0


def test_tool_args_raising_predicate_is_graceful_zero() -> None:
    # A predicate that trips on a malformed args dict must not crash scoring — it counts as unmet.
    def _boom(args: dict) -> bool:
        raise KeyError("nope")

    result = ToolArgsMetric("skill", _boom, description="explodes", name="skill_boom").score(
        tool_calls=[{"name": "skill", "args": {}}]
    )
    _assert_well_formed(result)
    assert result.value == 0.0


# --- FileEqualsMetric -------------------------------------------------------------------------


def test_file_equals_exact_match_scores_one() -> None:
    result = FileEqualsMetric(path="hello.txt", expected="hi").score(file_state={"hello.txt": "hi"})
    _assert_well_formed(result)
    assert result.value == 1.0


def test_file_equals_trailing_newline_scores_zero() -> None:
    # "exactly 'hi'" is byte-for-byte — a trailing newline is a fail, unlike a line-count diff.
    result = FileEqualsMetric(path="hello.txt", expected="hi").score(
        file_state={"hello.txt": "hi\n"}
    )
    _assert_well_formed(result)
    assert result.value == 0.0


def test_file_equals_absent_file_is_graceful_zero() -> None:
    result = FileEqualsMetric(path="hello.txt", expected="hi").score(file_state={})
    _assert_well_formed(result)
    assert result.value == 0.0


def test_file_equals_missing_field_is_graceful_zero() -> None:
    result = FileEqualsMetric(path="hello.txt", expected="hi").score()
    _assert_well_formed(result)
    assert result.value == 0.0


# --- NewFileNameMetric ------------------------------------------------------------------------


def _new_py_metric() -> NewFileNameMetric:
    return NewFileNameMetric(
        ".py", _prefix_dc, description="dc_ prefix", name="new_py_files_prefixed_dc"
    )


def test_new_file_name_all_created_files_obey_scores_one() -> None:
    result = _new_py_metric().score(
        file_state={"dc_strings.py": "code", "AGENTS.md": "rule", "src/dc_util.py": "x"}
    )
    _assert_well_formed(result)
    assert result.value == 1.0


def test_new_file_name_a_violating_file_scores_zero() -> None:
    # One created .py that breaks the rule fails the whole probe, even if another obeys.
    result = _new_py_metric().score(file_state={"dc_ok.py": "x", "strings.py": "x"})
    _assert_well_formed(result)
    assert result.value == 0.0


def test_new_file_name_no_matching_file_is_graceful_zero() -> None:
    # The agent never created a .py file — the rule cannot be satisfied vacuously.
    result = _new_py_metric().score(file_state={"AGENTS.md": "rule", "notes.txt": "x"})
    _assert_well_formed(result)
    assert result.value == 0.0


def test_new_file_name_missing_field_is_graceful_zero() -> None:
    result = _new_py_metric().score()
    _assert_well_formed(result)
    assert result.value == 0.0


def test_new_file_name_raising_predicate_is_a_violation_not_a_crash() -> None:
    def _boom(name: str) -> bool:
        raise ValueError("nope")

    result = NewFileNameMetric(".py", _boom, description="explodes", name="boom").score(
        file_state={"dc_x.py": "x"}
    )
    _assert_well_formed(result)
    assert result.value == 0.0


# --- JsonSchemaMetric -------------------------------------------------------------------------


def test_json_schema_valid_output_scores_one() -> None:
    result = JsonSchemaMetric(_SampleSchema).score(
        output='{"file": "inventory.py", "issue_count": 2}'
    )
    _assert_well_formed(result)
    assert result.value == 1.0


def test_json_schema_wrong_shape_scores_zero() -> None:
    # Valid JSON, wrong shape (issue_count missing) — IsJson would pass, this must not.
    result = JsonSchemaMetric(_SampleSchema).score(output='{"file": "inventory.py"}')
    _assert_well_formed(result)
    assert result.value == 0.0


def test_json_schema_prose_or_fenced_output_scores_zero() -> None:
    # A ```json fence / trailing prose breaks the raw-JSON contract — the point of the probe.
    result = JsonSchemaMetric(_SampleSchema).score(
        output='```json\n{"file": "inventory.py", "issue_count": 2}\n```'
    )
    _assert_well_formed(result)
    assert result.value == 0.0


def test_json_schema_missing_field_is_graceful_zero() -> None:
    result = JsonSchemaMetric(_SampleSchema).score()
    _assert_well_formed(result)
    assert result.value == 0.0


# --- AnsweredWithoutErrorMetric ----------------------------------------------------------------


def test_answered_without_error_an_answer_and_no_error_scores_one() -> None:
    result = AnsweredWithoutErrorMetric().score(
        output="uname printed arm64.", agent_error=None, infra_error=None
    )
    _assert_well_formed(result)
    assert result.value == 1.0


def test_answered_without_error_a_crashed_run_scores_zero_and_names_the_error() -> None:
    # The mined symptom: a terminal error AND no answer (evaluators/decode_bad_request_400.py).
    result = AnsweredWithoutErrorMetric().score(
        output="", agent_error="Exceeded maximum output retries (3)", infra_error=None
    )
    _assert_well_formed(result)
    assert result.value == 0.0
    assert "Exceeded maximum output retries (3)" in (result.reason or "")


def test_answered_without_error_an_error_with_partial_output_still_scores_zero() -> None:
    result = AnsweredWithoutErrorMetric().score(output="partial", agent_error="boom")
    assert result.value == 0.0


def test_answered_without_error_a_blank_answer_scores_zero() -> None:
    # No crash, but nothing the user can read — the other half of the mined symptom.
    result = AnsweredWithoutErrorMetric().score(output="   ", agent_error=None)
    _assert_well_formed(result)
    assert result.value == 0.0


def test_answered_without_error_missing_output_scores_zero() -> None:
    result = AnsweredWithoutErrorMetric().score()
    _assert_well_formed(result)
    assert result.value == 0.0


def test_answered_without_error_an_infra_failure_is_unscorable_never_a_pass() -> None:
    # A fixture/setup failure means the case never ran: Opik must DROP the score (RewardMetric's
    # rule), not read a None agent_error as a green run.
    result = AnsweredWithoutErrorMetric().score(
        output="", agent_error=None, infra_error="fixture raised: no such file"
    )
    _assert_well_formed(result)
    assert result.scoring_failed is True
    assert "fixture raised: no such file" in (result.reason or "")


# --- ToolArgsNeverMetric -----------------------------------------------------------------------


def _reads_a_missing_path(args: dict) -> bool:
    return args.get("path") not in {"README.md", "notes.txt"}


def test_tool_args_never_no_matching_call_scores_one() -> None:
    result = ToolArgsNeverMetric(
        "read", _reads_a_missing_path, description="a path outside the tree", name="read_exists"
    ).score(tool_calls=[{"name": "read", "args": {"path": "README.md"}}])
    _assert_well_formed(result)
    assert result.value == 1.0


def test_tool_args_never_one_matching_call_scores_zero_and_names_the_args() -> None:
    result = ToolArgsNeverMetric(
        "read", _reads_a_missing_path, description="a path outside the tree", name="read_exists"
    ).score(
        tool_calls=[
            {"name": "read", "args": {"path": "README"}},
            {"name": "read", "args": {"path": "README.md"}},
        ]
    )
    _assert_well_formed(result)
    assert result.value == 0.0
    assert "README" in (result.reason or "")


def test_tool_args_never_tool_not_called_scores_one() -> None:
    result = ToolArgsNeverMetric(
        "read", _reads_a_missing_path, description="a path outside the tree", name="read_exists"
    ).score(tool_calls=[{"name": "glob", "args": {"pattern": "*"}}])
    _assert_well_formed(result)
    assert result.value == 1.0


def test_tool_args_never_missing_field_scores_one() -> None:
    # Nothing recorded means the forbidden call trivially never happened (ToolNotCalledMetric's rule).
    result = ToolArgsNeverMetric(
        "read", _reads_a_missing_path, description="a path outside the tree", name="read_exists"
    ).score()
    _assert_well_formed(result)
    assert result.value == 1.0


def test_tool_args_never_reads_tool_call_record_and_json_string_args() -> None:
    metric = ToolArgsNeverMetric(
        "read", _reads_a_missing_path, description="a path outside the tree", name="read_exists"
    )
    assert (
        metric.score(tool_calls=[ToolCallRecord(name="read", args={"path": "README"})]).value == 0.0
    )
    assert metric.score(tool_calls=[{"name": "read", "args": '{"path": "README"}'}]).value == 0.0


def test_tool_args_never_raising_predicate_is_not_a_violation() -> None:
    def _boom(args: dict) -> bool:
        raise KeyError("nope")

    result = ToolArgsNeverMetric("read", _boom, description="explodes", name="read_boom").score(
        tool_calls=[{"name": "read", "args": {}}]
    )
    _assert_well_formed(result)
    assert result.value == 1.0
