"""**Trace Mining**: search the LIVE Opik project, cluster the hits, print trace ids (ADR-0022 §8).

The discovery half of the mining loop (the glossary's Trace Mining). ``evals mine`` never writes
a case (that is task 164): it
answers "what went wrong in production lately, and which of it is the SAME thing?" — four presets
over the live project, each hit reduced to a **Signature**, one Rich table per signature with the
trace ids a human then picks from.

Four presets, ONE table (:data:`PRESET_FILTERS`). Two are server-side OQL, two are client-side
because Opik's query language cannot express them:

* ``errors`` — ``error_info is_not_empty``. (Verified against the live backend: ``error_info``
  accepts only ``is_empty`` / ``is_not_empty``, and the operator takes NO value — ``error_info
  is_not_empty ""`` is a parse error.)
* ``low-quality`` — ``feedback_scores.response_quality < 5``, the score
  :mod:`evals.harness.online_rule` attaches. Empty until that rule exists.
* ``long`` — the top decile of ``usage.total_tokens`` **within the fetched window**, computed here:
  a decile is relative to what was fetched, which OQL has no way to say.
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

Cost, deliberately: one ``search_spans`` call per candidate trace (skipped when the trace says
``has_tool_spans`` is false, cached per trace id so ``--preset all`` pays once). That is
O(``--limit``) requests per run — fine at the demo's 68-trace scale; if a run ever wants thousands
of traces, switch to one windowed span query grouped client-side.

``opik`` is imported lazily (:func:`open_source`), so ``python -m evals mine --help`` needs no key
and no network (ADR-0017 §1). Everything above the source is pure over plain dicts — which is what
the fixture-driven unit tests exercise.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from evals.harness.online import live_project_name
from evals.harness.online_rule import RULE_NAME
from importers.opik_spans import (
    is_tool_span,
    tool_span_arguments,
    tool_span_deferred,
    tool_span_name,
)

if TYPE_CHECKING:
    from rich.console import Console
    from rich.table import Table

logger = logging.getLogger(__name__)

# A ``response_quality`` below this is worth reading — the online rule's 0-10 scale, so 5 is "did
# not really answer". Same number the README's UI filter suggests.
LOW_QUALITY_BAR = 5
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
    "errors": "error_info is_not_empty",
    "low-quality": f"feedback_scores.{RULE_NAME} < {LOW_QUALITY_BAR}",
    "long": None,
    "denied": None,
}
PRESETS: tuple[str, ...] = tuple(PRESET_FILTERS)


class MineError(Exception):
    """A mining run cannot be built (bad preset / bad ``--since``) — surfaced as ONE CLI line."""


def _mapping(value: object) -> dict[str, Any]:
    """The value as a dict, or an empty one — Opik omits absent fields from ``.dict()`` entirely."""
    return value if isinstance(value, dict) else {}


def _as_dict(record: Any) -> dict[str, Any]:
    """One shape for everything above the source: a plain dict.

    Opik hands back pydantic ``TracePublic`` / ``SpanPublic`` models whose ``.dict()`` yields plain
    dicts (with ``datetime`` values and nested ``error_info`` as a dict); the JSON fixtures are
    already dicts with ISO strings. Normalising here is what lets every rule below be pure.
    """
    if isinstance(record, dict):
        return record
    return dict(record.dict())


def as_datetime(value: object) -> datetime | None:
    """An aware UTC datetime from Opik's two spellings (``datetime`` live, ISO string exported).

    Naive values are rejected (``None``), never silently assumed UTC — the project rule for
    datetimes at a boundary. Opik's exported form (``2026-09-09 12:19:00.836000+00:00``) parses
    with :meth:`datetime.fromisoformat` as-is.
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

    Opik returns a trace's spans unordered, so they are sorted here rather than trusted; a tool span
    whose ``logfire.msg`` lost its name is skipped rather than reported as the answer.
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
    """``start_time > "<iso>"`` for a tz-aware ``--since``; ``None`` when unset.

    A naive timestamp is refused rather than assumed UTC (the project's datetime rule at a
    boundary) — and the OQL parser validates the COLUMN, never the value, so a bad one would
    otherwise reach the server.
    """
    if since is None or not since.strip():
        return None
    moment = as_datetime(since)
    if moment is None:
        raise MineError(
            f"--since {since!r} is not a timezone-aware ISO timestamp — "
            "write it like 2026-09-04T00:00:00Z (or with an explicit offset)."
        )
    return f'start_time > "{moment.strftime("%Y-%m-%dT%H:%M:%SZ")}"'


def compose_filter(preset: str, since: str | None) -> str | None:
    """The OQL a preset sends to Opik: its own clause AND the optional ``--since`` window."""
    if preset not in PRESET_FILTERS:
        raise MineError(f"unknown preset {preset!r} — pick one of {', '.join(PRESETS)}.")
    clauses = [clause for clause in (PRESET_FILTERS[preset], since_clause(since)) if clause]
    return " AND ".join(clauses) if clauses else None


@dataclass
class TraceSource:
    """The ONE Opik-touching seam: plain dicts up, one cached span query per trace (ADR-0022 §8)."""

    client: Any
    project: str
    _spans: dict[str, list[dict[str, Any]]] = field(default_factory=dict, init=False, repr=False)

    def traces(self, filter_string: str | None, limit: int) -> list[dict[str, Any]]:
        """The matching traces, newest first as Opik returns them, as plain dicts."""
        found = self.client.search_traces(
            project_name=self.project, filter_string=filter_string, max_results=limit
        )
        return [_as_dict(record) for record in found]

    def spans(self, trace: dict[str, Any]) -> list[dict[str, Any]]:
        """This trace's spans — skipped entirely when Opik says the trace has no tool spans."""
        if trace.get("has_tool_spans") is False:
            return []
        trace_id = str(trace.get("id"))
        cached = self._spans.get(trace_id)
        if cached is None:
            cached = [
                _as_dict(record)
                for record in self.client.search_spans(
                    project_name=self.project, trace_id=trace_id, max_results=SPAN_FETCH_CAP
                )
            ]
            self._spans[trace_id] = cached
        return cached


def open_source(project: str | None = None) -> TraceSource:
    """A :class:`TraceSource` over the LIVE project (``settings.opik_project_name``).

    ``opik`` is imported here, not at module scope, so building the CLI never needs a key.
    """
    import opik

    target = project or live_project_name()
    return TraceSource(client=opik.Opik(project_name=target), project=target)


def mine_preset(
    source: TraceSource, preset: str, *, since: str | None = None, limit: int = 50
) -> list[TraceHit]:
    """Run ONE preset: fetch its window, apply the client-side rules, reduce to hits."""
    traces = source.traces(compose_filter(preset, since), limit)
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
