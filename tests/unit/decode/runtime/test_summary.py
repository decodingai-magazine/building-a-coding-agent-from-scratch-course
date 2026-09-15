"""Unit tests for the `decode run --summary-json` payload (ADR-0022 §1).

Every helper here is pure over a pydantic-ai message history plus a :class:`ShipResult`, so the
tests are hermetic and keyless: no agent, no sandbox, no network. They pin the JSON contract the
Benchmark Job reads as ground truth — key set, exit reasons, the `handback` shape, and the usage
totals the `request_limit` / `error` paths must still report.
"""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from decode.config.settings import settings
from decode.runtime import summary as summary_module
from decode.runtime.summary import build_summary, summarize_usage, write_summary
from decode.sandbox.handback import ShipResult

# The exact key set the Benchmark Job (task 160) reads — a missing key is a broken contract.
SUMMARY_KEYS = {
    "session_id",
    "kitaru_session_id",
    "exit_reason",
    "error",
    "requests",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "handback",
    "output",
}


def _response(*, input_tokens: int, output_tokens: int, text: str = "ok") -> ModelResponse:
    """A priceable-by-nobody model response with explicit usage (no catalog row, so rates decide)."""
    return ModelResponse(
        parts=[TextPart(content=text)],
        usage=RequestUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        model_name="function:model_fn:",
    )


def _request(text: str = "do it") -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


# --- usage totals ------------------------------------------------------------------------------


def test_summarize_usage_counts_one_request_per_model_response():
    """``requests`` is the number of ``ModelResponse``s — the same source ``_build_record`` uses."""
    messages = [
        _request(),
        _response(input_tokens=10, output_tokens=2),
        _request(),
        _response(input_tokens=30, output_tokens=5),
    ]

    usage = summarize_usage(messages)

    assert usage.requests == 2
    assert usage.input_tokens == 40
    assert usage.output_tokens == 7


def test_summarize_usage_on_a_run_that_never_reached_the_model_is_all_zero():
    """The error-before-the-first-request path: nothing was spent, and the summary says so."""
    usage = summarize_usage([_request()])

    assert (usage.requests, usage.input_tokens, usage.output_tokens) == (0, 0, 0)
    assert usage.cost_usd == 0.0


def test_summarize_usage_tolerates_a_response_with_no_usage():
    """A provider that reported no usage counts as a request with zero tokens, never a crash."""
    usage = summarize_usage([ModelResponse(parts=[ToolCallPart(tool_name="read", args={})])])

    assert usage.requests == 1
    assert usage.input_tokens == 0


# --- cost: carried through from the honesty rule (its three branches live in test_cost.py) -------


def test_summarize_usage_carries_the_configured_rate_cost(monkeypatch):
    """``cost_usd`` is :func:`decode.observability.cost.run_cost_usd` over the same responses."""
    monkeypatch.setattr(settings, "llm_cost_input_usd_per_mtok", 1.0, raising=False)
    monkeypatch.setattr(settings, "llm_cost_output_usd_per_mtok", 2.0, raising=False)

    usage = summarize_usage([_response(input_tokens=1_000_000, output_tokens=500_000)])

    assert usage.cost_usd == pytest.approx(1.0 * 1.0 + 0.5 * 2.0)


def test_summarize_usage_cost_is_null_when_nothing_can_price_the_run():
    """The Modal endpoint's case — a blank cost is the honest answer, never a guess."""
    assert summarize_usage([_response(input_tokens=1000, output_tokens=100)]).cost_usd is None


# --- the summary object ------------------------------------------------------------------------


def test_build_summary_carries_every_key_the_benchmark_reads():
    summary = build_summary(
        session_id="abc-123",
        kitaru_session_id=None,
        exit_reason="completed",
        error=None,
        usage=summarize_usage([_response(input_tokens=10, output_tokens=1)]),
        handback=ShipResult("decode/abc-123", True, "shipped"),
        output="the answer",
    )

    assert set(summary) == SUMMARY_KEYS
    assert summary["session_id"] == "abc-123"
    assert summary["kitaru_session_id"] is None
    assert summary["exit_reason"] == "completed"
    assert summary["error"] is None
    assert summary["requests"] == 1
    assert summary["handback"] == {"branch": "decode/abc-123", "pushed": True}
    assert summary["output"] == "the answer"


def test_build_summary_reports_a_skipped_handback_as_null():
    """A skip (no ``--repo``, ``none`` mode, unchanged Workspace) has no branch to name."""
    for handback in (None, ShipResult(None, False, "nothing to hand back")):
        summary = build_summary(
            session_id="s",
            kitaru_session_id=None,
            exit_reason="completed",
            error=None,
            usage=summarize_usage([]),
            handback=handback,
            output="",
        )

        assert summary["handback"] is None


