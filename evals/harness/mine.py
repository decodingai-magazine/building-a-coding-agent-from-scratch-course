"""Trace Mining: search Langfuse observations, cluster hits, and print trace ids.

The discovery half of the mining loop (the glossary's Trace Mining). ``evals mine`` never writes
a case (that is task 164): it
answers "what went wrong in production lately, and which of it is the SAME thing?" — three presets
over the live project, each hit reduced to a **Signature**, one Rich table per signature with the
trace ids a human then picks from.

Three presets, ONE table (:data:`PRESET_FILTERS`), evaluated locally over Langfuse observations:

* ``errors`` — traces with an error-level observation.
* ``long`` — the top decile of ``usage.total_tokens`` **within the fetched window**, computed here:
  a decile is relative to what was fetched.
* ``denied`` — traces with at least :data:`DENIED_TOOL_CALL_BAR` gate-denied tool calls, read off
  the spans (see :func:`denied_tool_calls`).

**How a denial is detected** (primary evidence: a live ``decode-prod`` probe run, 2026-09-11). A
gated tool call raises :class:`pydantic_ai.ApprovalRequired` from inside the tool body, so
pydantic-ai emits an ``execute_tool`` span carrying ``pydantic_ai.tool.deferral.name =
"ApprovalRequired"`` and NO result. If the gate then ALLOWS, the resume leg runs the tool for real
and emits a SECOND, completed span for the same tool + arguments. If the gate DENIES, no second
span is ever emitted — ``pydantic_ai._tool_execution._call_tool`` turns a ``ToolDenied`` straight
into a history part without touching the instrumented executor. So *denied = deferred legs minus
completed legs, per (tool, arguments)*. The task text assumed the denial TEXT rides a tool span's
output; it does not (live ``output contains "denied"`` → 0 hits), it rides the next model request's
input. The rule above reads the same fact where it actually exists.

A **Signature** is ``(preset, error type or first line, last tool name, metadata.model)`` — the four
facts that make two bad runs "the same bug". ``metadata.git_sha`` / ``metadata.model`` exist only on
traces from task 156 onward; older ones render as ``-`` (and are ``null`` in ``--json``).

Observations are fetched in one bounded Langfuse API query and grouped locally by trace. The SDK
client is imported lazily, so ``python -m evals mine --help`` needs no key and no network.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

# The ONE tool-span reader lives in the Kitaru importer, which a Worker uploads and runs as a single
# file from an arbitrary cwd — so it cannot import a sibling, and this caller imports from it
# instead (task 165). Costs `evals mine` a `kitaru.task.importer` import, which is cheap and already
# a dependency; `evals --help` never pays it, because this module is imported inside the command.
if TYPE_CHECKING:
    from rich.console import Console
    from rich.table import Table

logger = logging.getLogger(__name__)

# One denial is routine (the gate doing its job on a single ``ask``); two or more in one run is the
# shape worth mining: the agent kept trying to do something it was not allowed to do.
DENIED_TOOL_CALL_BAR = 2
# "Long" = this fraction of the fetched window, by total tokens.
LONG_DECILE = 0.1
# How a missing metadata key renders in a table (``--json`` keeps it ``null``).
MISSING = "-"
# Trace ids shown per signature TABLE. ``--json`` carries the whole group — it feeds task 164's
# case authoring, which needs every id, while a table is for reading.
TABLE_TRACE_CAP = 5
ERROR_SUMMARY_CHARS = 120
# Wide enough for BOTH 36-char uuids plus the three narrow columns (36+36+20+12+16 of content, plus
# Rich's padding and borders) — ids are the whole product of this command, and Rich falls back to 80
# columns when piped, which ellipsises them.
TABLE_MIN_WIDTH = 144
# Per-trace span fetch ceiling; a decode run is 15-30 spans, so this is slack, not a limit.
SPAN_FETCH_CAP = 500

PRESET_FILTERS: dict[str, str | None] = {
    "errors": None,
    "long": None,
    "denied": None,
}
PRESETS: tuple[str, ...] = tuple(PRESET_FILTERS)


class MineError(Exception):
    """A mining run cannot be built (bad preset / bad ``--since``) — surfaced as ONE CLI line."""


def live_project_name() -> str:
    """The Langfuse project label used for live tracing."""
    from decode.config.settings import settings

    return getattr(settings, "langfuse_project_name", "decode")


def _mapping(value: object) -> dict[str, Any]:
    """The value as a dict, or an empty one."""
    return value if isinstance(value, dict) else {}


def _span_metadata(span: dict[str, Any]) -> dict[str, Any]:
    return _mapping(span.get("metadata"))


def is_tool_span(span: dict[str, Any]) -> bool:
    """Recognize tool observations from explicit metadata or their recorded name."""
    metadata = _span_metadata(span)
    return (
        metadata.get("gen_ai.operation.name") == "execute_tool"
        or span.get("type") == "TOOL"
        or str(span.get("name", "")).startswith("execute_tool ")
    )


def tool_span_name(span: dict[str, Any]) -> str | None:
    """Return a tool name from explicit metadata or the execute_tool observation name."""
    metadata = _span_metadata(span)
    name = metadata.get("logfire.msg")
    if isinstance(name, str) and name.startswith("running tool: "):
        return name.removeprefix("running tool: ").strip() or None
    observation_name = span.get("name")
    if isinstance(observation_name, str) and observation_name.startswith("execute_tool "):
        return observation_name.removeprefix("execute_tool ").strip() or None
    tool_name = metadata.get("tool_name")
    return tool_name if isinstance(tool_name, str) and tool_name else None


def tool_span_arguments(span: dict[str, Any]) -> Any:
    """Read tool arguments from Langfuse input or OpenTelemetry metadata."""
    inputs = _mapping(span.get("input"))
    metadata = _span_metadata(span)
    for key in ("gen_ai.tool.call.arguments", "tool_arguments"):
        if key in inputs:
            return inputs[key]
        if key in metadata:
            return metadata[key]
    return inputs


def tool_span_deferred(span: dict[str, Any]) -> bool:
    """Detect a gated tool call that was deferred rather than executed."""
    metadata = _span_metadata(span)
    inputs = _mapping(span.get("input"))
    return (
        metadata.get("pydantic_ai.tool.deferral.name") == "ApprovalRequired"
        or inputs.get("pydantic_ai.tool.deferral.name") == "ApprovalRequired"
    )


def _as_dict(record: Any) -> dict[str, Any]:
    """One shape for everything above the source: a plain dict.

    Langfuse SDK models and JSON fixtures are normalized to the same plain-dict shape.
    """
    if isinstance(record, dict):
        return record
    if hasattr(record, "model_dump"):
        return dict(record.model_dump())
    return dict(record.dict())


def as_datetime(value: object) -> datetime | None:
    """An aware UTC datetime from Langfuse's live or serialized timestamps.

    Naive values are rejected (``None``), never silently assumed UTC — the project rule for
    datetimes at a boundary.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def metadata_value(trace: dict[str, Any], key: str) -> str | None:
    """A task-156 join field (``git_sha`` / ``model`` / …), or ``None`` on an older trace."""
    value = _mapping(trace.get("metadata")).get(key)
    return value if isinstance(value, str) and value.strip() else None


