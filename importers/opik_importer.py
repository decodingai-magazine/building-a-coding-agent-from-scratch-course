"""Kitaru importer for Opik trace exports (provider: ``opik``).

Consumes ONE static JSON payload produced by a separate acquisition step (the Opik REST export;
see ``scripts/`` once wired) — the parser itself performs no network calls, filesystem writes,
subprocesses, or credential reads:

```json
{
  "schema_version": 1,
  "workspace": "default",
  "project": "decode-local",
  "traces": [{"trace": {...opik trace...}, "spans": [{...opik span...}, ...]}]
}
```

Mapping (evidence: decode's logfire→OTLP→Opik spans, 2026-08 sample):

- ``llm`` spans → ``llm_call`` (model, provider, usage→TokenUsage, total_estimated_cost→cost);
- ``general`` spans with metadata ``gen_ai.operation.name == "execute_tool"`` → ``tool_call``
  (tool name parsed from ``logfire.msg`` "running tool: <name>", inputs=tool_arguments,
  outputs=tool_response);
- every other span → ``span``.

Sessions join per-turn traces on ``thread_id`` (trace column, falling back to trace metadata);
a trace with no thread key becomes a single-turn session keyed by its trace id. Turn order is
``trace.start_time`` within one Opik project (one clock domain), trace id as tie-break. Session
status derives from the LAST ordered turn root: ``error_info`` present → failed, else completed.

Identity: ``external_id = enc(workspace/project)/enc(thread-or-trace-id)`` with URI
percent-encoding per component; nodes add ``/enc(span-id)``. Registered provider ``opik``
completes the deduplication key. A stable content digest + ``source_trace_count`` land in
session metadata so a stale-duplicate (grown conversation) can be detected before a re-import.
"""

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
_TOOL_MSG_PREFIX = "running tool: "


def _enc(component: str) -> str:
    """One URI path-segment encoding for every identity component (collision-free join)."""
    return urllib.parse.quote(component, safe="")


def _aware(value: object) -> datetime | None:
    """Parse an Opik timestamp string into an aware datetime; ``None`` when absent/naive."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _thread_key(trace: dict[str, Any]) -> str | None:
    key = trace.get("thread_id")
    if isinstance(key, str) and key:
        return key
    metadata = trace.get("metadata")
    if isinstance(metadata, dict):
        key = metadata.get("thread_id")
        if isinstance(key, str) and key:
            return key
    return None


def _usage_to_tokens(usage: object) -> TokenUsage | None:
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    reasoning = usage.get("details.thoughts_tokens")
    cached = usage.get("details.cached_content_tokens")
    if prompt is None and completion is None:
        return None
    return TokenUsage(
        input_tokens=prompt if isinstance(prompt, int) else None,
        output_tokens=completion if isinstance(completion, int) else None,
        cached_input_tokens=cached if isinstance(cached, int) else None,
        reasoning_tokens=reasoning if isinstance(reasoning, int) else None,
    )


def _cost(span: dict[str, Any]) -> Decimal | None:
    raw = span.get("total_estimated_cost")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except ArithmeticError:
        return None


def _node_semantics(span: dict[str, Any]) -> tuple[NodeType, str | None]:
    """(node type, tool name) from explicit provider evidence, never from names alone."""
    if span.get("type") == "llm":
        return NodeType.LLM_CALL, None
    metadata = span.get("metadata") or {}
    if isinstance(metadata, dict) and metadata.get("gen_ai.operation.name") == "execute_tool":
        msg = metadata.get("logfire.msg")
        tool = None
        if isinstance(msg, str) and msg.startswith(_TOOL_MSG_PREFIX):
            tool = msg[len(_TOOL_MSG_PREFIX) :] or None
        return NodeType.TOOL_CALL, tool
    return NodeType.SPAN, None


def _bounded_metadata(span: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted provider metadata — provenance keys only, never the whole record."""
    metadata = span.get("metadata") or {}
    if not isinstance(metadata, dict):
        return {}
    keep = ("integration", "gen_ai.operation.name", "agent_name", "call.id", "model_name")
    return {k: metadata[k] for k in keep if metadata.get(k) is not None}