def test_build_summary_keeps_an_unpushed_branch_with_pushed_false():
    """Never-lose-results: the local branch is named even when the push did not land."""
    summary = build_summary(
        session_id="s",
        kitaru_session_id=None,
        exit_reason="error",
        error="RuntimeError: boom",
        usage=summarize_usage([]),
        handback=ShipResult("decode/s", False, "could not push"),
        output="",
    )

    assert summary["handback"] == {"branch": "decode/s", "pushed": False}
    assert summary["error"] == "RuntimeError: boom"


# --- writing -------------------------------------------------------------------------------------


def test_write_summary_writes_one_json_object_creating_parent_dirs(tmp_path):
    target = tmp_path / "trial" / "agent" / "summary.json"

    write_summary(
        target,
        build_summary(
            session_id="s",
            kitaru_session_id=None,
            exit_reason="request_limit",
            error=None,
            usage=summarize_usage([_response(input_tokens=5, output_tokens=1)]),
            handback=None,
            output="",
        ),
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["exit_reason"] == "request_limit"
    assert payload["requests"] == 1
    # ONE object, not a stream: the whole file parses in a single ``json.loads``.
    assert isinstance(payload, dict)
    # Readable by whoever grades the trial, not just the uid that ran it: the atomic write goes
    # through a 0600 temp file, so the mode a plain write would give has to be restored.
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    # No temp file left next to it — a ``*.json`` glob in a trial dir must find exactly one file.
    assert [entry.name for entry in target.parent.iterdir()] == ["summary.json"]


def test_write_summary_json_is_serialisable_with_a_catalog_price(tmp_path):
    """``ModelResponse.cost()`` returns a ``Decimal``; the summary must carry a plain JSON number."""
    priced = ModelResponse(
        parts=[TextPart(content="ok")],
        usage=RequestUsage(input_tokens=1_000, output_tokens=1_000),
        model_name="gemini-2.5-flash",
        provider_name="google-gla",
    )
    target = tmp_path / "summary.json"

    write_summary(
        target,
        build_summary(
            session_id="s",
            kitaru_session_id=None,
            exit_reason="completed",
            error=None,
            usage=summarize_usage([priced]),
            handback=None,
            output="ok",
        ),
    )

    assert isinstance(json.loads(target.read_text(encoding="utf-8"))["cost_usd"], float)


def test_write_summary_failure_logs_a_warning_and_never_raises(tmp_path, caplog):
    """A write failure must not change a run's exit code — the summary is evidence, not a guard."""
    blocked = tmp_path / "file.txt"
    blocked.write_text("not a directory", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        write_summary(blocked / "summary.json", {"session_id": "s"})

    assert any("summary" in record.message.lower() for record in caplog.records)


def test_write_summary_failure_on_unserialisable_content_never_raises(tmp_path, caplog):
    target = tmp_path / "summary.json"

    with caplog.at_level(logging.WARNING):
        write_summary(target, {"output": Path})  # a type is not JSON

    assert caplog.records


def test_write_summary_never_leaves_a_half_written_file_and_a_rerun_overwrites(
    tmp_path, caplog, mocker
):
    """A crash mid-write must leave the previous summary intact, and a clean re-run must replace it.

    Two runs of one Trial can target the same path; the file a grader reads is ground truth, so it is
    either absent or valid JSON — never a truncated object. The failure is injected at
    :func:`os.replace` because that is the step the atomic write hangs on.
    """
    target = tmp_path / "summary.json"

    def summary(session_id: str) -> dict[str, object]:
        return build_summary(
            session_id=session_id,
            kitaru_session_id=None,
            exit_reason="completed",
            error=None,
            usage=summarize_usage([_response(input_tokens=5, output_tokens=1)]),
            handback=None,
            output="ok",
        )

    write_summary(target, summary("first"))

    mocker.patch.object(summary_module.os, "replace", side_effect=OSError("boom"))
    with caplog.at_level(logging.WARNING):
        write_summary(target, summary("second"))  # must not raise

    # The first run's file survives, whole and parseable — never a truncated one.
    assert json.loads(target.read_text(encoding="utf-8"))["session_id"] == "first"
    assert caplog.records
    # And no temp file is left behind for the harness to trip over.
    assert [entry.name for entry in tmp_path.iterdir()] == ["summary.json"]

    mocker.stopall()
    write_summary(target, summary("second"))

    assert json.loads(target.read_text(encoding="utf-8"))["session_id"] == "second"
    assert [entry.name for entry in tmp_path.iterdir()] == ["summary.json"]
