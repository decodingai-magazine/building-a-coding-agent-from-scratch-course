"""Integration capstone: the headless-run Langfuse trace shape through the REAL runner (ADR-0024).

Drives the **real** :func:`decode.runtime.headless.run_headless_task` — no kitaru, no local stack,
no network, no ``GEMINI_API_KEY`` — swapping only the model boundary (the ``_build_headless_agent``
seam, patched to inject a scripted ``FunctionModel`` agent). Spans are captured with
``logfire.testing``'s in-memory exporter (``capfire``). It proves the trace a headless run produces:

* ONE ``decode_run`` root span per run, carrying the run's Langfuse session id (AC1);
* the pydantic-ai model/tool spans nest **under** it — one trace for the whole run (AC5) — and a
  leaf model span carries ``gen_ai.usage.*`` tokens;
* **inactive** — with tracing off (no Langfuse keys), a run emits ZERO spans and returns the
  same output as always (AC4).

Activation mirrors :mod:`tests.integration.test_langfuse_repl_trace`: ``tracing._active`` is forced
``True`` and pydantic-ai is instrumented directly (fake Langfuse keys are set only for fidelity)
rather than through ``init_tracing``, whose real ``logfire.configure`` would replace ``capfire``'s
exporter and could flush to the network. Because ``_active`` is already ``True``, the runner's own
``init_tracing()`` is a no-op early-return that leaves ``capfire``'s exporter intact, and its
``root_span`` opens real spans. An autouse fixture saves/restores the global instrumentation +
module flag so nothing leaks across tests.
"""

from __future__ import annotations

import json
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
def _isolate_tracing_state(monkeypatch) -> Iterator[None]:
    """Save tracing state and keep capfire's scrubber from redacting synthetic session ids."""
    prior_instrument = Agent._instrument_default
    prior_active = tracing._active
    configure = logfire.configure

    def configure_with_session_id_scrubbing(*args, **kwargs):
        kwargs["scrubbing"] = logfire.ScrubbingOptions(
            callback=tracing._preserve_langfuse_session_id
        )
        return configure(*args, **kwargs)

    monkeypatch.setattr(logfire, "configure", configure_with_session_id_scrubbing)
    yield
    Agent.instrument_all(prior_instrument)
    tracing._active = prior_active
    reset_tracing()


@pytest.fixture
def active_tracing(_isolate_tracing_state, monkeypatch, capfire) -> CaptureLogfire:  # noqa: F811
    """Turn tracing ON against ``capfire``'s in-memory exporter and instrument pydantic-ai.

    ``capfire`` configures logfire with the in-memory exporter FIRST (fixture dependency), then we
    instrument pydantic-ai so its model/tool spans emit into it and set ``_active`` so ``root_span``
    opens real spans (and the runner's in-body ``init_tracing`` early-returns without replacing the
    exporter). Fake Langfuse keys are set only for fidelity with the production trigger — the
    span path never reads it. Restored by :func:`_isolate_tracing_state`.
    """
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr("fake-secret"), raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", SecretStr("fake-public"), raising=False)
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
    """The tool-call spans, selected by the Langfuse importer.

    Not by span *name*: the importer maps a span to a ``tool_call`` node when its
    metadata says ``gen_ai.operation.name == "execute_tool"``, and reads the tool's name out of
    ``logfire.msg``. Selecting here on the same attribute means an upstream change to either fact
    (the pydantic-ai upgrade of task 155 is exactly when that could happen) fails this test instead
    of silently producing a trace the importer would classify as a plain ``span``.
    """
    return [s for s in spans if s["attributes"].get("gen_ai.operation.name") == "execute_tool"]