def _build_node(
    span: dict[str, Any],
    children: list[ImportedNode],
    instance: str,
    trace_id: str,
) -> ImportedNode:
    node_type, tool_name = _node_semantics(span)
    metadata = span.get("metadata") or {}
    model = span.get("model") if isinstance(span.get("model"), str) else None
    inputs = span.get("input")
    outputs = span.get("output")
    if node_type is NodeType.TOOL_CALL and isinstance(inputs, dict):
        inputs = inputs.get("tool_arguments", inputs)
    if node_type is NodeType.TOOL_CALL and isinstance(outputs, dict):
        outputs = outputs.get("tool_response", outputs)
    return ImportedNode(
        external_id=f"{instance}/{_enc(trace_id)}/{_enc(str(span['id']))}",
        trace_id=trace_id,
        node_type=node_type,
        name=str(span.get("name") or node_type.value),
        status=NodeStatus.FAILED if span.get("error_info") else NodeStatus.COMPLETED,
        error=_error_text(span.get("error_info")),
        started_at=_aware(span.get("start_time")),
        ended_at=_aware(span.get("end_time")),
        inputs=inputs,
        outputs=outputs,
        attributes={},
        metadata=_bounded_metadata(span),
        model=model,
        requested_model=(
            metadata.get("model_name")
            if isinstance(metadata, dict) and node_type is NodeType.LLM_CALL
            else None
        ),
        model_provider=span.get("provider") if isinstance(span.get("provider"), str) else None,
        tokens=_usage_to_tokens(span.get("usage")) if node_type is NodeType.LLM_CALL else None,
        cost=_cost(span) if node_type is NodeType.LLM_CALL else None,
        tool_name=tool_name,
        children=children,
    )


def _error_text(error_info: object) -> str | None:
    if not isinstance(error_info, dict):
        return None
    kind = error_info.get("exception_type")
    message = error_info.get("message")
    text = ": ".join(str(part) for part in (kind, message) if part)
    return text or None


def _trace_trees(
    entry: dict[str, Any], instance: str, warnings: list[str]
) -> tuple[list[ImportedNode], bool]:
    """(root node trees, graph_complete) for one trace; missing parents promote to roots."""
    trace = entry["trace"]
    trace_id = str(trace["id"])
    spans = entry.get("spans") or []
    by_id: dict[str, dict[str, Any]] = {}
    for span in spans:
        span_id = str(span["id"])
        existing = by_id.get(span_id)
        if existing is not None and existing != span:
            raise ValueError(f"conflicting duplicate span identity {span_id} in trace {trace_id}")
        by_id[span_id] = span
    children_ids: dict[str | None, list[str]] = {}
    graph_complete = True
    for span_id, span in by_id.items():
        parent = span.get("parent_span_id")
        parent_key = str(parent) if isinstance(parent, str) and parent in by_id else None
        if isinstance(parent, str) and parent not in by_id:
            graph_complete = False
            warnings.append(f"trace {trace_id}: span {span_id} parent missing; promoted to root")
        children_ids.setdefault(parent_key, []).append(span_id)

    def sort_key(span_id: str) -> tuple[str, str]:
        span = by_id[span_id]
        return (str(span.get("start_time") or ""), span_id)

    def build(span_id: str, seen: frozenset[str]) -> ImportedNode:
        if span_id in seen:
            raise ValueError(f"parent cycle at span {span_id} in trace {trace_id}")
        kids = [
            build(c, seen | {span_id}) for c in sorted(children_ids.get(span_id, []), key=sort_key)
        ]
        return _build_node(by_id[span_id], kids, instance, trace_id)

    root_ids = sorted(children_ids.get(None, []), key=sort_key)
    if by_id and not root_ids:
        raise ValueError(f"trace {trace_id} has spans but no root")
    if len(root_ids) > 1:
        warnings.append(f"trace {trace_id}: {len(root_ids)} roots preserved")
    return [build(r, frozenset()) for r in root_ids], graph_complete


