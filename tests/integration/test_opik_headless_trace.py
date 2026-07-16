"""Integration capstone: the headless-run Opik trace shape through the REAL flow (ADR-0014 §4-5).

Drives the **real** Kitaru ``@flow`` + ``KitaruAgent`` adapter (``run_agent_task`` /
``run_agent_task_hitl``) on the local ZenML stack — no server, no network, no ``GEMINI_API_KEY`` — and
swaps only two boundaries: the runtime seam (``_build_runtime_agent`` / ``_build_hitl_runtime_agent``,
patched to inject a scripted ``FunctionModel`` agent) and the model itself. Spans are captured with
``logfire.testing``'s in-memory exporter (``capfire``). It proves the trace a headless run produces:

* the **bypass** flow opens ONE ``decode_run`` root span whose ``thread_id`` equals the run's
  ``current_execution_id()`` (== the returned ``handle.exec_id``), with the pydantic-ai model/tool
  spans nested under it and a leaf model span carrying ``gen_ai.usage.*`` tokens (AC1);
* the **HITL** flow opens a ``decode_run_hitl`` root under the same ``thread_id`` contract (AC2);
* **offline nesting holds** — the single-loop ``FunctionModel`` path exports the whole run as ONE
  trace (AC5). The real-provider caveat (per-call worker loops may sibling some model spans) is a
  documented ceiling in :mod:`decode.runtime.flow`; the token attributes ride regardless of thread;
* **inactive** — with tracing off (no ``OPIK_API_KEY``), both flows emit ZERO spans and return the
  same output as always (AC4).

Activation mirrors :mod:`tests.integration.test_opik_repl_trace`: ``tracing._active`` is forced ``True``
and pydantic-ai is instrumented directly (a fake ``opik_api_key`` is set only for fidelity) rather than
through ``init_tracing`` — whose real ``logfire.configure`` would replace ``capfire``'s exporter and
could flush to the network. Because ``_active`` is already ``True``, the flow's own in-body
``init_tracing()`` is a no-op early-return that leaves ``capfire``'s exporter intact, and the flow's
``root_span`` opens real spans. An autouse fixture saves/restores the global instrumentation + module
flag so nothing leaks across tests; a second autouse fixture redirects the Kitaru/ZenML store under
``tmp_path`` so the live flow runs offline and hermetically (mirrors
:mod:`tests.integration.test_runtime_capstone`).
"""

from __future__ import annotations

import gc
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

import decode.runtime.flow as flow_mod
from decode.config.settings import settings
from decode.observability import tracing
from decode.observability.tracing import reset_tracing
from decode.runtime import run_agent_task, run_hitl_agent_task

# Booting the real Kitaru/ZenML stack emits two third-party deprecation warnings unrelated to decode
# (passlib importing the stdlib ``crypt``; pydantic-ai's sync bridge touching the event loop). Scope
# the ignores here so the strict ``filterwarnings=["error"]`` gate stays green (mirrors test_flow.py).
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]

# Is the durable runtime importable (kitaru + the local ZenML stack)? On the normal CI case — kitaru is
# a hard dependency — these run; on a stripped environment they SKIP rather than fail, mirroring the
# ``test_runtime_capstone`` guard.
try:  # pragma: no cover - import-time capability probe
    import kitaru as _kitaru  # noqa: F401
    import zenml.client as _zenml_client  # noqa: F401

    _LOCAL_KITARU_STACK_AVAILABLE = True
except Exception:  # pragma: no cover - only on an incompatible environment
    _LOCAL_KITARU_STACK_AVAILABLE = False

pytestmark.append(
    pytest.mark.skipif(
        not _LOCAL_KITARU_STACK_AVAILABLE,
        reason="the local Kitaru stack (kitaru + zenml) is not available in this environment",
    )
)

_READ_TARGET = "spec.md"
_READ_CONTENTS = "trace this headless run"


# Hermeticity — redirect the Kitaru/ZenML store under tmp_path and release every straggler (copied
# from test_runtime_capstone: a live @flow leaves ZenML's SQLite engine + the run_sync event loop
# behind, which filterwarnings=["error"] turns into errors when finalized during a later test).


