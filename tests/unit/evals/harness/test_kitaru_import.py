"""``evals kitaru import`` — Opik traces in, Kitaru Sessions out (task 165, ADR-0022 §11).

The Opik client is a fake and the ``kitaru`` CLI is a fake subprocess whose argv IS the assertion.
The round-trip tests run the REAL ``importers/opik_importer.py::parse`` over a built envelope, so an
envelope these tests accept is one the registered importer can actually parse — that is the whole
point of a builder that lives in a different file from its parser.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals.harness import kitaru_cli, kitaru_import
from evals.harness.kitaru_import import (
    SCHEMA_VERSION,
    ImportOutcome,
    ThreadSource,
    build_envelope,
    collect_threads,
    envelope_path,
    existing_session,
    format_outcome,
    import_args,
    readiness_of,
    run_import,
    session_external_id,
    thread_key,
)

FIXTURE = Path(__file__).resolve().parents[3].parent / "importers" / "fixtures" / "opik-sample.json"
WORKSPACE = "default"
PROJECT = "decode-local"
SERVER = "http://localhost:8000"


def fixture_document() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def trace(trace_id: str, *, thread: str | None = None, **extra: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": trace_id,
        "name": "chat_turn",
        "start_time": "2026-09-01T10:00:00+00:00",
        "end_time": "2026-09-01T10:00:05+00:00",
        "input": {"prompt": "hi"},
        "output": {"final_result": "hello"},
        **extra,
    }
    if thread is not None:
        record["thread_id"] = thread
    return record


class Record:
    """An Opik pydantic model as far as this code cares: something with ``.dict()``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def dict(self) -> dict[str, Any]:
        return dict(self._payload)