def _digest(instance: str, session_key: str, turns: list[dict[str, Any]]) -> str:
    projection = {"instance": instance, "session": session_key, "turns": turns}
    canonical = json.dumps(projection, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_readiness(
    roots: list[ImportedNode], graph_complete: bool, root_inputs: bool
) -> dict[str, Any]:
    def walk(nodes: list[ImportedNode]) -> Iterator[ImportedNode]:
        for node in nodes:
            yield node
            yield from walk(node.children or [])

    tools = [n for n in walk(roots) if n.node_type is NodeType.TOOL_CALL]
    replayable = [
        n for n in tools if n.tool_name and n.inputs is not None and n.outputs is not None
    ]
    reasons: list[str] = []
    if not root_inputs:
        reasons.append("session root inputs missing from export")
    if not graph_complete:
        reasons.append("span graph incomplete (missing parents)")
    if len(replayable) < len(tools):
        reasons.append("some tool calls lack name, inputs, or outputs (logfire scrubbing)")
    level = "unavailable" if not root_inputs else ("ready" if not reasons else "partial")
    return {
        "level": level,
        "root_inputs_available": root_inputs,
        "graph_complete": graph_complete,
        "tool_call_count": len(tools),
        "replayable_tool_call_count": len(replayable),
        "tool_activity_observable": len(replayable) == len(tools),
        "reasons": reasons,
    }


def parse(payload: bytes, params: dict[str, Any]) -> Iterator[ImportedSession | ImportFailure]:
    """Parse one Opik export payload into Kitaru sessions or isolated failures."""
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    document = json.loads(payload)
    if not isinstance(document, dict) or not isinstance(document.get("traces"), list):
        raise ValueError('expected a JSON object with a "traces" array')
    workspace = str(params.get("source_instance") or document.get("workspace") or "")
    project = str(document.get("project") or "")
    if not workspace or not project:
        raise ValueError("workspace (or params.source_instance) and project are required")
    if len(document["traces"]) > MAX_TRACE_RECORDS:
        raise ValueError(f"more than {MAX_TRACE_RECORDS} trace records")
    instance = f"{_enc(workspace)}/{_enc(project)}"

    groups: dict[str, list[dict[str, Any]]] = {}
    for line, entry in enumerate(document["traces"]):
        try:
            trace = entry["trace"]
            key = _thread_key(trace) or str(trace["id"])
        except (KeyError, TypeError) as exc:
            yield ImportFailure(
                line=line, external_id=None, error=f"unreadable trace entry: {exc!r}"
            )
            continue
        groups.setdefault(key, []).append(entry)

    for key in sorted(groups):
        entries = sorted(
            groups[key],
            key=lambda e: (str(e["trace"].get("start_time") or ""), str(e["trace"]["id"])),
        )
        external_id = f"{instance}/{_enc(key)}"
        try:
            yield _session(entries, key, instance, external_id, workspace, project)
        except (ValueError, KeyError, TypeError) as exc:
            yield ImportFailure(line=0, external_id=external_id, error=str(exc))


def _session(
    entries: list[dict[str, Any]],
    key: str,
    instance: str,
    external_id: str,
    workspace: str,
    project: str,
) -> ImportedSession:
    warnings: list[str] = []
    all_roots: list[ImportedNode] = []
    graph_complete = True
    digest_turns: list[dict[str, Any]] = []
    for entry in entries:
        roots, complete = _trace_trees(entry, instance, warnings)
        all_roots.extend(roots)
        graph_complete = graph_complete and complete
        digest_turns.append(
            {
                "trace_id": str(entry["trace"]["id"]),
                "root_ids": [r.external_id for r in roots],
                "span_ids": sorted(str(s["id"]) for s in entry.get("spans") or []),
                "status": "failed" if entry["trace"].get("error_info") else "completed",
            }
        )
    first, last = entries[0]["trace"], entries[-1]["trace"]
    error = _error_text(last.get("error_info"))
    inputs = first.get("input")
    metadata_integration = (
        (first.get("metadata") or {}).get("integration")
        if isinstance(first.get("metadata"), dict)
        else None
    )
    readiness = _replay_readiness(all_roots, graph_complete, inputs is not None)
    return ImportedSession(
        external_id=external_id,
        status=SessionStatus.FAILED if error else SessionStatus.COMPLETED,
        name=str(first.get("name") or key),
        inputs=inputs,
        outputs=last.get("output"),
        error=error,
        started_at=_aware(first.get("start_time")),
        ended_at=_aware(last.get("end_time")),
        framework="pydantic-ai" if metadata_integration == "pydantic-ai" else None,
        metadata={
            "opik_workspace": workspace,
            "opik_project": project,
            "opik_thread_id": key if _thread_key(first) else None,
            "source_trace_ids": [t["trace_id"] for t in digest_turns],
            "source_trace_count": len(entries),
            "source_completeness": "query-dependent",
            "normalization_warnings": warnings,
            "replay_readiness": readiness,
            "source_content_digest": _digest(instance, key, digest_turns),
        },
        nodes=all_roots,
    )


parser: Parser = parse