@pytest.fixture(autouse=True)
def isolated_kitaru_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect Kitaru/ZenML's store + config under ``tmp_path`` so the flow runs offline, hermetically."""
    from zenml.client import Client
    from zenml.config.global_config import GlobalConfiguration

    config_dir = tmp_path / "kitaru-config"
    config_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("click.get_app_dir", lambda app_name: str(config_dir))
    monkeypatch.setenv("ZENML_CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("ZENML_ANALYTICS_OPT_IN", "false")
    monkeypatch.chdir(tmp_path)

    GlobalConfiguration._reset_instance()
    Client._reset_instance()
    try:
        yield tmp_path
    finally:
        _dispose_kitaru_engine()
        Client._reset_instance()
        GlobalConfiguration._reset_instance()
        _close_idle_event_loop()
        gc.collect()


def _dispose_kitaru_engine() -> None:
    """Dispose ZenML's live SQLAlchemy engine so its pooled SQLite sockets close deterministically."""
    from zenml.config.global_config import GlobalConfiguration

    store = GlobalConfiguration()._zen_store
    engine = getattr(store, "_engine", None)
    if engine is not None:
        engine.dispose()


def _close_idle_event_loop() -> None:
    """Close the idle event loop ``run_sync`` left as the main thread's current loop, and clear it."""
    import asyncio

    policy = asyncio.get_event_loop_policy()
    loop = getattr(getattr(policy, "_local", None), "_loop", None)
    if loop is not None and not loop.is_running() and not loop.is_closed():
        loop.close()
        asyncio.set_event_loop(None)


# Tracing activation — force _active True against capfire's in-memory exporter (mirrors the REPL
# trace test): the flow's own init_tracing() then early-returns (no reconfigure) and root_span opens
# real spans. An autouse fixture saves/restores the global instrumentation + module flag.


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
    opens real spans (and the flow's in-body ``init_tracing`` early-returns without replacing the
    exporter). A fake ``opik_api_key`` is set only for fidelity with the production trigger — the span
    path never reads it. Restored by :func:`_isolate_tracing_state`.
    """
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("fake-opik-key"), raising=False)
    monkeypatch.setattr(tracing, "_active", True)
    logfire.instrument_pydantic_ai()
    return capfire


# --- scripted durable agents (no network) ------------------------------------------------------


def _bypass_durable(responses: list[ModelResponse]):
    """A scripted bypass ``KitaruAgent`` (``"calls"`` — the real default) for the bypass seam."""
    from kitaru.adapters.pydantic_ai import KitaruAgent

    agent, _counter = make_scripted_agent(responses)
    return KitaruAgent(agent, name=flow_mod.RUNTIME_AGENT_NAME, checkpoint_strategy="calls")


def _hitl_durable(responses: list[ModelResponse]):
    """A scripted HITL ``KitaruAgent`` (via the real ``_to_hitl_durable_agent`` config) for the HITL seam."""
    agent, _counter = make_scripted_agent(responses, name=flow_mod.HITL_RUNTIME_AGENT_NAME)
    return flow_mod._to_hitl_durable_agent(agent)


# --- span selectors ----------------------------------------------------------------------------


def _roots_named(spans: list[dict], name: str) -> list[dict]:
    return [s for s in spans if s["name"] == name]


def _model_spans(spans: list[dict]) -> list[dict]:
    """The pydantic-ai model-request spans (``chat <model>``) — NOT the run root or the ``agent run``."""
    return [s for s in spans if s["name"].startswith("chat ")]


def _tool_spans(spans: list[dict]) -> list[dict]:
    return [s for s in spans if s["name"] == "running tool"]


def test_bypass_run_is_one_decode_run_root_with_nested_spans_and_usage(active_tracing, monkeypatch):
    """AC1 + AC5: one ``decode_run`` root (thread_id = exec_id); model/tool spans nest; usage rides.

    A two-leg bypass run (read a seeded file inline → final text) through the REAL flow. Exactly one
    ``decode_run`` root span is emitted; its ``thread_id`` equals the returned ``handle.exec_id`` (==
    ``current_execution_id()`` read inside the flow). The model + tool spans nest under it (offline the
    single-loop ``FunctionModel`` path exports ONE trace), and a leaf model span carries token usage.
    """
    Path(_READ_TARGET).write_text(_READ_CONTENTS, encoding="utf-8")  # cwd is the isolated tmp_path
    durable = _bypass_durable(
        [
            ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": _READ_TARGET})]),
            ModelResponse(parts=[TextPart(content="read the spec")]),
        ]
    )
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)

    handle = run_agent_task.run(task="read the spec then report")

    assert flow_mod._load_runtime_output(handle.exec_id) == "read the spec"
    spans = active_tracing.exporter.exported_spans_as_dict()
    roots = _roots_named(spans, "decode_run")
    assert len(roots) == 1, [s["name"] for s in spans]
    root = roots[0]
    assert root["parent"] is None, "the decode_run span must be the trace root"
    # AC1: the root thread_id is the Kitaru exec_id (read via current_execution_id() inside the flow).
    assert root["attributes"]["thread_id"] == handle.exec_id
    trace_id = root["context"]["trace_id"]

    model_spans = _model_spans(spans)
    tool_spans = _tool_spans(spans)
    assert model_spans, [s["name"] for s in spans]
    assert tool_spans, "the read tool must produce a 'running tool' span"
    # AC5 (offline): everything the run produced nests under the one root — same trace, not a root.
    for span in model_spans + tool_spans:
        assert span["context"]["trace_id"] == trace_id
        assert span["parent"] is not None

    # AC1: a leaf model span carries ``gen_ai.usage.*`` tokens (> 0).
    input_tokens = [s["attributes"].get("gen_ai.usage.input_tokens") for s in model_spans]
    assert any(tokens and tokens > 0 for tokens in input_tokens), input_tokens


def test_hitl_run_is_one_decode_run_hitl_root_with_the_same_thread_id_contract(
    active_tracing, monkeypatch
):
    """AC2: the HITL flow opens a ``decode_run_hitl`` root under the same ``thread_id`` contract.

    A read-only HITL run (no waits — read-only tools run inline) through the REAL HITL flow. Exactly
    one ``decode_run_hitl`` root span is emitted, its ``thread_id`` equals the run's ``exec_id``, and
    the model/tool spans nest under it (same offline single-trace shape as the bypass flow).
    """
    Path(_READ_TARGET).write_text(_READ_CONTENTS, encoding="utf-8")
    durable = _hitl_durable(
        [
            ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": _READ_TARGET})]),
            ModelResponse(parts=[TextPart(content="read it under HITL")]),
        ]
    )
    monkeypatch.setattr(flow_mod, "_build_hitl_runtime_agent", lambda model=None: durable)

    result = run_hitl_agent_task("read the spec under HITL")

    assert result.paused is False
    assert result.output == "read it under HITL"
    spans = active_tracing.exporter.exported_spans_as_dict()
    roots = _roots_named(spans, "decode_run_hitl")
    assert len(roots) == 1, [s["name"] for s in spans]
    root = roots[0]
    assert root["parent"] is None
    assert root["attributes"]["thread_id"] == result.exec_id
    # No bypass root leaked into the HITL run.
    assert _roots_named(spans, "decode_run") == []
    trace_id = root["context"]["trace_id"]
    nested = _model_spans(spans) + _tool_spans(spans)
    assert nested, [s["name"] for s in spans]
    for span in nested:
        assert span["context"]["trace_id"] == trace_id


def test_inactive_bypass_run_emits_zero_spans_and_returns_the_same_output(capfire, monkeypatch):  # noqa: F811
    """AC4: with tracing OFF, a bypass run emits ZERO spans and returns its normal output.

    No ``active_tracing``: ``tracing._active`` stays False (so the flow's ``root_span`` is a nullcontext
    and its ``init_tracing()`` is a no-op) and pydantic-ai is never instrumented — exactly production
    with no ``OPIK_API_KEY``. capfire still supplies an exporter, so the assertion observes the *absence*
    of any span while the run returns the same text as always.
    """
    assert tracing._active is False
    durable = _bypass_durable([ModelResponse(parts=[TextPart(content="done, untraced")])])
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)

    handle = run_agent_task.run(task="run without tracing")

    assert flow_mod._load_runtime_output(handle.exec_id) == "done, untraced"
    assert capfire.exporter.exported_spans_as_dict() == [], "an inactive run must emit no spans"


def test_inactive_hitl_run_emits_zero_spans_and_returns_the_same_output(capfire, monkeypatch):  # noqa: F811
    """AC4: with tracing OFF, a HITL run emits ZERO spans and returns its normal output."""
    assert tracing._active is False
    durable = _hitl_durable([ModelResponse(parts=[TextPart(content="hitl, untraced")])])
    monkeypatch.setattr(flow_mod, "_build_hitl_runtime_agent", lambda model=None: durable)

    result = run_hitl_agent_task("hitl without tracing")

    assert result.paused is False
    assert result.output == "hitl, untraced"
    assert capfire.exporter.exported_spans_as_dict() == [], (
        "an inactive HITL run must emit no spans"
    )
