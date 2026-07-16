"""Opik tracing wired into the headless flow seams — bypass + HITL (ADR-0014 §4-5, task 093).

Drives the real Kitaru ``@flow`` offline with a scripted ``FunctionModel`` agent injected via the
``_build_runtime_agent`` / ``_build_hitl_runtime_agent`` seams. Each flow must call ``init_tracing()``
before opening its root span (``thread_id`` = the run's exec_id) — including on the exception unwind.
Config hydration is process-scoped (ADR-0015 §5), so a bucket-sourced ``OPIK_API_KEY`` is simply
already in ``settings`` here. Span *shape* is the integration capstone's concern.
"""

from __future__ import annotations

import contextlib

import logfire
import pytest
from kitaru.adapters.pydantic_ai import KitaruAgent
from logfire.testing import (
    CaptureLogfire,
    capfire,  # noqa: F401 — imported so pytest registers the in-memory fixture
)
from pydantic import SecretStr
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from support.runtime_agents import make_scripted_agent

import decode.runtime.flow as flow_mod
from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.observability import tracing
from decode.observability.tracing import is_tracing_active, reset_tracing
from decode.runtime import run_agent_task, run_hitl_agent_task
from decode.tools.registry import register_tools

# Booting the real Kitaru/ZenML stack emits two unrelated third-party deprecation warnings (passlib's
# ``crypt``; pydantic-ai's sync-bridge event loop); scope the ignores so the strict gate stays green.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    """Clear the module ``_active`` flag + restore global instrumentation so tracing never leaks.

    Extends the flag reset with a save/restore of ``Agent._instrument_default``: the active-tracing
    raise-unwind test (below) instruments pydantic-ai globally, and without this restore every later
    test's agents would stay instrumented (mirrors the 092/093 span-test isolation fixture).
    """
    prior_instrument = Agent._instrument_default
    reset_tracing()
    try:
        yield
    finally:
        Agent.instrument_all(prior_instrument)
        reset_tracing()


@pytest.fixture
def active_tracing(monkeypatch, capfire) -> CaptureLogfire:  # noqa: F811
    """Turn tracing ON against ``capfire``'s in-memory exporter and instrument pydantic-ai.

    Mirrors :mod:`tests.integration.test_opik_headless_trace`: ``capfire`` configures logfire with the
    in-memory exporter FIRST, then we set ``_active`` (so the flow's ``root_span`` opens real spans and
    its in-body ``init_tracing`` early-returns without replacing the exporter) and instrument
    pydantic-ai. The fake ``opik_api_key`` is only for fidelity — the span path never reads it.
    """
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("fake-opik-key"), raising=False)
    monkeypatch.setattr(tracing, "_active", True)
    logfire.instrument_pydantic_ai()
    return capfire


def _bypass_durable(text: str = "ok") -> KitaruAgent:
    """A scripted bypass ``KitaruAgent`` (``"calls"`` — the settings default) for the bypass seam."""
    agent, _counter = make_scripted_agent([ModelResponse(parts=[TextPart(content=text)])])
    return KitaruAgent(agent, name=flow_mod.RUNTIME_AGENT_NAME, checkpoint_strategy="calls")


def _hitl_durable(text: str = "ok") -> KitaruAgent:
    """A scripted HITL ``KitaruAgent`` (via the real ``_to_hitl_durable_agent`` config) for the HITL seam."""
    agent, _counter = make_scripted_agent(
        [ModelResponse(parts=[TextPart(content=text)])], name=flow_mod.HITL_RUNTIME_AGENT_NAME
    )
    return flow_mod._to_hitl_durable_agent(agent)


def _raising_durable(message: str) -> KitaruAgent:
    """A real bypass ``KitaruAgent`` whose model leg RAISES ``message`` mid-run (a headless failure)."""

    def raising_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError(message)

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(raising_model),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        name=flow_mod.RUNTIME_AGENT_NAME,
    )
    register_tools(agent)
    return KitaruAgent(agent, name=flow_mod.RUNTIME_AGENT_NAME, checkpoint_strategy="calls")


def _exception_carries(exc: BaseException, message: str) -> bool:
    """Whether ``message`` appears anywhere in the exception tree (direct, chained, or grouped).

    Kitaru's ``run_sync`` surfaces a model failure wrapped (an ``ExceptionGroup`` around the original
    ``RuntimeError``), so an equality check on the top-level type is too brittle; this walks
    ``__cause__`` / ``__context__`` / ``ExceptionGroup.exceptions`` to prove the ORIGINAL error rode
    out unchanged.
    """
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if message in str(current):
            return True
        stack.extend([current.__cause__, current.__context__])
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
    return False


# Seam mirror — each flow calls init_tracing() then opens the correctly-named root span whose
# thread_id is the run's exec_id (patched observability seam; the run itself is the real flow).


def test_bypass_flow_inits_tracing_then_opens_decode_run_root_keyed_on_exec_id(mocker):
    init_mock = mocker.patch("decode.observability.init_tracing", return_value=True)

    def _root(*args, **kwargs):
        assert init_mock.called, "init_tracing must run BEFORE the root span opens"
        return contextlib.nullcontext()

    root_mock = mocker.patch("decode.observability.root_span", side_effect=_root)
    monkeypatch_seam(mocker, "_build_runtime_agent", _bypass_durable("all done"))

    handle = run_agent_task.run(task="do the thing")

    assert flow_mod._load_runtime_output(handle.exec_id) == "all done"
    init_mock.assert_called_once_with()
    root_mock.assert_called_once_with("decode_run", thread_id=handle.exec_id, input="do the thing")


def test_hitl_flow_inits_tracing_then_opens_decode_run_hitl_root_keyed_on_exec_id(mocker):
    init_mock = mocker.patch("decode.observability.init_tracing", return_value=True)

    def _root(*args, **kwargs):
        assert init_mock.called, "init_tracing must run BEFORE the root span opens"
        return contextlib.nullcontext()

    root_mock = mocker.patch("decode.observability.root_span", side_effect=_root)
    monkeypatch_seam(mocker, "_build_hitl_runtime_agent", _hitl_durable("hitl done"))

    result = run_hitl_agent_task("do the thing under HITL")

    assert result.paused is False
    assert result.output == "hitl done"
    init_mock.assert_called_once_with()
    root_mock.assert_called_once_with(
        "decode_run_hitl", thread_id=result.exec_id, input="do the thing under HITL"
    )


def test_inactive_bypass_flow_never_opens_a_real_span(mocker):
    # The real init_tracing (no key via the autouse conftest) + the real root_span run here.
    span_fn = mocker.patch("decode.observability.tracing.logfire.span")
    monkeypatch_seam(mocker, "_build_runtime_agent", _bypass_durable("untraced"))

    handle = run_agent_task.run(task="no key, no trace")

    assert flow_mod._load_runtime_output(handle.exec_id) == "untraced"
    assert is_tracing_active() is False
    span_fn.assert_not_called()  # root_span was a nullcontext — no logfire span ever opened


def test_bypass_flow_raise_with_tracing_active_closes_decode_run_span_once(active_tracing, mocker):
    """093-QA follow-up: a raising model leg with tracing active closes ``decode_run`` once + reraises.

    The 093 Tester's recommended hardening (its Log's "PROBE 1"), committed: harden against a future
    refactor that swaps the flow's ``with root_span(...)`` for a manual enter/exit. With tracing active,
    a model failure inside ``run_sync`` unwinds ``logfire.span.__exit__`` — closing the ``decode_run``
    root EXACTLY once and recording the exception — and the original error still propagates out of
    ``.run()`` unchanged (neither swallowed nor mangled by the span). A captured span is one that ENDED,
    so exactly one exported ``decode_run`` proves it closed once and never leaked. Under the file's
    ``filterwarnings=["error"]`` gate. (Span *shape* is the ``logfire.testing`` capstone in
    :mod:`tests.integration.test_observability_capstone`; here the concern is the flow's unwind path.)
    """
    boom = "boom-in-headless-model"
    monkeypatch_seam(mocker, "_build_runtime_agent", _raising_durable(boom))

    with pytest.raises(BaseException) as exc_info:
        run_agent_task.run(task="make the headless model blow up")

    # The model failure propagated out of ``.run()`` unchanged (found in the exception tree).
    assert _exception_carries(exc_info.value, boom), "the model failure must propagate unchanged"

    spans = active_tracing.exporter.exported_spans_as_dict()
    roots = [s for s in spans if s["name"] == "decode_run"]
    assert len(roots) == 1, "the decode_run root must close EXACTLY once on the exception unwind"
    root = roots[0]
    assert root["parent"] is None
    # The span recorded the error: logfire error level (17) + an ``exception`` event.
    assert root["attributes"]["logfire.level_num"] == 17
    assert "exception" in [e.get("name") for e in (root.get("events") or [])]


# Hydration is process-scoped now (ADR-0015 §5): a bucket-hydrated OPIK_API_KEY is already in
# ``settings`` when the flow starts, so the old in-flow hydration context — and the "init_tracing runs
# after it" ordering slices that hung off it — are gone. The remaining ordering that matters,
# init_tracing before the root span opens, is asserted in the seam-mirror tests above. The bucket
# source's own behaviour (hydration, precedence, never-os.environ, names-not-values logging) lives in
# tests/unit/decode/config/test_env_bucket.py.


def monkeypatch_seam(mocker, seam_name: str, durable: KitaruAgent) -> None:
    """Patch a runtime seam (``_build_runtime_agent`` / ``_build_hitl_runtime_agent``) to a scripted agent."""
    mocker.patch.object(flow_mod, seam_name, lambda model=None: durable)
