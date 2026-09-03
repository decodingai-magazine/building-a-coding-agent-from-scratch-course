"""Integration capstone: the headless-run Opik trace shape through the REAL runner (ADR-0014 §4-5).

Drives the **real** :func:`decode.runtime.headless.run_headless_task` — no kitaru, no local stack,
no network, no ``GEMINI_API_KEY`` — swapping only the model boundary (the ``_build_headless_agent``
seam, patched to inject a scripted ``FunctionModel`` agent). Spans are captured with
``logfire.testing``'s in-memory exporter (``capfire``). It proves the trace a headless run produces:

* ONE ``decode_run`` root span per run, carrying the run's session id as ``thread_id`` (AC1);
* the pydantic-ai model/tool spans nest **under** it — one trace for the whole run (AC5) — and a
  leaf model span carries ``gen_ai.usage.*`` tokens;
* **inactive** — with tracing off (no ``OPIK_API_KEY``), a run emits ZERO spans and returns the
  same output as always (AC4).

Activation mirrors :mod:`tests.integration.test_opik_repl_trace`: ``tracing._active`` is forced
``True`` and pydantic-ai is instrumented directly (a fake ``opik_api_key`` is set only for fidelity)
rather than through ``init_tracing``, whose real ``logfire.configure`` would replace ``capfire``'s
exporter and could flush to the network. Because ``_active`` is already ``True``, the runner's own
``init_tracing()`` is a no-op early-return that leaves ``capfire``'s exporter intact, and its
``root_span`` opens real spans. An autouse fixture saves/restores the global instrumentation +
module flag so nothing leaks across tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import logfire
import pytest
from logfire.testing import (
    CaptureLogfire,
    capfire,  # noqa: F401 — imported so pytest registers the in-memory fixture
)
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from support.runtime_agents import make_scripted_agent

import decode.runtime.headless as hl
from decode.config.settings import settings
from decode.observability import tracing
from decode.observability.tracing import reset_tracing

_READ_TARGET = "spec.md"
_READ_CONTENTS = "trace this headless run"


@pytest.fixture(autouse=True)
def _in_tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from a throwaway cwd — the headless tool scope in ``none`` mode is the launch cwd."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _isolate_tracing_state() -> Iterator[None]:
    """Save/restore the GLOBAL pydantic-ai instrumentation + the module flag, so nothing leaks."""
    prior_instrument = Agent._instrument_default
    prior_active = tracing._active
    yield
    Agent.instrument_all(prior_instrument)
    tracing._active = prior_active
    reset_tracing()


@pytest.fixture
def active_tracing(monkeypatch, capfire) -> CaptureLogfire:  # noqa: F811
    """Turn tracing ON against ``capfire``'s in-memory exporter and instrument pydantic-ai.

    ``capfire`` configures logfire with the in-memory exporter FIRST (fixture dependency), then we
    instrument pydantic-ai so its model/tool spans emit into it and set ``_active`` so ``root_span``
    opens real spans (and the runner's in-body ``init_tracing`` early-returns without replacing the
    exporter). A fake ``opik_api_key`` is set only for fidelity with the production trigger — the
    span path never reads it. Restored by :func:`_isolate_tracing_state`.
    """
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("fake-opik-key"), raising=False)
    monkeypatch.setattr(tracing, "_active", True)
    logfire.instrument_pydantic_ai()
    return capfire


def _patch_agent(monkeypatch, responses: list[ModelResponse]) -> None:
    """Point the runner's agent seam at a scripted agent (no network, all tools registered)."""
    agent, _counter = make_scripted_agent(responses)
    monkeypatch.setattr(hl, "_build_headless_agent", lambda model=None: agent)


def _roots_named(spans: list[dict], name: str) -> list[dict]:
    return [s for s in spans if s["name"] == name]


def _model_spans(spans: list[dict]) -> list[dict]:
    """The pydantic-ai model-request spans (``chat <model>``) — NOT the run root or the ``agent run``."""
    return [s for s in spans if s["name"].startswith("chat ")]


