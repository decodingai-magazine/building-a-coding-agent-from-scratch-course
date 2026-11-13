"""Kitaru importer for Langfuse trace exports (provider: ``langfuse``)."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

from kitaru.task.importer import (
    ImportedNode,
    ImportedSession,
    ImportFailure,
    NodeStatus,
    NodeType,
    Parser,
    SessionStatus,
    TokenUsage,
)

MAX_PAYLOAD_BYTES = 50 * 1024 * 1024
MAX_TRACE_RECORDS = 100_000


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _enc(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _aware(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def _thread_key(trace: dict[str, Any]) -> str | None:
    value = trace.get("thread_id")
    if isinstance(value, str) and value:
        return value
    value = _mapping(trace.get("metadata")).get("thread_id")
    return value if isinstance(value, str) and value else None


def _error_text(error: object) -> str | None:
    if isinstance(error, dict):
        message = error.get("message") or error.get("status_message")
        kind = error.get("exception_type")
        text = ": ".join(str(part) for part in (kind, message) if part)
        return text or None
    return str(error) if error else None


def _tokens(span: dict[str, Any]) -> TokenUsage | None:
    usage = _mapping(span.get("usage"))
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if prompt is None and completion is None:
        return None
    return TokenUsage(
        input_tokens=prompt if isinstance(prompt, int) else None,
        output_tokens=completion if isinstance(completion, int) else None,
        cached_input_tokens=usage.get("details.cached_content_tokens")
        if isinstance(usage.get("details.cached_content_tokens"), int)
        else None,
        reasoning_tokens=usage.get("details.thoughts_tokens")
        if isinstance(usage.get("details.thoughts_tokens"), int)
        else None,
    )


def _tool_name(span: dict[str, Any]) -> str | None:
    metadata = _mapping(span.get("metadata"))
    value = metadata.get("tool_name")
    if isinstance(value, str) and value:
        return value
    name = span.get("name")
    if isinstance(name, str) and name.startswith("execute_tool "):
        return name.removeprefix("execute_tool ").strip() or None
    message = metadata.get("logfire.msg")
    if isinstance(message, str) and message.startswith("running tool: "):
        return message.removeprefix("running tool: ").strip() or None
    return None


def _node(
    span: dict[str, Any],
    children: list[ImportedNode],
    instance: str,
    trace_id: str,
) -> ImportedNode:
    metadata = _mapping(span.get("metadata"))
    node_type = (
        NodeType.LLM_CALL
        if span.get("type") == "llm"
        else (NodeType.TOOL_CALL if _tool_name(span) else NodeType.SPAN)
    )
    inputs = span.get("input")
    outputs = span.get("output")
    if node_type is NodeType.TOOL_CALL:
        arguments = _mapping(inputs).get("gen_ai.tool.call.arguments")
        if arguments is not None:
            inputs = arguments
        result = _mapping(outputs).get("gen_ai.tool.call.result")
        if result is not None:
            outputs = result
    usage = span.get("usage")
    cost_value = span.get("total_estimated_cost")
    try:
        cost = Decimal(str(cost_value)) if cost_value is not None else None
    except ArithmeticError:
        cost = None
    error = _error_text(span.get("error_info"))
    return ImportedNode(
        external_id=f"{instance}/{_enc(trace_id)}/{_enc(str(span.get('id', 'unknown')))}",
        trace_id=trace_id,
        node_type=node_type,
        name=str(span.get("name") or node_type.value),
        status=NodeStatus.FAILED if error else NodeStatus.COMPLETED,
        error=error,
        started_at=_aware(span.get("start_time")),
        ended_at=_aware(span.get("end_time")),
        inputs=inputs,
        outputs=outputs,
        attributes={},
        metadata={
            key: metadata[key]
            for key in ("integration", "gen_ai.operation.name", "tool_name", "model_name")
            if metadata.get(key) is not None
        },
        model=span.get("model") if isinstance(span.get("model"), str) else None,
        requested_model=metadata.get("model_name"),
        model_provider=span.get("provider") if isinstance(span.get("provider"), str) else None,
        tokens=_tokens({"usage": usage}) if node_type is NodeType.LLM_CALL else None,
        cost=cost if node_type is NodeType.LLM_CALL else None,
        tool_name=_tool_name(span),
        children=children,
    )


def _roots(entry: dict[str, Any], instance: str) -> list[ImportedNode]:
    trace = _mapping(entry.get("trace"))
    trace_id = str(trace.get("id") or "")
    spans = [_mapping(span) for span in (entry.get("spans") or [])]
    by_id = {str(span.get("id")): span for span in spans if span.get("id") is not None}
    children: dict[str | None, list[str]] = {}
    for span_id, span in by_id.items():
        parent = span.get("parent_span_id")
        parent_id = str(parent) if parent and str(parent) in by_id else None
        children.setdefault(parent_id, []).append(span_id)

    def build(span_id: str, seen: frozenset[str]) -> ImportedNode:
        if span_id in seen:
            raise ValueError(f"parent cycle in trace {trace_id}")
        child_nodes = [
            build(child, seen | {span_id}) for child in sorted(children.get(span_id, []))
        ]
        return _node(by_id[span_id], child_nodes, instance, trace_id)

    return [build(root, frozenset()) for root in sorted(children.get(None, []))]


def _session(
    entries: list[dict[str, Any]],
    key: str,
    workspace: str,
    project: str,
) -> ImportedSession:
    instance = f"{_enc(workspace)}/{_enc(project)}"
    entries.sort(
        key=lambda entry: (
            str(_mapping(entry.get("trace")).get("start_time") or ""),
            str(_mapping(entry.get("trace")).get("id") or ""),
        )
    )
    roots: list[ImportedNode] = []
    for entry in entries:
        roots.extend(_roots(entry, instance))
    first = _mapping(entries[0].get("trace"))
    last = _mapping(entries[-1].get("trace"))
    error = _error_text(last.get("error_info"))
    canonical = json.dumps(
        {
            "key": key,
            "traces": [
                {
                    "id": _mapping(entry.get("trace")).get("id"),
                    "span_ids": sorted(str(span.get("id")) for span in entry.get("spans", [])),
                }
                for entry in entries
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    metadata = _mapping(first.get("metadata"))
    return ImportedSession(
        external_id=f"{instance}/{_enc(key)}",
        status=SessionStatus.FAILED if error else SessionStatus.COMPLETED,
        name=str(first.get("name") or key),
        inputs=first.get("input"),
        outputs=last.get("output"),
        error=error,
        started_at=_aware(first.get("start_time")),
        ended_at=_aware(last.get("end_time")),
        framework="pydantic-ai" if metadata.get("integration") == "pydantic-ai" else None,
        metadata={
            "langfuse_workspace": workspace,
            "langfuse_project": project,
            "langfuse_thread_id": key if _thread_key(first) else None,
            "source_trace_ids": [_mapping(entry.get("trace")).get("id") for entry in entries],
            "source_trace_count": len(entries),
            "source_content_digest": digest,
        },
        nodes=roots,
    )


def parse(payload: bytes, params: dict[str, Any]) -> Iterator[ImportedSession | ImportFailure]:
    """Parse one normalized Langfuse trace envelope into Kitaru sessions."""
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    document = json.loads(payload)
    if not isinstance(document, dict) or not isinstance(document.get("traces"), list):
        raise ValueError('expected a JSON object with a "traces" array')
    if len(document["traces"]) > MAX_TRACE_RECORDS:
        raise ValueError(f"more than {MAX_TRACE_RECORDS} trace records")
    workspace = str(params.get("source_instance") or document.get("workspace") or "")
    project = str(document.get("project") or "")
    if not workspace or not project:
        raise ValueError("workspace (or params.source_instance) and project are required")

    groups: dict[str, list[dict[str, Any]]] = {}
    for line, entry in enumerate(document["traces"]):
        try:
            trace = _mapping(entry["trace"])
            key = _thread_key(trace) or str(trace["id"])
            groups.setdefault(key, []).append(entry)
        except (KeyError, TypeError) as exc:
            yield ImportFailure(
                line=line, external_id=None, error=f"unreadable trace entry: {exc!r}"
            )
    for key, entries in groups.items():
        external_id = f"{_enc(workspace)}/{_enc(project)}/{_enc(key)}"
        try:
            yield _session(entries, key, workspace, project)
        except (ValueError, KeyError, TypeError) as exc:
            yield ImportFailure(line=0, external_id=external_id, error=str(exc))


parser: Parser = parse
