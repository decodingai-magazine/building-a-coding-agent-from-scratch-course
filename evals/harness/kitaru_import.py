"""Backfill Kitaru Sessions from Opik traces — the acquisition half of the ``opik`` importer.

``importers/opik_importer.py`` has always been able to PARSE one Opik export
(``{schema_version, workspace, project, traces: [{trace, spans}]}``); nothing in the repo produced
one (its own docstring said "see ``scripts/`` once wired"). This module is that producer
(ADR-0022 §11): the Opik SDK fetches the traces a human picked (usually out of ``evals mine``),
expands each one to its whole THREAD — a decode thread is one session's turns — writes the envelope
under ``.decode/kitaru-imports/`` and shells ``kitaru session import`` through
:mod:`evals.harness.kitaru_cli`.

Everything above :class:`ThreadSource` is pure over plain dicts, which is what lets the envelope
builder be tested against the importer's OWN ``parse`` (round-trip) with no network and no kitaru
import.

**Two rules are mirrored from the importer on purpose**, because the producer and the parser have to
agree on identity or a re-import would silently duplicate:

* :func:`thread_key` — ``trace.thread_id``, else ``metadata.thread_id``, else the trace id alone;
* :func:`session_external_id` — ``enc(workspace)/enc(project)/enc(thread)``, the importer's
  ``external_id``.

They are re-implemented here (a few lines of stdlib) rather than imported from the importer, which
pulls ``kitaru.task.importer`` at import time and so would cost ``evals kitaru import --help`` a
kitaru import (ADR-0017 §1); the unit tests prove the two agree by running the real parser over a
built envelope.

**Why ``external_id`` and not the Session Name.** An imported Session is named by the importer after
the first trace's NAME (``ImportedSession.name = first.get("name") or key``), so a decode backfill
is called ``agent run``, never the thread id — only ``external_id`` (and ``metadata.opik_thread_id``)
identify it. Recorded sessions are the other way round: the adapter names them by the decode session
id, which is the join :mod:`evals.harness.kitaru_cohort` uses.

**A live Worker is required.** ``kitaru session import`` files a job that a Worker executes (the
server executes nothing, ADR-0019); with no Worker claiming, ``--wait`` times out and the CLI says
so. Verified on a local OSS server: ``kitaru worker start`` in another shell, then the same import
settles in ~2s.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.harness import kitaru_cli
from evals.harness.kitaru_cli import KitaruCommandError

logger = logging.getLogger(__name__)

# The envelope version `importers/opik_importer.py::parse` reads.
SCHEMA_VERSION = 1

# Envelopes live under the Harness Home's `.decode/` (ADR-0012 §6) — never in the user's tree.
IMPORTS_DIR_NAME = "kitaru-imports"

# Every backfilled session carries this tag, so `kitaru session list --tag` finds the whole batch.
DEFAULT_TAG = "regression-case"

# A decode turn is 15-30 spans and a thread is a handful of turns; these are slack, not limits.
SPAN_FETCH_CAP = 1000
THREAD_TRACE_CAP = 200


class TraceImportError(Exception):
    """An import cannot be built or run — surfaced as ONE CLI line.

    Not ``ImportError``: that is a builtin, and shadowing it in a module that also catches real
    import failures would be a trap.
    """


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """What happened to one thread: the line ``evals kitaru import`` prints.

    ``status`` is one of ``imported`` (a Session was created), ``skipped`` (Kitaru already had it —
    no payload was uploaded), ``duplicate`` (the importer deduplicated it server-side), ``queued``
    (filed under ``--no-wait``, so no Session id is known yet) or ``failed``.
    """

    thread: str
    status: str
    session_id: str | None = None
    readiness: str | None = None
    path: Path | None = None
    detail: str | None = None


def _mapping(value: object) -> dict[str, Any]:
    """The value as a dict, or an empty one — Opik omits absent fields entirely."""
    return value if isinstance(value, dict) else {}


def thread_key(trace: dict[str, Any]) -> str | None:
    """The importer's thread key: the trace column, else its metadata, else ``None``."""
    key = trace.get("thread_id")
    if isinstance(key, str) and key:
        return key
    key = _mapping(trace.get("metadata")).get("thread_id")
    return key if isinstance(key, str) and key else None


def session_external_id(workspace: str, project: str, key: str) -> str:
    """The ``external_id`` the importer assigns the Session for this thread."""
    return "/".join(urllib.parse.quote(part, safe="") for part in (workspace, project, key))