def _tool_spans(spans: list[dict]) -> list[dict]:
    """The tool-call spans — ``execute_tool <name>`` under pydantic-ai 2.x instrumentation."""
    return [s for s in spans if s["name"].startswith("execute_tool")]


def test_a_headless_run_is_one_decode_run_root_with_nested_spans_and_usage(
    active_tracing, monkeypatch
):
    """AC1 + AC5: one ``decode_run`` root; model/tool spans nest under it; usage rides along.

    A two-leg run (read a seeded file inline → final text) through the REAL runner. Exactly one
    ``decode_run`` root span is emitted, it carries the run's session id as ``thread_id``, the model
    + tool spans nest under it (one trace for the whole run), and a leaf model span carries token
    usage.
    """
    Path(_READ_TARGET).write_text(_READ_CONTENTS, encoding="utf-8")  # cwd is the isolated tmp_path
    _patch_agent(
        monkeypatch,
        [
            ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": _READ_TARGET})]),
            ModelResponse(parts=[TextPart(content="read the spec")]),
        ],
    )

    assert hl.run_headless_task("read the spec then report") == "read the spec"

    spans = active_tracing.exporter.exported_spans_as_dict()
    roots = _roots_named(spans, hl.RUN_SPAN_NAME)
    assert len(roots) == 1, [s["name"] for s in spans]
    root = roots[0]
    assert root["parent"] is None, "the decode_run span must be the trace root"
    assert root["attributes"]["thread_id"]  # the run's session id — also the Hand-back branch id
    assert root["attributes"]["input"] == "read the spec then report"
    trace_id = root["context"]["trace_id"]

    model_spans = _model_spans(spans)
    tool_spans = _tool_spans(spans)
    assert model_spans, [s["name"] for s in spans]
    assert tool_spans, [s["name"] for s in spans]
    # AC5: everything the run produced nests under the one root — same trace, not a root.
    for span in model_spans + tool_spans:
        assert span["context"]["trace_id"] == trace_id
        assert span["parent"] is not None

    # AC1: a leaf model span carries ``gen_ai.usage.*`` tokens (> 0).
    input_tokens = [s["attributes"].get("gen_ai.usage.input_tokens") for s in model_spans]
    assert any(tokens and tokens > 0 for tokens in input_tokens), input_tokens


def test_each_run_gets_its_own_root_span_and_thread_id(active_tracing, monkeypatch):
    """Two runs are two traces: the session id is minted per run, never shared."""
    _patch_agent(monkeypatch, [ModelResponse(parts=[TextPart(content="first")])])
    hl.run_headless_task("first task")
    _patch_agent(monkeypatch, [ModelResponse(parts=[TextPart(content="second")])])
    hl.run_headless_task("second task")

    roots = _roots_named(active_tracing.exporter.exported_spans_as_dict(), hl.RUN_SPAN_NAME)

    assert len(roots) == 2
    assert roots[0]["attributes"]["thread_id"] != roots[1]["attributes"]["thread_id"]


def test_an_inactive_run_emits_zero_spans_and_returns_the_same_output(capfire, monkeypatch):  # noqa: F811
    """AC4: with tracing OFF, a run emits ZERO spans and returns its normal output.

    No ``active_tracing``: ``tracing._active`` stays False (so ``root_span`` is a nullcontext and
    ``init_tracing()`` is a no-op) and pydantic-ai is never instrumented — exactly production with
    no ``OPIK_API_KEY``. capfire still supplies an exporter, so the assertion observes the *absence*
    of any span while the run returns the same text as always.
    """
    assert tracing._active is False
    _patch_agent(monkeypatch, [ModelResponse(parts=[TextPart(content="done, untraced")])])

    assert hl.run_headless_task("run without tracing") == "done, untraced"
    assert capfire.exporter.exported_spans_as_dict() == [], "an inactive run must emit no spans"
