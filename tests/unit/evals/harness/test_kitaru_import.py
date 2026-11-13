"""Langfuse trace acquisition and Kitaru import envelope tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evals.harness.kitaru_import import (
    SCHEMA_VERSION,
    ThreadSource,
    build_envelope,
    import_args,
    session_external_id,
    thread_key,
)


def _observation(
    observation_id: str,
    *,
    trace_id: str = "trace-1",
    session_id: str = "session-1",
    parent_id: str | None = None,
    name: str = "agent",
    observation_type: str = "SPAN",
) -> dict[str, Any]:
    return {
        "id": observation_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "parent_observation_id": parent_id,
        "name": name,
        "type": observation_type,
        "level": "DEFAULT",
        "start_time": "2026-09-01T10:00:00+00:00",
        "end_time": "2026-09-01T10:00:05+00:00",
        "input": {"prompt": "hello"},
        "output": {"answer": "hi"},
        "metadata": {"thread_id": session_id},
    }


class FakeObservations:
    def __init__(self, observations: list[dict[str, Any]]) -> None:
        self.observations = observations
        self.calls: list[dict[str, Any]] = []

    def get_many(self, *, limit: int, **filters: Any) -> Any:
        self.calls.append({"limit": limit, **filters})
        filtered = self.observations
        for key in ("trace_id", "session_id"):
            if key in filters:
                filtered = [item for item in filtered if item.get(key) == filters[key]]
        if filters.get("is_root_observation"):
            filtered = [item for item in filtered if item.get("parent_observation_id") is None]
        return SimpleNamespace(data=filtered[:limit])


def _source(observations: list[dict[str, Any]]) -> tuple[ThreadSource, FakeObservations]:
    endpoint = FakeObservations(observations)
    return (
        ThreadSource(
            client=SimpleNamespace(api=SimpleNamespace(observations=endpoint)),
            project="decode",
            workspace="default",
        ),
        endpoint,
    )


def test_thread_key_prefers_session_and_falls_back_to_metadata() -> None:
    assert thread_key({"id": "t1", "thread_id": "session-1"}) == "session-1"
    assert thread_key({"id": "t1", "metadata": {"thread_id": "session-2"}}) == "session-2"
    assert thread_key({"id": "t1"}) is None


def test_external_id_encodes_each_component() -> None:
    assert session_external_id("a/b", "p", "thread/1") == "a%2Fb/p/thread%2F1"


def test_thread_source_groups_langfuse_observations_by_trace() -> None:
    root = _observation("root")
    generation = _observation(
        "generation", parent_id="root", name="generate", observation_type="GENERATION"
    )
    source, endpoint = _source([root, generation])

    trace = source.trace("trace-1")
    entries = source.entries([trace])

    assert trace["id"] == "trace-1"
    assert trace["thread_id"] == "session-1"
    assert entries[0]["spans"][0]["id"] == "root"
    assert entries[0]["spans"][1]["type"] == "llm"
    assert source.thread_traces("session-1")[0]["id"] == "trace-1"
    assert endpoint.calls[0]["trace_id"] == "trace-1"


def test_envelope_round_trips_through_the_langfuse_importer() -> None:
    from importers.langfuse_importer import parse

    root = _observation("root")
    source, _ = _source([root])
    trace = source.trace("trace-1")
    envelope = build_envelope(
        workspace="default",
        project="decode",
        entries=source.entries([trace]),
    )

    sessions = list(parse(json.dumps(envelope).encode(), {}))

    assert envelope["schema_version"] == SCHEMA_VERSION
    assert len(sessions) == 1
    assert sessions[0].external_id == session_external_id("default", "decode", "session-1")
    assert sessions[0].metadata["langfuse_project"] == "decode"


def test_import_args_uses_langfuse_provider_ref(tmp_path: Path) -> None:
    args = import_args(
        tmp_path / "session.json",
        importer_ref="langfuse@1",
        agent_ref="decode@2",
        tag="regression-case",
        wait=True,
    )

    assert args[args.index("--importer") + 1] == "langfuse@1"
    assert "--wait" in args