def error_summary(trace: dict[str, Any]) -> str | None:
    """The trace's error as ONE line: its exception type, else the message's first line.

    The type is preferred because it is what makes two failures the same failure;
    ``UsageLimitExceeded`` clusters, its message (which quotes the limit) does not.
    """
    info = _mapping(trace.get("error_info"))
    kind = info.get("exception_type")
    if isinstance(kind, str) and kind.strip():
        return kind.strip()
    message = info.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip().splitlines()[0][:ERROR_SUMMARY_CHARS]
    return None


def last_tool_name(spans: list[dict[str, Any]]) -> str | None:
    """The name of the LAST tool the run called, by span start time.

    Langfuse observations may be unordered, so they are sorted rather than trusted.
    """
    tools = [span for span in spans if is_tool_span(span)]
    tools.sort(
        key=lambda span: as_datetime(span.get("start_time")) or datetime.min.replace(tzinfo=UTC)
    )
    for span in reversed(tools):
        name = tool_span_name(span)
        if name:
            return name
    return None


def denied_tool_calls(spans: list[dict[str, Any]]) -> int:
    """How many gated tool calls this run had DENIED — deferred legs minus completed legs.

    See the module docstring for the evidence. Pairing is by ``(tool name, arguments)`` because the
    span metadata's ``call.id`` is the agent-leg id, NOT the tool-call id (verified live: a deferred
    ``read`` and a completed ``glob`` in one leg share a ``call.id``). Counting per pair means
    a tool called twice, approved once, reports exactly one denial.
    """
    deferred: Counter[tuple[str | None, str]] = Counter()
    completed: Counter[tuple[str | None, str]] = Counter()
    for span in spans:
        if not is_tool_span(span):
            continue
        arguments = json.dumps(tool_span_arguments(span), sort_keys=True, default=str)
        key = (tool_span_name(span), arguments)
        if tool_span_deferred(span):
            deferred[key] += 1
        else:
            completed[key] += 1
    return sum(max(count - completed[key], 0) for key, count in deferred.items())


