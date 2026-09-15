"""`evals mine` — the trace-triage half of regression mining (task 163).

Every fixture under ``tests/unit/evals/fixtures/traces/`` is a real Opik export shape: two
anonymised live ``decode_run`` traces (pre-156, so no ``git_sha``/``model`` metadata — the ``-``
path) plus hand-made error / denied / long / chat-only cases built from shapes observed live on
``decode-prod``. The Opik client is a fake; every rule under test is pure over plain dicts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evals.harness.mine import (
    DENIED_TOOL_CALL_BAR,
    MISSING,
    PRESETS,
    MineError,
    Signature,
    TraceSource,
    build_hit,
    compose_filter,
    denied_tool_calls,
    error_summary,
    group_hits,
    groups_as_json,
    last_tool_name,
    metadata_value,
    mine,
    mine_preset,
    render_group_table,
    since_clause,
    top_decile,
    total_tokens,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "traces"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def trace_of(name: str) -> dict[str, Any]:
    return load(name)["trace"]


def spans_of(name: str) -> list[dict[str, Any]]:
    return load(name)["spans"]


class FakeOpik:
    """The Opik surface ``TraceSource`` touches: ``search_traces`` + ``search_spans``."""

    def __init__(
        self, entries: list[dict[str, Any]], *, filtered: dict[str, list[str]] | None = None
    ):
        self.entries = {str(entry["trace"]["id"]): entry for entry in entries}
        self.filtered = filtered or {}
        self.trace_queries: list[tuple[str | None, int]] = []
        self.span_queries: list[str] = []

    def search_traces(
        self, *, project_name: str, filter_string: str | None, max_results: int
    ) -> list[dict[str, Any]]:
        self.trace_queries.append((filter_string, max_results))
        wanted = self.filtered.get(filter_string or "", list(self.entries))
        return [self.entries[key]["trace"] for key in wanted][:max_results]

    def search_spans(
        self, *, project_name: str, trace_id: str, max_results: int
    ) -> list[dict[str, Any]]:
        self.span_queries.append(trace_id)
        return self.entries[trace_id]["spans"]


def source_over(*names: str, filtered: dict[str, list[str]] | None = None) -> TraceSource:
    client = FakeOpik([load(name) for name in names], filtered=filtered)
    return TraceSource(client=client, project="decode-prod")


# --- the pure rules ----------------------------------------------------------------------------


def test_the_error_summary_prefers_the_exception_type_because_that_is_what_clusters() -> None:
    assert (
        error_summary(trace_of("error_usage_limit")) == "pydantic_ai.exceptions.UsageLimitExceeded"
    )


def test_an_error_without_a_type_falls_back_to_the_messages_first_line_only() -> None:
    trace = {"error_info": {"message": "boom happened\nsecond line\nthird line"}}

    assert error_summary(trace) == "boom happened"


def test_a_clean_trace_has_no_error_summary() -> None:
    assert error_summary(trace_of("real_decode_run_1")) is None


def test_the_last_tool_is_read_in_start_time_order_not_in_the_order_opik_returned() -> None:
    # The fixture deliberately lists the LATER `read` span before the earlier `glob` span.
    assert last_tool_name(spans_of("error_usage_limit")) == "read"
    # ...and this one lists the later `bash` before the earlier `edit`.
    assert last_tool_name(spans_of("long_run")) == "bash"


def test_a_run_with_no_tool_spans_has_no_last_tool() -> None:
    assert last_tool_name(spans_of("quiet_run")) is None


def test_a_denied_call_is_a_deferred_leg_with_no_completed_twin() -> None:
    # write + bash were denied; glob deferred then completed (approved) — exactly the live probe.
    assert denied_tool_calls(spans_of("denied_gate")) == 2


def test_a_run_where_every_gated_call_was_approved_reports_no_denials() -> None:
    assert denied_tool_calls(spans_of("real_decode_run_1")) == 0


def test_the_same_tool_called_twice_and_approved_once_reports_exactly_one_denial() -> None:
    def leg(arguments: dict[str, Any], *, deferred: bool) -> dict[str, Any]:
        span: dict[str, Any] = {
            "name": "execute_tool read",
            "metadata": {
                "gen_ai.operation.name": "execute_tool",
                "logfire.msg": "running tool: read",
            },
            "input": {"gen_ai.tool.call.arguments": arguments},
        }
        if deferred:
            span["input"]["pydantic_ai.tool.deferral.name"] = "ApprovalRequired"
        else:
            span["output"] = {"gen_ai.tool.call.result": "ok"}
        return span

    spans = [
        leg({"path": "a.txt"}, deferred=True),
        leg({"path": "a.txt"}, deferred=False),
        leg({"path": "b.txt"}, deferred=True),
    ]

    assert denied_tool_calls(spans) == 1


def test_the_top_decile_of_a_small_window_is_still_the_single_longest_run() -> None:
    window = [trace_of(name) for name in ("real_decode_run_1", "long_run", "denied_gate")]

    assert [trace["id"] for trace in top_decile(window)] == [trace_of("long_run")["id"]]


def test_a_trace_with_no_usage_is_never_ranked_as_long() -> None:
    assert total_tokens(trace_of("quiet_run")) is None
    assert top_decile([trace_of("quiet_run")]) == []


def test_a_window_with_no_usage_at_all_yields_no_long_traces() -> None:
    assert top_decile([]) == []


def test_ties_break_on_trace_id_so_the_same_window_always_returns_the_same_list() -> None:
    window = [
        {"id": "b", "usage": {"total_tokens": 10}},
        {"id": "a", "usage": {"total_tokens": 10}},
    ]

    assert [trace["id"] for trace in top_decile(window)] == ["a"]


def test_the_task_156_join_fields_are_read_from_trace_metadata() -> None:
    trace = trace_of("long_run")

    assert metadata_value(trace, "model") == "gemini-2.5-flash"
    assert metadata_value(trace, "git_sha") == "5c46f38b5f56b39606a454b704b79d50a7500000"


def test_a_pre_156_trace_has_no_join_fields() -> None:
    trace = trace_of("real_decode_run_1")

    assert metadata_value(trace, "model") is None
    assert metadata_value(trace, "git_sha") is None


# --- signatures + grouping ---------------------------------------------------------------------


def test_a_signature_is_preset_error_last_tool_and_model() -> None:
    hit = build_hit("errors", trace_of("error_usage_limit"), spans_of("error_usage_limit"))

    assert hit.signature == Signature(
        preset="errors",
        error="pydantic_ai.exceptions.UsageLimitExceeded",
        last_tool="read",
        model="gemini-2.5-flash",
    )
    assert hit.signature.label == (
        "errors | pydantic_ai.exceptions.UsageLimitExceeded | read | gemini-2.5-flash"
    )


def test_a_pre_156_hit_renders_its_missing_metadata_as_a_dash_but_keeps_json_null() -> None:
    hit = build_hit("long", trace_of("real_decode_run_1"), spans_of("real_decode_run_1"))

    assert hit.signature.label.endswith(f"| {MISSING}")
    assert hit.as_json()["model"] is None
    assert hit.as_json()["git_sha"] is None


def test_hits_cluster_by_signature_biggest_group_first() -> None:
    same = [
        build_hit("errors", trace_of("error_usage_limit"), spans_of("error_usage_limit")),
        build_hit("errors", trace_of("error_usage_limit"), spans_of("error_usage_limit")),
    ]
    other = build_hit("long", trace_of("long_run"), spans_of("long_run"))

    groups = group_hits([other, *same])

    assert [group.count for group in groups] == [2, 1]
    assert groups[0].signature.preset == "errors"
    assert groups[0].first_seen == datetime(2026, 9, 10, 22, 38, 50, 211000, tzinfo=UTC)
    assert groups[0].last_seen == groups[0].first_seen


def test_the_json_document_carries_every_trace_in_a_group_not_just_the_five_a_table_shows() -> None:
    hits = [
        build_hit("errors", {**trace_of("error_usage_limit"), "id": f"trace-{index}"}, [])
        for index in range(7)
    ]

    document = json.loads(groups_as_json(group_hits(hits)))

    assert len(document) == 1
    assert document[0]["count"] == 7
    assert len(document[0]["traces"]) == 7
    assert set(document[0]["traces"][0]) == {
        "id",
        "thread_id",
        "start_time",
        "git_sha",
        "model",
        "error",
    }


def test_a_table_shows_at_most_five_trace_ids_and_says_how_many_it_hid() -> None:
    hits = [
        build_hit("errors", {**trace_of("error_usage_limit"), "id": f"trace-{index}"}, [])
        for index in range(7)
    ]

    table = render_group_table(group_hits(hits)[0])

    assert table.row_count == 5
    assert "7 trace(s)" in str(table.caption)
    assert "2 more" in str(table.caption)


# --- filters -------------------------------------------------------------------------------------


def test_every_preset_composes_the_oql_the_live_backend_accepts() -> None:
    assert compose_filter("errors", None) == "error_info is_not_empty"
    assert compose_filter("low-quality", None) == "feedback_scores.response_quality < 5"
    assert compose_filter("long", None) is None
    assert compose_filter("denied", None) is None


def test_since_narrows_every_preset_to_one_window() -> None:
    assert compose_filter("errors", "2026-09-04T00:00:00+00:00") == (
        'error_info is_not_empty AND start_time > "2026-09-04T00:00:00Z"'
    )
    assert compose_filter("denied", "2026-09-04T00:00:00Z") == (
        'start_time > "2026-09-04T00:00:00Z"'
    )


def test_a_naive_since_is_refused_rather_than_assumed_to_be_utc() -> None:
    with pytest.raises(MineError, match="timezone-aware"):
        since_clause("2026-09-04T00:00:00")


def test_an_unknown_preset_is_refused_before_any_request() -> None:
    with pytest.raises(MineError, match="unknown preset"):
        compose_filter("flaky", None)


# --- the run -----------------------------------------------------------------------------------


def test_the_errors_preset_asks_opik_for_exactly_the_error_traces() -> None:
    source = source_over("error_usage_limit", "real_decode_run_1")
    source.client.filtered = {"error_info is_not_empty": [trace_of("error_usage_limit")["id"]]}

    hits = mine_preset(source, "errors", limit=20)

    assert source.client.trace_queries == [("error_info is_not_empty", 20)]
    assert [hit.id for hit in hits] == [trace_of("error_usage_limit")["id"]]


def test_the_denied_preset_keeps_only_runs_over_the_denial_bar() -> None:
    source = source_over("denied_gate", "real_decode_run_1")

    hits = mine_preset(source, "denied", limit=20)

    assert DENIED_TOOL_CALL_BAR == 2
    assert [hit.id for hit in hits] == [trace_of("denied_gate")["id"]]


def test_a_trace_opik_says_has_no_tool_spans_is_never_queried_for_spans() -> None:
    source = source_over("quiet_run")

    mine_preset(source, "errors", limit=20)

    assert source.client.span_queries == []


def test_preset_all_pays_for_each_traces_spans_once_and_can_report_it_twice() -> None:
    source = source_over("error_usage_limit")

    groups = mine(source, preset="all", limit=20)

    # The fake returns the one trace for every query, so it reports under the three presets whose
    # client-side rule it passes — and NOT under `denied`, which it fails (no denied calls).
    assert sorted(group.signature.preset for group in groups) == ["errors", "long", "low-quality"]
    # One span query for the one trace, even though four presets looked at it.
    assert source.client.span_queries == [trace_of("error_usage_limit")["id"]]


def test_mine_all_runs_every_preset_once() -> None:
    source = source_over("error_usage_limit")

    mine(source, preset="all", limit=20)

    assert [query[0] for query in source.client.trace_queries] == [
        compose_filter(preset, None) for preset in PRESETS
    ]


def test_the_console_is_wide_enough_that_a_trace_id_is_never_ellipsised() -> None:
    """Ids are the product of this command; Rich falls back to 80 columns when piped."""
    from evals.harness.mine import TABLE_MIN_WIDTH, mine_console

    assert mine_console().width >= TABLE_MIN_WIDTH
    # Two full uuids (36 each) + started/git_sha/model content, + Rich's padding and borders.
    assert TABLE_MIN_WIDTH >= 2 * 36 + 20 + 12 + 16 + 16