def build_envelope(
    *, workspace: str, project: str, entries: list[dict[str, Any]]
) -> dict[str, Any]:
    """The ``opik@N`` importer payload for one thread — pure, no I/O.

    Trace order is left as fetched: the parser sorts turns by ``start_time`` itself, so an envelope
    never depends on the order Opik answered in.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace,
        "project": project,
        "traces": [
            {"trace": entry["trace"], "spans": list(entry.get("spans") or [])} for entry in entries
        ],
    }


def envelope_path(thread: str, imports_dir: Path) -> Path:
    """``<imports dir>/<thread>.json``, with the thread id encoded into ONE safe path segment.

    A thread id is normally a uuid, but it is provider data: percent-encoding it means a
    ``../../etc/passwd`` thread cannot write outside the imports dir.
    """
    return imports_dir / f"{urllib.parse.quote(thread, safe='')}.json"


def default_imports_dir() -> Path:
    """``.decode/kitaru-imports/`` under the Harness Home (never the Workspace, ADR-0012 §6)."""
    from decode.config.settings import settings

    return Path(settings.decode_dir) / IMPORTS_DIR_NAME


def import_args(
    path: Path, *, importer_ref: str, agent_ref: str, tag: str | None, wait: bool
) -> list[str]:
    """The ``kitaru session import`` sub-command argv (without the ``kitaru`` prefix).

    ``--tag`` is dropped when not waiting: kitaru refuses the combination (a tag is applied to the
    sessions the settled job created), and a refused import is worse than an untagged one.
    """
    args = [
        "session",
        "import",
        str(path),
        "--importer",
        importer_ref,
        "--agent",
        agent_ref,
        "--media-type",
        "application/json",
    ]
    if wait:
        if tag:
            args += ["--tag", tag]
        args.append("--wait")
    return args


def existing_session(
    sessions: list[dict[str, Any]], *, workspace: str, project: str, key: str
) -> dict[str, Any] | None:
    """The already-imported Session for this thread, or ``None``.

    Matched on the importer's ``external_id`` (its deduplication key) with the thread recorded in
    session metadata as a second spelling — the Session NAME is deliberately not used, because the
    importer names a backfilled session after the first trace, not after the thread.
    """
    external_id = session_external_id(workspace, project, key)
    for session in sessions:
        if session.get("external_id") == external_id:
            return session
        metadata = _mapping(session.get("metadata"))
        if metadata.get("opik_thread_id") == key and metadata.get("opik_project") == project:
            return session
    return None


def readiness_of(session: dict[str, Any]) -> str | None:
    """The importer's replay-readiness level for a session (``ready`` / ``partial`` / …)."""
    level = _mapping(_mapping(session.get("metadata")).get("replay_readiness")).get("level")
    return level if isinstance(level, str) and level else None


def format_outcome(outcome: ImportOutcome) -> str:
    """``<thread> → <session id> (<readiness>)`` — the one line per thread the command prints."""
    target = outcome.session_id or outcome.detail or "-"
    note = outcome.readiness or outcome.status
    return f"{outcome.thread} → {target} ({note})"