class FakeOpik:
    """``get_trace_content`` + ``search_traces`` + ``search_spans``, and a record of the queries."""

    def __init__(
        self,
        traces: list[dict[str, Any]],
        spans: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.by_id = {str(t["id"]): t for t in traces}
        self.spans_by_trace = spans or {}
        self.filters: list[str | None] = []

    def get_trace_content(self, id: str) -> Record:
        return Record(self.by_id[id])

    def search_traces(
        self, project_name: str, filter_string: str | None = None, max_results: int = 100
    ) -> list[Record]:
        self.filters.append(filter_string)
        matched = [
            t
            for t in self.by_id.values()
            if filter_string is None or f'"{t.get("thread_id")}"' in filter_string
        ]
        return [Record(t) for t in matched[:max_results]]

    def search_spans(
        self, project_name: str, trace_id: str, max_results: int = 100
    ) -> list[Record]:
        return [Record(s) for s in self.spans_by_trace.get(trace_id, [])]


def source(fake: FakeOpik) -> ThreadSource:
    return ThreadSource(client=fake, project=PROJECT, workspace=WORKSPACE)


# --- the two rules mirrored from the importer ------------------------------------------------------


def test_the_thread_key_is_the_trace_column_first():
    assert thread_key(trace("t1", thread="session-abc")) == "session-abc"


def test_the_thread_key_falls_back_to_trace_metadata():
    assert thread_key(trace("t1", metadata={"thread_id": "session-abc"})) == "session-abc"


def test_a_trace_with_no_thread_has_no_key():
    assert thread_key(trace("t1")) is None


def test_the_external_id_encodes_every_component_separately():
    assert session_external_id("a/b", "p", "thread/1") == "a%2Fb/p/thread%2F1"


def test_the_external_id_matches_the_importers_own_identity():
    """Round-trip: the id computed here is the id the registered importer assigns."""
    from importers.opik_importer import parse

    document = fixture_document()
    payload = json.dumps(document).encode()
    parsed = [s for s in parse(payload, {}) if type(s).__name__ == "ImportedSession"]

    # The fixture's two threadless entries key on their own trace id, exactly like a thread.
    expected = {
        session_external_id(document["workspace"], document["project"], key)
        for key in ("thread-alpha", "trace-failed-1")
    }
    assert expected <= {session.external_id for session in parsed}


# --- the envelope ----------------------------------------------------------------------------------


def test_the_envelope_carries_the_schema_workspace_and_project():
    envelope = build_envelope(workspace=WORKSPACE, project=PROJECT, entries=[])

    assert envelope["schema_version"] == SCHEMA_VERSION
    assert envelope["workspace"] == WORKSPACE
    assert envelope["project"] == PROJECT
    assert envelope["traces"] == []


def test_the_envelope_keeps_each_trace_with_its_own_spans():
    entries = [{"trace": trace("t1", thread="s"), "spans": [{"id": "sp1", "trace_id": "t1"}]}]

    envelope = build_envelope(workspace=WORKSPACE, project=PROJECT, entries=entries)

    assert envelope["traces"][0]["trace"]["id"] == "t1"
    assert envelope["traces"][0]["spans"][0]["id"] == "sp1"


def test_a_built_envelope_parses_into_one_session_per_thread():
    """The real parser, over an envelope this module built from the fixture's own records."""
    from importers.opik_importer import parse

    document = fixture_document()
    entries = [e for e in document["traces"] if e["trace"].get("thread_id") == "thread-alpha"]

    envelope = build_envelope(
        workspace=document["workspace"], project=document["project"], entries=entries
    )
    parsed = list(parse(json.dumps(envelope).encode(), {}))

    assert len(parsed) == 1
    assert parsed[0].external_id == session_external_id(
        document["workspace"], document["project"], "thread-alpha"
    )


def test_the_envelope_is_json_serialisable_with_opik_datetimes():
    """Opik hands back real ``datetime`` values live; the written file must still be json."""
    from datetime import UTC, datetime

    entries = [
        {"trace": trace("t1", thread="s", start_time=datetime(2026, 9, 1, tzinfo=UTC)), "spans": []}
    ]

    envelope = build_envelope(workspace=WORKSPACE, project=PROJECT, entries=entries)

    assert "2026-09-01" in json.dumps(envelope, default=str)


# --- where the envelope lands ----------------------------------------------------------------------


def test_the_envelope_file_is_named_by_its_thread(tmp_path):
    assert envelope_path("session-abc", tmp_path) == tmp_path / "session-abc.json"


def test_a_thread_id_with_a_slash_cannot_escape_the_imports_dir(tmp_path):
    path = envelope_path("../../etc/passwd", tmp_path)

    assert path.parent == tmp_path


def test_the_default_imports_dir_sits_under_the_harness_homes_decode_dir():
    from decode.config.settings import settings

    assert kitaru_import.default_imports_dir() == Path(settings.decode_dir) / "kitaru-imports"


# --- the kitaru argv -------------------------------------------------------------------------------


def test_the_import_argv_is_the_documented_invocation(tmp_path):
    path = tmp_path / "thread.json"

    args = import_args(path, importer_ref="opik@1", agent_ref="decode@2", tag="x", wait=True)

    assert args[:2] == ["session", "import"]
    assert str(path) in args
    assert args[args.index("--importer") + 1] == "opik@1"
    assert args[args.index("--agent") + 1] == "decode@2"
    assert args[args.index("--media-type") + 1] == "application/json"
    assert args[args.index("--tag") + 1] == "x"
    assert "--wait" in args


def test_a_tag_is_dropped_without_wait_because_kitaru_refuses_that_combination(tmp_path):
    """kitaru 0.26: `--tag` requires `--wait`; sending both would fail the whole import."""
    args = import_args(
        tmp_path / "t.json", importer_ref="opik@1", agent_ref="decode@2", tag="x", wait=False
    )

    assert "--tag" not in args
    assert "--wait" not in args


# --- the skip check --------------------------------------------------------------------------------


def test_a_thread_already_imported_is_found_by_external_id():
    sessions = [{"id": "s-1", "external_id": session_external_id(WORKSPACE, PROJECT, "t-abc")}]

    found = existing_session(sessions, workspace=WORKSPACE, project=PROJECT, key="t-abc")

    assert found is not None and found["id"] == "s-1"


def test_the_importers_own_thread_metadata_also_identifies_a_session():
    sessions = [{"id": "s-1", "metadata": {"opik_thread_id": "t-abc", "opik_project": PROJECT}}]

    assert existing_session(sessions, workspace=WORKSPACE, project=PROJECT, key="t-abc") is not None


def test_a_session_from_another_project_is_not_this_thread():
    sessions = [{"id": "s-1", "external_id": session_external_id(WORKSPACE, "other", "t-abc")}]

    assert existing_session(sessions, workspace=WORKSPACE, project=PROJECT, key="t-abc") is None


def test_readiness_is_read_off_the_importers_session_metadata():
    assert readiness_of({"metadata": {"replay_readiness": {"level": "ready"}}}) == "ready"
    assert readiness_of({"metadata": {}}) is None


def test_the_printed_line_joins_the_thread_to_its_session_and_readiness():
    line = format_outcome(
        ImportOutcome(thread="t-abc", status="imported", session_id="s-1", readiness="ready")
    )

    assert "t-abc" in line and "s-1" in line and "ready" in line


# --- thread expansion ------------------------------------------------------------------------------


def test_a_trace_id_expands_to_every_turn_of_its_thread():
    fake = FakeOpik(
        [
            trace("t1", thread="s-1"),
            trace("t2", thread="s-1"),
            trace("t3", thread="s-2"),
        ]
    )

    threads = collect_threads(source(fake), trace_ids=["t1"], threads=[])

    assert set(threads) == {"s-1"}
    assert {e["trace"]["id"] for e in threads["s-1"]} == {"t1", "t2"}


def test_a_thread_asked_for_by_name_needs_no_trace_id():
    fake = FakeOpik([trace("t1", thread="s-1")])

    threads = collect_threads(source(fake), trace_ids=[], threads=["s-1"])

    assert set(threads) == {"s-1"}
    assert fake.filters and "s-1" in (fake.filters[0] or "")


def test_a_threadless_trace_becomes_its_own_single_turn_thread():
    fake = FakeOpik([trace("t1")])

    threads = collect_threads(source(fake), trace_ids=["t1"], threads=[])

    assert set(threads) == {"t1"}


def test_two_trace_ids_from_the_same_thread_are_fetched_once():
    fake = FakeOpik([trace("t1", thread="s-1"), trace("t2", thread="s-1")])

    threads = collect_threads(source(fake), trace_ids=["t1", "t2"], threads=[])

    assert set(threads) == {"s-1"}


def test_every_trace_carries_its_spans_into_the_envelope():
    fake = FakeOpik([trace("t1", thread="s-1")], spans={"t1": [{"id": "sp1", "trace_id": "t1"}]})

    threads = collect_threads(source(fake), trace_ids=["t1"], threads=[])

    assert threads["s-1"][0]["spans"][0]["id"] == "sp1"


# --- the orchestration -----------------------------------------------------------------------------


def install_kitaru(mocker, envelopes: list[dict[str, Any]]) -> list[list[str]]:
    """Fake the kitaru seam; return the list the recorded sub-command argv lands in."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], *, server: str | None = None, **kwargs: Any) -> dict[str, Any]:
        calls.append(list(args))
        return envelopes.pop(0) if envelopes else {"ok": True, "items": [], "page": {}}

    mocker.patch.object(kitaru_cli, "run_kitaru", fake_run)
    return calls


IMPORT_OK = {
    "ok": True,
    "item": {
        "task": {"id": "task-1", "status": "completed"},
        "stats": {"created": 1, "skipped": 0, "failed": 0, "failures": []},
    },
}


def test_a_new_thread_is_written_imported_and_reported(mocker, tmp_path):
    fake = FakeOpik([trace("t1", thread="s-1")], spans={"t1": [{"id": "sp1", "trace_id": "t1"}]})
    calls = install_kitaru(
        mocker,
        [
            {"ok": True, "items": [], "page": {}},  # session list: nothing imported yet
            IMPORT_OK,
            {
                "ok": True,
                "items": [{"id": "kit-1", "metadata": {"replay_readiness": {"level": "ready"}}}],
                "page": {},
            },
        ],
    )

    outcomes = run_import(
        source(fake),
        trace_ids=["t1"],
        threads=[],
        agent_ref="decode@2",
        importer_ref="opik@1",
        server=SERVER,
        imports_dir=tmp_path,
    )

    assert [o.status for o in outcomes] == ["imported"]
    assert outcomes[0].session_id == "kit-1"
    assert outcomes[0].readiness == "ready"
    written = json.loads((tmp_path / "s-1.json").read_text())
    assert written["traces"][0]["trace"]["id"] == "t1"
    assert calls[1][:2] == ["session", "import"]


def test_the_skip_check_is_scoped_to_the_agent_the_import_files_under(mocker, tmp_path):
    """An unscoped read pages every session on the workspace on every run."""
    fake = FakeOpik([trace("t1", thread="s-1")])
    calls = install_kitaru(
        mocker,
        [{"ok": True, "items": [], "page": {}}, IMPORT_OK, {"ok": True, "items": [], "page": {}}],
    )

    run_import(
        source(fake),
        trace_ids=["t1"],
        threads=[],
        agent_ref="decode@2",
        importer_ref="opik@1",
        server=SERVER,
        imports_dir=tmp_path,
    )

    assert calls[0][calls[0].index("--agent") + 1] == "decode"


def test_a_thread_kitaru_already_has_is_skipped_without_a_second_import(mocker, tmp_path):
    fake = FakeOpik([trace("t1", thread="s-1")])
    calls = install_kitaru(
        mocker,
        [
            {
                "ok": True,
                "items": [
                    {
                        "id": "kit-1",
                        "external_id": session_external_id(WORKSPACE, PROJECT, "s-1"),
                        "metadata": {"replay_readiness": {"level": "partial"}},
                    }
                ],
                "page": {},
            }
        ],
    )

    outcomes = run_import(
        source(fake),
        trace_ids=["t1"],
        threads=[],
        agent_ref="decode@2",
        importer_ref="opik@1",
        server=SERVER,
        imports_dir=tmp_path,
    )

    assert [o.status for o in outcomes] == ["skipped"]
    assert outcomes[0].session_id == "kit-1"
    assert [c[:2] for c in calls] == [["session", "list"]]


def test_an_import_that_created_nothing_reports_the_duplicate_rather_than_a_session(
    mocker, tmp_path
):
    fake = FakeOpik([trace("t1", thread="s-1")])
    install_kitaru(
        mocker,
        [
            {"ok": True, "items": [], "page": {}},
            {
                "ok": True,
                "item": {
                    "task": {"id": "task-1"},
                    "stats": {"created": 0, "skipped": 1, "failed": 0, "failures": []},
                },
            },
            {"ok": True, "items": [], "page": {}},
        ],
    )

    outcomes = run_import(
        source(fake),
        trace_ids=["t1"],
        threads=[],
        agent_ref="decode@2",
        importer_ref="opik@1",
        server=SERVER,
        imports_dir=tmp_path,
    )

    assert outcomes[0].status == "duplicate"


def test_the_created_session_is_looked_up_by_the_imports_own_task_id(mocker, tmp_path):
    fake = FakeOpik([trace("t1", thread="s-1")])
    calls = install_kitaru(
        mocker,
        [
            {"ok": True, "items": [], "page": {}},
            IMPORT_OK,
            {"ok": True, "items": [{"id": "kit-1"}], "page": {}},
        ],
    )

    run_import(
        source(fake),
        trace_ids=["t1"],
        threads=[],
        agent_ref="decode@2",
        importer_ref="opik@1",
        server=SERVER,
        imports_dir=tmp_path,
    )

    assert "task-1" in json.dumps(calls[2])


def test_an_import_failure_is_one_outcome_not_an_abort_of_the_batch(mocker, tmp_path):
    from evals.harness.kitaru_cli import KitaruCommandError

    fake = FakeOpik([trace("t1", thread="s-1"), trace("t2", thread="s-2")])
    responses: list[Any] = [
        {"ok": True, "items": [], "page": {}},
        KitaruCommandError("`kitaru session import` failed: no live worker"),
        IMPORT_OK,
        {"ok": True, "items": [{"id": "kit-2"}], "page": {}},
    ]

    def fake_run(args: list[str], **kwargs: Any) -> dict[str, Any]:
        answer = responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    mocker.patch.object(kitaru_cli, "run_kitaru", fake_run)

    outcomes = run_import(
        source(fake),
        trace_ids=["t1", "t2"],
        threads=[],
        agent_ref="decode@2",
        importer_ref="opik@1",
        server=SERVER,
        imports_dir=tmp_path,
    )

    assert sorted(o.status for o in outcomes) == ["failed", "imported"]


def test_nothing_to_import_is_an_empty_list_not_an_error(mocker, tmp_path):
    fake = FakeOpik([])
    install_kitaru(mocker, [{"ok": True, "items": [], "page": {}}])

    assert (
        run_import(
            source(fake),
            trace_ids=[],
            threads=[],
            agent_ref="decode@2",
            importer_ref="opik@1",
            server=SERVER,
            imports_dir=tmp_path,
        )
        == []
    )


def test_a_trace_id_opik_does_not_have_is_one_friendly_error(mocker, tmp_path):
    fake = FakeOpik([])

    with pytest.raises(kitaru_import.TraceImportError) as error:
        collect_threads(source(fake), trace_ids=["missing"], threads=[])

    assert "missing" in str(error.value)
