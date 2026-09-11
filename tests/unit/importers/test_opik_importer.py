"""``importers/opik_importer.py``: the ONE tool-span reader (task 163) and its single-file portability.

Two contracts live here. The reader — the importer owns the one definition and ``evals mine``
imports it FROM here (task 165) — with every assertion pinned to a REAL Opik span shape: the two
anonymised `decode_run` exports under ``tests/unit/evals/fixtures/traces/`` and the gate-denied
probe run recorded live against ``decode-prod`` while implementing task 163 (a deferred call carries
``pydantic_ai.tool.deferral.name = "ApprovalRequired"`` in its span ``input`` and NO result).

And portability: a Kitaru Worker uploads this file ALONE and runs it from whatever cwd the Worker
was started in, so a sibling import (``importers.opik_spans``, task 163) resolved only by accident
at the repo root and failed every import job elsewhere — live-reproduced in task 165 QA. The last
two tests pin the fix from both sides: the file loads with the repo nowhere on ``sys.path``, and its
only third-party import is ``kitaru.task.importer`` (which every Worker has).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from importers.opik_importer import (
    is_tool_span,
    tool_span_arguments,
    tool_span_deferred,
    tool_span_name,
    tool_span_result,
)

IMPORTER = Path(__file__).resolve().parents[3] / "importers" / "opik_importer.py"
# Everything a single-file importer may import: the stdlib, plus kitaru's own task contract.
ALLOWED_THIRD_PARTY_ROOTS = frozenset({"kitaru"})

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


@pytest.mark.parametrize(
    ("fixture", "span_name", "arguments", "result"),
    [
        # The CURRENT instrumentation spelling (live decode-prod, 2026-09).
        ("denied_gate", "execute_tool glob", {"pattern": "*"}, "note.txt\nother.txt"),
        # The OLDER spelling the Kitaru importer was written against (2026-08 sample).
        ("long_run", "execute_tool bash", {"command": "pytest -q"}, "Exit code: 0."),
    ],
)
def test_an_imported_tool_node_is_unwrapped_under_either_spelling(
    fixture: str, span_name: str, arguments: dict[str, Any], result: str
) -> None:
    """``_build_node`` reads the payload through the ONE reader, so a current span imports unwrapped.

    Before task 169 it unwrapped only ``tool_arguments`` / ``tool_response``, so a span recorded by
    today's instrumentation landed in Kitaru with the ``gen_ai.tool.call.*`` envelope still on it.
    """
    from importers.opik_importer import _build_node

    spans = [s for s in load(fixture)["spans"] if s["name"] == span_name]
    span = next(s for s in spans if not tool_span_deferred(s))

    node = _build_node(span, [], "workspace", span["trace_id"])

    assert node.inputs == arguments
    assert node.outputs == result


def test_a_deferred_call_imports_with_its_deferral_evidence_intact() -> None:
    """Unwrapping must not cost the node the ONE fact that makes it a denial (task 169).

    A deferred span carries the arguments AND ``pydantic_ai.tool.deferral.name`` in the same
    ``input`` dict, so unwrapping to the arguments alone would drop "this call never ran".
    """
    from importers.opik_importer import _build_node

    span = next(
        s
        for s in load("denied_gate")["spans"]
        if s["name"] == "execute_tool write" and tool_span_deferred(s)
    )

    node = _build_node(span, [], "workspace", span["trace_id"])

    assert node.inputs == span["input"]
    assert node.outputs is None


def test_an_imported_tool_node_keeps_an_unrecognised_payload_whole() -> None:
    """Neither spelling present — the raw dict rides, rather than being dropped."""
    from importers.opik_importer import _build_node

    span = {
        "id": "s-1",
        "name": "execute_tool glob",
        "metadata": {"gen_ai.operation.name": "execute_tool", "logfire.msg": "running tool: glob"},
        "input": {"something_else": 1},
        "output": {"another": 2},
    }

    node = _build_node(span, [], "workspace", "t-1")

    assert node.inputs == {"something_else": 1}
    assert node.outputs == {"another": 2}


def test_the_parser_reads_tool_semantics_through_the_same_reader() -> None:
    """ONE definition, two callers (task 163 AC3): ``parse`` and ``evals mine`` read it the same."""
    from kitaru.task.importer import NodeType

    from importers.opik_importer import _node_semantics

    span = next(s for s in load("real_decode_run_1")["spans"] if s["name"] == "execute_tool bash")

    assert _node_semantics(span) == (NodeType.TOOL_CALL, "bash")


def test_the_importer_loads_as_a_single_file_from_a_foreign_cwd(tmp_path: Path) -> None:
    """The Worker's contract: one uploaded script, an arbitrary cwd, no repo on ``sys.path``.

    A subprocess, not an in-process ``spec_from_file_location``: the repo is importable inside this
    test session, so an in-process load would happily resolve a sibling out of ``sys.modules`` and
    pass while the real Worker fails.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('opik_importer', {str(IMPORTER)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "assert callable(module.parse), module.parse\n"
        "assert callable(module.parser), module.parser\n"
        "assert callable(module.is_tool_span), module.is_tool_span\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, env=env
    )

    assert result.returncode == 0, result.stderr


def test_the_importer_imports_nothing_but_the_stdlib_and_kitarus_task_contract() -> None:
    """The contract behind the test above, named at the source: no sibling module, ever."""
    tree = ast.parse(IMPORTER.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    third_party = roots - sys.stdlib_module_names - {"__future__"}

    assert third_party <= ALLOWED_THIRD_PARTY_ROOTS, third_party