@dataclass(slots=True)
class ThreadSource:
    """The ONE Opik-touching seam: traces + spans as plain dicts, one project, one workspace."""

    client: Any
    project: str
    workspace: str

    def trace(self, trace_id: str) -> dict[str, Any]:
        """One trace by id, or one friendly error naming the id that is not there."""
        try:
            return _as_dict(self.client.get_trace_content(trace_id))
        except Exception as exc:  # opik raises its own ApiError / KeyError shapes
            raise TraceImportError(
                f"no trace {trace_id!r} in Opik project {self.project!r} ({type(exc).__name__})."
            ) from exc

    def thread_traces(self, key: str) -> list[dict[str, Any]]:
        """Every trace of one thread — ``thread_id`` is a server-side OQL field."""
        found = self.client.search_traces(
            project_name=self.project,
            filter_string=f'thread_id = "{key}"',
            max_results=THREAD_TRACE_CAP,
        )
        return [_as_dict(record) for record in found]

    def spans(self, trace_id: str) -> list[dict[str, Any]]:
        """Every span of one trace — no ``has_tool_spans`` shortcut: an envelope wants them all."""
        found = self.client.search_spans(
            project_name=self.project, trace_id=trace_id, max_results=SPAN_FETCH_CAP
        )
        return [_as_dict(record) for record in found]

    def entries(self, traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``[{trace, spans}]`` for every trace, the envelope's own shape."""
        return [{"trace": trace, "spans": self.spans(str(trace["id"]))} for trace in traces]


def _as_dict(record: Any) -> dict[str, Any]:
    """One shape for everything above the source: a plain dict (Opik answers pydantic models)."""
    return record if isinstance(record, dict) else dict(record.dict())


def open_source(project: str | None = None) -> ThreadSource:
    """A :class:`ThreadSource` over the live Opik project — ``opik`` imported lazily (ADR-0017 §1)."""
    import opik

    from decode.config.settings import settings
    from evals.harness.mine import live_project_name

    target = project or live_project_name()
    return ThreadSource(
        client=opik.Opik(project_name=target), project=target, workspace=settings.opik_workspace
    )


def collect_threads(
    source: ThreadSource, *, trace_ids: list[str], threads: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Expand every trace id to its thread and merge in the explicitly named threads.

    A trace with no thread key becomes its own single-turn thread, which is exactly how the importer
    groups it — so the envelope, the Session and the skip check all agree on the same key.
    """
    keys: dict[str, list[dict[str, Any]]] = {}
    singles: dict[str, dict[str, Any]] = {}

    for trace_id in trace_ids:
        trace = source.trace(trace_id)
        key = thread_key(trace)
        if key is None:
            singles.setdefault(str(trace["id"]), trace)
        elif key not in keys:
            keys[key] = source.thread_traces(key) or [trace]
    for key in threads:
        if key not in keys:
            found = source.thread_traces(key)
            if not found:
                raise TraceImportError(
                    f"no traces for thread {key!r} in project {source.project!r}."
                )
            keys[key] = found

    collected = {key: source.entries(traces) for key, traces in keys.items()}
    collected.update({key: source.entries([trace]) for key, trace in singles.items()})
    return collected


def run_import(
    source: ThreadSource,
    *,
    trace_ids: list[str],
    threads: list[str],
    agent_ref: str,
    importer_ref: str,
    tag: str | None = DEFAULT_TAG,
    wait: bool = True,
    server: str | None = None,
    imports_dir: Path | None = None,
) -> list[ImportOutcome]:
    """Import every named thread once, skipping the ones Kitaru already has.

    One ``session list`` read up front answers the skip check for the whole batch; after a settled
    import the sessions it created are read back by the import's own task id (the CLI's own
    ``next_actions`` hint), because ``session import`` reports counts, not session ids.
    """
    collected = collect_threads(source, trace_ids=trace_ids, threads=threads)
    if not collected:
        return []

    directory = imports_dir or default_imports_dir()
    # Scoped to the agent the import files under: the skip check only ever matches a session this
    # same command created, and an unscoped read pages the whole workspace on every run.
    known = kitaru_cli.list_sessions(agent=agent_ref.split("@")[0], server=server)
    outcomes: list[ImportOutcome] = []
    for key in sorted(collected):
        already = existing_session(
            known, workspace=source.workspace, project=source.project, key=key
        )
        if already is not None:
            outcomes.append(
                ImportOutcome(
                    thread=key,
                    status="skipped",
                    session_id=str(already.get("id")),
                    readiness=readiness_of(already),
                    detail="already imported",
                )
            )
            continue
        outcomes.append(
            _import_thread(
                source,
                key=key,
                entries=collected[key],
                directory=directory,
                agent_ref=agent_ref,
                importer_ref=importer_ref,
                tag=tag,
                wait=wait,
                server=server,
            )
        )
    return outcomes


def _import_thread(
    source: ThreadSource,
    *,
    key: str,
    entries: list[dict[str, Any]],
    directory: Path,
    agent_ref: str,
    importer_ref: str,
    tag: str | None,
    wait: bool,
    server: str | None,
) -> ImportOutcome:
    """Write one thread's envelope and hand it to kitaru; never raises for one bad thread."""
    directory.mkdir(parents=True, exist_ok=True)
    path = envelope_path(key, directory)
    envelope = build_envelope(workspace=source.workspace, project=source.project, entries=entries)
    # ``default=str`` because a live Opik record carries real ``datetime`` values; the importer
    # parses the ISO strings this produces (``datetime.fromisoformat``).
    path.write_text(json.dumps(envelope, default=str), encoding="utf-8")

    try:
        result = kitaru_cli.run_kitaru(
            import_args(path, importer_ref=importer_ref, agent_ref=agent_ref, tag=tag, wait=wait),
            server=server,
        )
    except KitaruCommandError as exc:
        logger.warning("[eval] import of thread %s failed: %s", key, exc)
        return ImportOutcome(thread=key, status="failed", path=path, detail=str(exc))

    item = _mapping(result.get("item"))
    stats = _mapping(item.get("stats"))
    if not wait:
        return ImportOutcome(
            thread=key, status="queued", path=path, detail=str(_mapping(item.get("job")).get("id"))
        )
    created = _created_sessions(item, server=server)
    if not created:
        status = "duplicate" if stats.get("skipped") else "failed"
        detail = "already in kitaru" if status == "duplicate" else _failure_detail(stats)
        return ImportOutcome(thread=key, status=status, path=path, detail=detail)
    session = created[0]
    return ImportOutcome(
        thread=key,
        status="imported",
        session_id=str(session.get("id")),
        readiness=readiness_of(session),
        path=path,
    )


def _created_sessions(item: dict[str, Any], *, server: str | None) -> list[dict[str, Any]]:
    """The sessions this import created, read back by its task id (the CLI's own hint)."""
    task_id = _mapping(item.get("task")).get("id")
    if not _mapping(item.get("stats")).get("created") or not task_id:
        return []
    task_filter = json.dumps({"field": "task_id", "op": "eq", "value": str(task_id)})
    return kitaru_cli.list_sessions(extra=["--filter", task_filter], server=server)


def _failure_detail(stats: dict[str, Any]) -> str:
    """The importer's own first failure line, or a count — one line either way."""
    failures = stats.get("failures")
    if isinstance(failures, list) and failures:
        first = _mapping(failures[0])
        return str(first.get("error") or first)
    return f"the importer created no session (failed={stats.get('failed')})"
