"""Offline tests for the MINED regression cases 21-23 (ADR-0022 §8; task 164).

A mined case is an invented one plus provenance and a live trace behind it, so these tests cover both
halves, all offline / no keys:

* the **contract** — every mined case carries the ``mined`` tag, a ``fixed_in``, exactly ONE
  deterministic metric, and either a live ``source_trace_id`` + ``thread_id`` or a ``skip_reason``
  that says why the trace could not be reached (case zero);
* the **behavior** — each runnable mined case is driven end-to-end through the real agent on a
  scripted ``FunctionModel`` and its metric scores ``1.0``; the same case is then driven through the
  behavior its TRACE showed and the metric scores ``0.0``, which is what proves the metric actually
  grades the mined regression rather than passing on anything.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from support.eval_models import bash_then_finish, read_then_finish

from evals.harness.driver import run_agent_once_sync
from evals.harness.regression import run_case
from evals.regression.case import RegressionCase
from evals.regression.cases.mined_empty_model_response import COMMAND
from evals.regression.loader import case_by_id, load_cases

MINED_IDS = {"21-empty-model-response", "22-guessed-file-path"}


def _mined_cases() -> list[RegressionCase]:
    return [case for case in load_cases() if "mined" in case.tags]


def _score_only_metric(case: RegressionCase, payload: dict) -> float:
    """The value of the case's single metric against ``payload`` (a mined case declares exactly one)."""
    (metric,) = case.metrics
    return metric.score(**payload).value


def empty_response_model() -> FunctionModel:
    """A model that returns an assistant turn with NOTHING in it — the mined trace's own shape.

    Trace ``019f5cd5`` recorded four of these in a row (``parts: []``), which pydantic-ai retries and
    then turns into ``UnexpectedModelBehavior``. Reproducing the shape offline is what proves case 21's
    metric fails on the behavior it was mined from.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        return
        yield  # pragma: no cover - never reached; makes this an async generator

    return FunctionModel(stream_function=stream_function)


def read_guessed_then_finish(guess: str, real: str, final_text: str) -> FunctionModel:
    """A model that opens ``guess`` first, then ``real``, then finishes — the mined trace's sequence."""

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        opened = [
            part.args_as_dict().get("path")
            for message in messages
            if isinstance(message, ModelResponse)
            for part in message.parts
            if getattr(part, "tool_name", None) == "read"
        ]
        if guess not in opened:
            yield {0: DeltaToolCall(name="read", json_args=json.dumps({"path": guess}))}
            return
        if real not in opened:
            yield {0: DeltaToolCall(name="read", json_args=json.dumps({"path": real}))}
            return
        yield final_text

    return FunctionModel(stream_function=stream_function)


# --- the mined contract ------------------------------------------------------------------------


def test_the_mined_cases_are_registered_beside_the_invented_ones() -> None:
    """Mined cases land BESIDE the 21 — nothing was deleted to make room (ADR-0022 §8)."""
    ids = {case.id for case in load_cases()}

    assert ids >= MINED_IDS
    assert "smoke-read-tool" in ids


def test_every_mined_case_carries_its_provenance_and_exactly_one_metric() -> None:
    for case in _mined_cases():
        assert "mined" in case.tags, case.id
        assert case.fixed_in, case.id
        assert len(case.metrics) == 1, f"{case.id}: a mined case grades ONE deterministic metric"
        assert not case.symptom.startswith("harness invariant:"), (
            f"{case.id}: a mined symptom names the behavior its trace showed"
        )
        assert case.source_trace_id and case.thread_id, case.id


# --- 21 empty-model-response -------------------------------------------------------------------


def test_empty_model_response_fixture_seeds_a_non_empty_workspace(tmp_path: Path) -> None:
    case_by_id("21-empty-model-response").fixture(tmp_path)

    assert (tmp_path / "note.txt").is_file()


def test_empty_model_response_runs_green_offline(install_model) -> None:
    install_model(bash_then_finish(COMMAND, "It printed arm64, /workspace and SANDBOX_OK."))
    case = case_by_id("21-empty-model-response")

    payload = run_case(case)

    assert payload["agent_error"] is None
    assert _score_only_metric(case, payload) == 1.0


def test_empty_model_response_metric_fails_on_the_behavior_the_trace_showed(install_model) -> None:
    """Empty assistant turns → retry ceiling → no answer: the mined symptom must score 0.0."""
    install_model(empty_response_model())
    case = case_by_id("21-empty-model-response")

    payload = run_case(case)

    assert payload["agent_error"], "an empty-turn run must be recorded as an agent error"
    assert _score_only_metric(case, payload) == 0.0


# --- 22 guessed-file-path ----------------------------------------------------------------------


def test_guessed_file_path_fixture_seeds_a_readme_with_an_extension(tmp_path: Path) -> None:
    case_by_id("22-guessed-file-path").fixture(tmp_path)

    assert (tmp_path / "README.md").is_file()
    assert not (tmp_path / "README").exists()
    assert (tmp_path / "src" / "app" / "main.py").is_file()


def test_guessed_file_path_runs_green_offline(install_model) -> None:
    install_model(read_then_finish("README.md", "README.md now greets the reader."))
    case = case_by_id("22-guessed-file-path")

    payload = run_case(case)

    assert payload["agent_error"] is None
    assert _score_only_metric(case, payload) == 1.0


def test_guessed_file_path_metric_fails_on_the_behavior_the_trace_showed(install_model) -> None:
    """``read("README")`` in a tree that holds README.md is the mined violation — it must score 0.0."""
    install_model(read_guessed_then_finish("README", "README.md", "Added the hello line."))
    case = case_by_id("22-guessed-file-path")

    payload = run_case(case)

    assert [call["name"] for call in payload["tool_calls"]] == ["read", "read"]
    assert _score_only_metric(case, payload) == 0.0


def test_a_read_that_raised_is_still_recorded_so_recovering_cannot_hide_the_guess(
    install_model, tmp_path: Path
) -> None:
    """The trace RECOVERED after its bad guess; the case must still fail on it.

    decode's ``read`` raises ``ModelRetry`` on a missing path, so the failed leg leaves a
    ``RetryPromptPart`` in the history AND keeps its ``ToolCallPart`` — which is what the driver reads
    into ``tool_calls``. Without that, a run that guessed and then recovered would look identical to a
    run that never guessed, and case 22 would pass on the very behavior it was mined from.
    """
    install_model(read_guessed_then_finish("README", "README.md", "Added the hello line."))
    case = case_by_id("22-guessed-file-path")
    case.fixture(tmp_path)

    record = run_agent_once_sync(case.prompt, cwd=tmp_path, max_requests=case.max_requests)

    retries = [
        part
        for message in record.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]
    assert retries, "reading a missing path must RAISE, not return text"
    assert "README" in str(retries[0].content)
    assert [call.args.get("path") for call in record.tool_calls if call.name == "read"] == [
        "README",
        "README.md",
    ]


@pytest.mark.parametrize("path", ["README.md", "/tmp/decode-regression-xyz/README.md"])
def test_guessed_file_path_metric_accepts_a_relative_or_absolute_seeded_path(path: str) -> None:
    """An absolute path into the temp Workspace is the same correct behavior as a relative one."""
    case = case_by_id("22-guessed-file-path")

    (metric,) = case.metrics
    assert metric.score(tool_calls=[{"name": "read", "args": {"path": path}}]).value == 1.0
