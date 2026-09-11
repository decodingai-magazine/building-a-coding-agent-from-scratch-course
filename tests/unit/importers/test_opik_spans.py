"""The ONE tool-span reader both the Kitaru importer and `evals mine` call (task 163).

Every assertion here is pinned to a REAL Opik span shape: the two anonymised `decode_run` exports
under ``tests/unit/evals/fixtures/traces/`` and the gate-denied probe run recorded live against
``decode-prod`` while implementing task 163 (a deferred call carries
``pydantic_ai.tool.deferral.name = "ApprovalRequired"`` in its span ``input`` and NO result).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from importers.opik_spans import (
    is_tool_span,
    tool_span_arguments,
    tool_span_deferred,
    tool_span_name,
    tool_span_result,
)

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "traces"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_an_execute_tool_span_is_detected_by_provider_evidence_not_by_name() -> None:
    span = next(s for s in load("real_decode_run_1")["spans"] if s["name"] == "execute_tool glob")

    assert is_tool_span(span) is True
    assert tool_span_name(span) == "glob"


def test_an_llm_span_is_not_a_tool_span() -> None:
    span = next(s for s in load("error_usage_limit")["spans"] if s["type"] == "llm")

    assert is_tool_span(span) is False
    assert tool_span_name(span) is None


def test_a_span_named_like_a_tool_without_the_operation_metadata_is_not_a_tool_span() -> None:
    impostor = {"name": "execute_tool read", "type": "general", "metadata": {"logfire.msg": "hi"}}

    assert is_tool_span(impostor) is False


@pytest.mark.parametrize(
    ("fixture", "span_name", "arguments", "result"),
    [
        # The CURRENT instrumentation spelling (live decode-prod, 2026-09).
        ("denied_gate", "execute_tool glob", {"pattern": "*"}, "note.txt\nother.txt"),
        # The OLDER spelling the Kitaru importer was written against (2026-08 sample).
        ("long_run", "execute_tool bash", {"command": "pytest -q"}, "Exit code: 0."),
    ],
)
def test_both_instrumentation_spellings_of_the_payload_are_read(
    fixture: str, span_name: str, arguments: dict[str, Any], result: str
) -> None:
    spans = [s for s in load(fixture)["spans"] if s["name"] == span_name]
    span = next(s for s in spans if not tool_span_deferred(s))

    assert tool_span_arguments(span) == arguments
    assert tool_span_result(span) == result


def test_a_deferred_call_is_flagged_and_carries_no_result() -> None:
    span = next(
        s
        for s in load("denied_gate")["spans"]
        if s["name"] == "execute_tool write" and is_tool_span(s)
    )

    assert tool_span_deferred(span) is True
    assert tool_span_result(span) is None


def test_a_completed_call_is_not_flagged_as_deferred() -> None:
    span = next(s for s in load("real_decode_run_1")["spans"] if s["name"] == "execute_tool read")

    assert tool_span_deferred(span) is False


def test_a_tool_span_whose_message_lost_its_name_degrades_to_none() -> None:
    span = {
        "name": "execute_tool",
        "metadata": {"gen_ai.operation.name": "execute_tool", "logfire.msg": "running tool: "},
    }

    assert is_tool_span(span) is True
    assert tool_span_name(span) is None


def test_the_importer_reads_tool_semantics_through_this_module() -> None:
    """ONE helper, two callers (task 163 AC3): the importer must not keep its own copy."""
    from kitaru.task.importer import NodeType

    from importers.opik_importer import _node_semantics

    span = next(s for s in load("real_decode_run_1")["spans"] if s["name"] == "execute_tool bash")

    assert _node_semantics(span) == (NodeType.TOOL_CALL, "bash")
