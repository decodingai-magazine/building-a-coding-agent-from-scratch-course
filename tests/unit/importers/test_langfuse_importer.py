"""Langfuse trace envelope parsing and single-file portability."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from importers.langfuse_importer import parse

IMPORTER = Path(__file__).resolve().parents[3] / "importers" / "langfuse_importer.py"


def _trace(
    trace_id: str = "trace-1",
    *,
    thread_id: str = "session-1",
    spans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "trace": {
            "id": trace_id,
            "thread_id": thread_id,
            "name": "decode run",
            "input": {"prompt": "inspect"},
            "output": {"answer": "done"},
            "start_time": "2026-09-11T09:00:00Z",
            "end_time": "2026-09-11T09:00:01Z",
            "metadata": {"integration": "pydantic-ai"},
        },
        "spans": spans
        if spans is not None
        else [
            {
                "id": "root",
                "name": "agent run",
                "type": "SPAN",
                "start_time": "2026-09-11T09:00:00Z",
                "end_time": "2026-09-11T09:00:01Z",
            },
            {
                "id": "tool",
                "parent_span_id": "root",
                "name": "execute_tool read",
                "type": "SPAN",
                "input": {"gen_ai.tool.call.arguments": {"path": "README.md"}},
                "output": {"gen_ai.tool.call.result": "contents"},
                "metadata": {
                    "gen_ai.operation.name": "execute_tool",
                    "tool_name": "read",
                },
                "start_time": "2026-09-11T09:00:00Z",
                "end_time": "2026-09-11T09:00:01Z",
            },
            {
                "id": "llm",
                "parent_span_id": "root",
                "name": "generate",
                "type": "llm",
                "model": "gemini-3.1-flash-lite",
                "provider": "google",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        ],
    }


def _parse(traces: list[dict[str, Any]]) -> list[Any]:
    payload = json.dumps({"workspace": "team", "project": "decode", "traces": traces})
    return list(parse(payload.encode(), {}))


def test_tool_and_llm_spans_are_imported_as_a_tree() -> None:
    session = _parse([_trace()])[0]

    assert session.external_id == "team/decode/session-1"
    assert session.framework == "pydantic-ai"
    assert session.inputs == {"prompt": "inspect"}
    root = session.nodes[0]
    assert root.name == "agent run"
    tool = next(node for node in root.children if node.tool_name)
    llm = next(node for node in root.children if node.model)
    assert tool.tool_name == "read"
    assert tool.inputs == {"path": "README.md"}
    assert tool.outputs == "contents"
    assert llm.model == "gemini-3.1-flash-lite"
    assert llm.tokens.input_tokens == 10


def test_traces_in_one_thread_become_a_single_ordered_session() -> None:
    first = _trace("trace-1")
    second = _trace("trace-2")
    second["trace"]["start_time"] = "2026-09-11T10:00:00Z"

    [session] = _parse([second, first])

    assert session.metadata["source_trace_ids"] == ["trace-1", "trace-2"]
    assert session.metadata["source_trace_count"] == 2
    assert session.outputs == {"answer": "done"}


def test_trace_without_thread_id_uses_its_trace_id_as_session_key() -> None:
    trace = _trace("trace-1", thread_id="")

    [session] = _parse([trace])

    assert session.external_id == "team/decode/trace-1"
    assert session.metadata["langfuse_thread_id"] is None


def test_missing_workspace_or_project_is_rejected() -> None:
    payload = json.dumps({"traces": [_trace()]}).encode()

    try:
        list(parse(payload, {}))
    except ValueError as exc:
        assert "workspace" in str(exc)
    else:
        raise AssertionError("missing workspace/project was accepted")


def test_unreadable_trace_entry_is_returned_as_an_import_failure() -> None:
    result = _parse([{"spans": []}])[0]

    assert result.error.startswith("unreadable trace entry:")


def test_importer_loads_as_a_single_file_from_a_foreign_cwd(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    code = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('langfuse_importer', {str(IMPORTER)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "assert callable(module.parse)\n"
        "assert callable(module.parser)\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, env=env
    )

    assert result.returncode == 0, result.stderr


def test_importer_uses_only_the_stdlib_and_kitarus_task_contract() -> None:
    tree = ast.parse(IMPORTER.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    third_party = roots - sys.stdlib_module_names - {"__future__"}

    assert third_party <= {"kitaru"}, third_party