def total_tokens(trace: dict[str, Any]) -> int | None:
    """The trace's ``usage.total_tokens``, or ``None`` when the run recorded no usage."""
    value = _mapping(trace.get("usage")).get("total_tokens")
    return value if isinstance(value, int) else None


def top_decile(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The longest ``ceil(n/10)`` traces of the fetched window, by total tokens.

    Three edges, pinned: a window smaller than ten still yields ONE trace (``max(1, …)``) — the
    longest run of five is still the one to look at; a trace with no ``usage`` is not ranked at all
    (an unknown length is not a short one); ties break on trace id, so the same window always
    returns the same list.
    """
    ranked = [trace for trace in traces if total_tokens(trace) is not None]
    if not ranked:
        return []
    ranked.sort(key=lambda trace: (-(total_tokens(trace) or 0), str(trace.get("id"))))
    return ranked[: max(1, math.ceil(len(ranked) * LONG_DECILE))]


@dataclass(frozen=True, slots=True)
class Signature:
    """What makes two bad runs "the same bug" (ADR-0022 §8): preset, error, last tool, model."""

    preset: str
    error: str | None
    last_tool: str | None
    model: str | None

    @property
    def label(self) -> str:
        """The one-line rendering used as the table title AND the ``--json`` ``signature`` key."""
        parts = (self.preset, self.error, self.last_tool, self.model)
        return " | ".join(part or MISSING for part in parts)


@dataclass(frozen=True, slots=True)
class TraceHit:
    """One matched trace, reduced to what a human needs to open (or skip) it."""

    signature: Signature
    id: str
    thread_id: str | None
    start_time: datetime | None
    git_sha: str | None
    model: str | None
    error: str | None

    def as_json(self) -> dict[str, Any]:
        """The ``traces[]`` entry of ``--json`` — absent metadata stays ``null``, never ``"-"``."""
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "git_sha": self.git_sha,
            "model": self.model,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class SignatureGroup:
    """Every hit sharing one :class:`Signature`, newest last."""

    signature: Signature
    traces: tuple[TraceHit, ...]

    @property
    def count(self) -> int:
        return len(self.traces)

    @property
    def first_seen(self) -> datetime | None:
        stamps = [hit.start_time for hit in self.traces if hit.start_time]
        return min(stamps) if stamps else None

    @property
    def last_seen(self) -> datetime | None:
        stamps = [hit.start_time for hit in self.traces if hit.start_time]
        return max(stamps) if stamps else None

    def as_json(self) -> dict[str, Any]:
        """``{signature, count, traces}`` — the whole group, uncapped: task 164 reads every id."""
        return {
            "signature": self.signature.label,
            "count": self.count,
            "traces": [hit.as_json() for hit in self.traces],
        }


def build_hit(preset: str, trace: dict[str, Any], spans: list[dict[str, Any]]) -> TraceHit:
    """Reduce one matched trace + its spans to a :class:`TraceHit` with its signature."""
    error = error_summary(trace)
    model = metadata_value(trace, "model")
    signature = Signature(preset=preset, error=error, last_tool=last_tool_name(spans), model=model)
    return TraceHit(
        signature=signature,
        id=str(trace.get("id")),
        thread_id=trace.get("thread_id") if isinstance(trace.get("thread_id"), str) else None,
        start_time=as_datetime(trace.get("start_time")),
        git_sha=metadata_value(trace, "git_sha"),
        model=model,
        error=error,
    )


def group_hits(hits: list[TraceHit]) -> list[SignatureGroup]:
    """Cluster hits by signature: biggest group first, ties by label, traces oldest-first.

    One trace can legitimately appear in TWO groups under ``--preset all`` (a long run that also
    errored), because the preset is part of the signature — that is reporting, not duplication.
    """
    buckets: dict[Signature, list[TraceHit]] = {}
    for hit in hits:
        buckets.setdefault(hit.signature, []).append(hit)
    groups = [
        SignatureGroup(
            signature=signature,
            traces=tuple(
                sorted(
                    bucket,
                    key=lambda hit: (
                        hit.start_time or datetime.min.replace(tzinfo=UTC),
                        hit.id,
                    ),
                )
            ),
        )
        for signature, bucket in buckets.items()
    ]
    groups.sort(key=lambda group: (-group.count, group.signature.label))
    return groups


def groups_as_json(groups: list[SignatureGroup]) -> str:
    """The whole run as the ``--json`` document task 164 consumes."""
    return json.dumps([group.as_json() for group in groups], indent=2)


def _stamp(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%SZ") if value else MISSING


def mine_console() -> Console:
    """A Rich console at least :data:`TABLE_MIN_WIDTH` wide, so no trace id is ever truncated.

    Rich sizes to the terminal — and to 80 columns when the output is piped, which ellipsises the
    ids (``019f60fd-7a5c-73…``) this command exists to hand over. A wider terminal still wins.
    """
    from rich.console import Console

    return Console(width=max(Console().width, TABLE_MIN_WIDTH))


def render_group_table(group: SignatureGroup, *, cap: int = TABLE_TRACE_CAP) -> Table:
    """One signature as a Rich table: the count + window in the caption, ≤ ``cap`` trace ids."""
    from rich.table import Table

    shown = group.traces[:cap]
    more = group.count - len(shown)
    caption = (
        f"{group.count} trace(s) · first {_stamp(group.first_seen)} "
        f"· last {_stamp(group.last_seen)}"
    )
    if more > 0:
        caption += f" · {more} more (use --json for all)"
    table = Table(title=group.signature.label, caption=caption, title_justify="left")
    table.add_column("trace id")
    table.add_column("thread id")
    table.add_column("started")
    table.add_column("git_sha")
    table.add_column("model")
    for hit in shown:
        table.add_row(
            hit.id,
            hit.thread_id or MISSING,
            _stamp(hit.start_time),
            (hit.git_sha or MISSING)[:12],
            hit.model or MISSING,
        )
    return table


def since_clause(since: str | None) -> str | None:
    """Return a normalized timestamp for a tz-aware ``--since``; ``None`` when unset.

    A naive timestamp is refused rather than assumed UTC (the project's datetime rule at a
    boundary) rather than silently assuming UTC.
    """
    if since is None or not since.strip():
        return None
    moment = as_datetime(since)
    if moment is None:
        raise MineError(
            f"--since {since!r} is not a timezone-aware ISO timestamp — "
            "write it like 2026-09-04T00:00:00Z (or with an explicit offset)."
        )
    return moment.isoformat()


def compose_filter(preset: str, since: str | None) -> str | None:
    """The normalized time boundary used by the local preset filter."""
    if preset not in PRESET_FILTERS:
        raise MineError(f"unknown preset {preset!r} — pick one of {', '.join(PRESETS)}.")
    return since_clause(since)


@dataclass
class TraceSource:
    """The Langfuse observation query seam: plain trace and span dicts up."""

    client: Any
    project: str
    _spans: dict[str, list[dict[str, Any]]] = field(default_factory=dict, init=False, repr=False)

    def traces(self, filter_string: str | None, limit: int) -> list[dict[str, Any]]:
        """Fetch a bounded observation window and group observations into traces."""
        from_start_time = as_datetime(filter_string) if filter_string else None
        response = self.client.api.observations.get_many(
            limit=min(max(limit * 50, 100), 1000),
            from_start_time=from_start_time,
        )
        observations = [_as_dict(record) for record in getattr(response, "data", []) or []]
        by_trace: dict[str, list[dict[str, Any]]] = {}
        for observation in observations:
            trace_id = observation.get("trace_id")
            if isinstance(trace_id, str) and trace_id:
                by_trace.setdefault(trace_id, []).append(observation)

        traces: list[dict[str, Any]] = []
        self._spans.clear()
        for trace_id, spans in by_trace.items():
            spans.sort(
                key=lambda span: (
                    as_datetime(span.get("start_time")) or datetime.min.replace(tzinfo=UTC)
                )
            )
            root = next(
                (span for span in spans if not span.get("parent_observation_id")),
                spans[0],
            )
            error = next(
                (
                    span
                    for span in spans
                    if str(getattr(span.get("level"), "value", span.get("level"))).upper()
                    == "ERROR"
                ),
                None,
            )
            metadata = _mapping(root.get("metadata"))
            usage = _mapping(root.get("usage_details"))
            total = usage.get("total_tokens")
            if not isinstance(total, int):
                total = sum(
                    value
                    for value in (usage.get("input"), usage.get("output"))
                    if isinstance(value, int)
                )
            trace = {
                "id": trace_id,
                "thread_id": root.get("session_id"),
                "start_time": root.get("start_time"),
                "metadata": metadata,
                "usage": {"total_tokens": total} if total else {},
                "has_tool_spans": any(is_tool_span(span) for span in spans),
                "error_info": (
                    {
                        "exception_type": _span_metadata(error).get("exception_type"),
                        "message": error.get("status_message") or error.get("output"),
                    }
                    if error
                    else None
                ),
            }
            if from_start_time is None or (
                as_datetime(trace.get("start_time")) is not None
                and as_datetime(trace["start_time"]) >= from_start_time
            ):
                traces.append(trace)
                self._spans[trace_id] = spans
        traces.sort(
            key=lambda trace: (
                as_datetime(trace.get("start_time")) or datetime.min.replace(tzinfo=UTC)
            ),
            reverse=True,
        )
        return traces[:limit]

    def spans(self, trace: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the observations grouped for this trace."""
        if trace.get("has_tool_spans") is False:
            return []
        return self._spans.get(str(trace.get("id")), [])


def open_source(project: str | None = None) -> TraceSource:
    """A lazily constructed :class:`TraceSource` over the configured Langfuse project."""
    from evals.harness.datasets import langfuse_client

    target = project or live_project_name()
    return TraceSource(client=langfuse_client(), project=target)


def mine_preset(
    source: TraceSource, preset: str, *, since: str | None = None, limit: int = 50
) -> list[TraceHit]:
    """Run ONE preset: fetch its window, apply the client-side rules, reduce to hits."""
    traces = source.traces(compose_filter(preset, since), limit)
    if preset == "errors":
        traces = [trace for trace in traces if error_summary(trace)]
    if preset == "long":
        traces = top_decile(traces)
    hits: list[TraceHit] = []
    for trace in traces:
        spans = source.spans(trace)
        if preset == "denied" and denied_tool_calls(spans) < DENIED_TOOL_CALL_BAR:
            continue
        hits.append(build_hit(preset, trace, spans))
    logger.info("[eval] mine preset=%s matched %d trace(s)", preset, len(hits))
    return hits


def mine(
    source: TraceSource,
    *,
    preset: str = "errors",
    since: str | None = None,
    limit: int = 50,
) -> list[SignatureGroup]:
    """Mine one preset (or ``all`` of them) and cluster every hit by signature."""
    presets = PRESETS if preset == "all" else (preset,)
    hits: list[TraceHit] = []
    for name in presets:
        hits.extend(mine_preset(source, name, since=since, limit=limit))
    return group_hits(hits)