def test_a_headless_run_is_one_decode_run_root_with_nested_spans_and_usage(
    active_tracing, monkeypatch
):
    """AC1 + AC5: one ``decode_run`` root; model/tool spans nest under it; usage rides along.

    A two-leg run (read a seeded file inline → final text) through the REAL runner. Exactly one
    ``decode_run`` root span is emitted, it carries the run's Langfuse session id, the model
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
    assert root["attributes"].get("langfuse.session.id"), list(root["attributes"])
    assert root["attributes"]["langfuse.trace.input"] == "read the spec then report"
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


def test_a_tool_span_carries_what_the_langfuse_importer_reads(active_tracing, monkeypatch):
    """The Langfuse → Kitaru importer's detection contract, pinned on a real run (task 155, AC6).

    The importer turns a Langfuse span into a ``tool_call`` node on exactly two
    facts, and nothing else: metadata ``gen_ai.operation.name == "execute_tool"``, and a
    ``logfire.msg`` of the form ``running tool: <name>`` that the tool's name is parsed out of.
    Both are pydantic-ai instrumentation details, so a version bump can move them without a single
    other test going red — and the damage would only show up as a Kitaru session whose tool calls
    all imported as anonymous spans. This asserts them on the spans a real headless run emits.
    """
    Path(_READ_TARGET).write_text(_READ_CONTENTS, encoding="utf-8")
    _patch_agent(
        monkeypatch,
        [
            ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": _READ_TARGET})]),
            ModelResponse(parts=[TextPart(content="read the spec")]),
        ],
    )

    hl.run_headless_task("read the spec then report")

    spans = active_tracing.exporter.exported_spans_as_dict()
    tool_spans = _tool_spans(spans)
    assert len(tool_spans) == 1, [s["name"] for s in spans]
    attributes = tool_spans[0]["attributes"]
    assert attributes["gen_ai.operation.name"] == "execute_tool"
    # The name the importer parses — the prefix and the tool name, verbatim (_TOOL_MSG_PREFIX).
    assert attributes["logfire.msg"] == "running tool: read"


def test_each_run_gets_its_own_root_span_and_session_id(active_tracing, monkeypatch):
    """Two runs are two traces: the session id is minted per run, never shared."""
    _patch_agent(monkeypatch, [ModelResponse(parts=[TextPart(content="first")])])
    hl.run_headless_task("first task")
    _patch_agent(monkeypatch, [ModelResponse(parts=[TextPart(content="second")])])
    hl.run_headless_task("second task")

    roots = _roots_named(active_tracing.exporter.exported_spans_as_dict(), hl.RUN_SPAN_NAME)

    assert len(roots) == 2
    assert (
        roots[0]["attributes"]["langfuse.session.id"]
        != roots[1]["attributes"]["langfuse.session.id"]
    )


def test_an_inactive_run_emits_zero_spans_and_returns_the_same_output(capfire, monkeypatch):  # noqa: F811
    """AC4: with tracing OFF, a run emits ZERO spans and returns its normal output.

    No ``active_tracing``: ``tracing._active`` stays False (so ``root_span`` is a nullcontext and
    ``init_tracing()`` is a no-op) and pydantic-ai is never instrumented — exactly production with
    no Langfuse keys. capfire still supplies an exporter, so the assertion observes the *absence*
    of any span while the run returns the same text as always.
    """
    assert tracing._active is False
    _patch_agent(monkeypatch, [ModelResponse(parts=[TextPart(content="done, untraced")])])

    assert hl.run_headless_task("run without tracing") == "done, untraced"
    assert capfire.exporter.exported_spans_as_dict() == [], "an inactive run must emit no spans"


def test_the_root_span_carries_the_eval_join_metadata(active_tracing, monkeypatch):
    """ADR-0022 §10: git_sha / model / sandbox_mode / decode_env ride on the exported root span.

    Langfuse's propagation helper maps trace metadata to ``langfuse.trace.metadata.<key>`` OTEL
    attributes. A root span's metadata is the trace's, so asserting the exported attributes here
    verifies the metadata contract.
    """
    _patch_agent(monkeypatch, [ModelResponse(parts=[TextPart(content="done")])])

    hl.run_headless_task("do it", model="gemini-2.5-pro")

    root = _roots_named(active_tracing.exporter.exported_spans_as_dict(), hl.RUN_SPAN_NAME)[0]
    attributes = root["attributes"]
    prefix = "langfuse.trace.metadata."
    # THIS run's model, not the configured default.
    assert json.loads(attributes[f"{prefix}model"]) == "gemini-2.5-pro"
    assert json.loads(attributes[f"{prefix}sandbox_mode"]) == settings.sandbox_mode
    assert json.loads(attributes[f"{prefix}decode_env"]) == settings.decode_env
    assert attributes[f"{prefix}git_sha"]  # a sha, or the explicit "unknown" — never missing
    # ``kitaru_session_id`` is absent unless the adapter published one (0.2.1 does not).
    assert f"{prefix}kitaru_session_id" not in attributes


def test_a_traced_run_also_writes_its_summary(active_tracing, monkeypatch, tmp_path):
    """The two ground-truth surfaces agree: the summary's session id IS the trace's thread id."""
    _patch_agent(monkeypatch, [ModelResponse(parts=[TextPart(content="done")])])
    target = tmp_path / "summary.json"

    assert hl.run_headless_task("do it", summary_json=target) == "done"

    summary = json.loads(target.read_text(encoding="utf-8"))
    root = _roots_named(active_tracing.exporter.exported_spans_as_dict(), hl.RUN_SPAN_NAME)[0]
    assert summary["session_id"] == root["attributes"]["langfuse.session.id"]
    assert summary["exit_reason"] == "completed"
    assert summary["requests"] == 1
