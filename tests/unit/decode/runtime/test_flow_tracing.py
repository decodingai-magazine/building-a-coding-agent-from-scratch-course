"""Opik tracing wired into the headless flow seams, on the real local stack (ADR-0014 §4-5, task 093).

Mirrors the two touched ``runtime/flow.py`` seams 1:1 — ``run_agent_task`` (bypass) and
``run_agent_task_hitl`` (HITL) — through the **real** Kitaru ``@flow`` + adapter offline (a scripted
``FunctionModel`` agent injected via the ``_build_runtime_agent`` / ``_build_hitl_runtime_agent`` seam,
the model boundary the only stub). Three wiring points per flow:

* ``observability.init_tracing()`` is called INSIDE the flow body, **before** the root span opens;
* the run is wrapped in ``observability.root_span("decode_run" / "decode_run_hitl",
  thread_id=current_execution_id())`` — the ``thread_id`` equals the returned ``handle.exec_id``;
* ``init_tracing`` runs **after** ``_config_from_secret_store()`` — proven on the real local stack by a
  run whose ``OPIK_API_KEY`` exists ONLY in a Kitaru secret the flow hydrates, with the logfire/OTLP
  boundary mocked so no network happens: the exporter is still built with the secret-sourced key (AC3).

The span *shape* (nesting, tokens, inactive-zero-spans) is the ``logfire.testing`` capstone in
:mod:`tests.integration.test_opik_headless_trace`; here the concern is the flow seams themselves. The
autouse ``isolated_kitaru_store`` (rootdir, gated to this runtime package) redirects the ZenML store
under ``tmp_path``; ``reset_tracing`` is cleared around every test so the module ``_active`` flag never
leaks (the AC3 run flips it via the real ``init_tracing``).
"""

from __future__ import annotations

import contextlib
import logging
from types import SimpleNamespace

import pytest
from kitaru import create_secret
from kitaru.adapters.pydantic_ai import KitaruAgent
from pydantic_ai.messages import ModelResponse, TextPart
from support.runtime_agents import make_scripted_agent

import decode.runtime.flow as flow_mod
from decode.config.settings import reload_settings, settings
from decode.observability.tracing import is_tracing_active, reset_tracing
from decode.runtime import run_agent_task, run_hitl_agent_task

# Booting the real Kitaru/ZenML stack emits two unrelated third-party deprecation warnings (passlib's
# ``crypt``; pydantic-ai's sync-bridge event loop); scope the ignores so the strict gate stays green.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    """Clear the module ``_active`` flag around every test so ``init_tracing`` state never leaks."""
    reset_tracing()
    yield
    reset_tracing()


@pytest.fixture(autouse=True)
def restore_settings_singleton():
    """Snapshot + restore the ``settings`` singleton so a hydrating flow never leaks into the suite."""
    from decode.config.settings import set_secret_hydration_active

    snapshot = dict(settings.__dict__)
    snapshot_fields_set = set(settings.__pydantic_fields_set__)
    try:
        yield
    finally:
        set_secret_hydration_active(False)
        settings.__dict__.clear()
        settings.__dict__.update(snapshot)
        settings.__pydantic_fields_set__.clear()
        settings.__pydantic_fields_set__.update(snapshot_fields_set)


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


# ================================================================================================
# Seam mirror — each flow calls init_tracing() then opens the correctly-named root span whose
# thread_id is the run's exec_id (patched observability seam; the run itself is the real flow).
# ================================================================================================


def test_bypass_flow_inits_tracing_then_opens_decode_run_root_keyed_on_exec_id(mocker):
    """AC1 (seam): ``run_agent_task`` calls ``init_tracing()`` then opens ``decode_run`` (thread_id=exec_id)."""
    init_mock = mocker.patch("decode.observability.init_tracing", return_value=True)

    def _root(*args, **kwargs):
        assert init_mock.called, "init_tracing must run BEFORE the root span opens"
        return contextlib.nullcontext()

    root_mock = mocker.patch("decode.observability.root_span", side_effect=_root)
    monkeypatch_seam(mocker, "_build_runtime_agent", _bypass_durable("all done"))

    handle = run_agent_task.run(task="do the thing")

    assert flow_mod._load_runtime_output(handle.exec_id) == "all done"
    init_mock.assert_called_once_with()
    root_mock.assert_called_once_with("decode_run", thread_id=handle.exec_id)


def test_hitl_flow_inits_tracing_then_opens_decode_run_hitl_root_keyed_on_exec_id(mocker):
    """AC2 (seam): ``run_agent_task_hitl`` calls ``init_tracing()`` then opens ``decode_run_hitl``."""
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
    root_mock.assert_called_once_with("decode_run_hitl", thread_id=result.exec_id)


def test_inactive_bypass_flow_never_opens_a_real_span(mocker):
    """AC4 (seam): with no key, ``root_span`` returns a nullcontext (the run is untraced, byte-identical)."""
    # The real init_tracing (no key via the autouse conftest) + the real root_span run here.
    span_fn = mocker.patch("decode.observability.tracing.logfire.span")
    monkeypatch_seam(mocker, "_build_runtime_agent", _bypass_durable("untraced"))

    handle = run_agent_task.run(task="no key, no trace")

    assert flow_mod._load_runtime_output(handle.exec_id) == "untraced"
    assert is_tracing_active() is False
    span_fn.assert_not_called()  # root_span was a nullcontext — no logfire span ever opened


# ================================================================================================
# AC3 — init_tracing() runs AFTER _config_from_secret_store(): the OPIK_API_KEY lives ONLY in the
# hydrated Kitaru secret, yet the flow's in-body init still builds the exporter with it (boundary
# mocked → no network). This is the ordering proof (an init before hydration would see no key).
# ================================================================================================


@pytest.fixture
def mock_logfire_boundary(mocker):
    """Patch the logfire + OTLP boundary so the real ``init_tracing`` configures nothing real / no network."""
    return SimpleNamespace(
        exporter_cls=mocker.patch("decode.observability.tracing.OTLPSpanExporter"),
        bsp_cls=mocker.patch("decode.observability.tracing.BatchSpanProcessor"),
        configure=mocker.patch("decode.observability.tracing.logfire.configure"),
        instrument=mocker.patch("decode.observability.tracing.logfire.instrument_pydantic_ai"),
        span=mocker.patch("decode.observability.tracing.logfire.span"),
    )


def test_init_tracing_runs_after_secret_store_hydration(
    monkeypatch, mocker, runtime_secret_name, mock_logfire_boundary
):
    """AC3: an ``OPIK_API_KEY`` present ONLY in the Kitaru secret still activates tracing inside the flow.

    The key is absent from the ambient settings (the autouse conftest blanks it) — it lives only in a
    real Kitaru secret. With ``RUNTIME_SECRET_STORE_CONFIG`` on, the flow hydrates the singleton from
    that secret and THEN calls ``init_tracing()``; the mocked ``OTLPSpanExporter`` is built with the
    secret-sourced key in its ``Authorization`` header. An init that ran before hydration would have
    seen an empty key and no-op'd, so the exporter build proves the ordering (init after secret store).
    """
    secret_value = "opik-key-only-in-the-secret-9f3a"
    assert settings.opik_api_key.get_secret_value() == ""  # blanked by the autouse conftest
    create_secret(runtime_secret_name, {"OPIK_API_KEY": secret_value}, private=True)
    monkeypatch.setenv("RUNTIME_SECRET_STORE_CONFIG", "true")
    reload_settings()  # the flow's entry check reads runtime_secret_store_config as True
    monkeypatch_seam(mocker, "_build_runtime_agent", _bypass_durable("secret-keyed run"))

    handle = run_agent_task.run(task="trace me from the secret")

    assert flow_mod._load_runtime_output(handle.exec_id) == "secret-keyed run"
    # The exporter was built INSIDE the flow with the SECRET-sourced key — proving init_tracing ran
    # after _config_from_secret_store hydrated OPIK_API_KEY (never present in the ambient settings).
    mock_logfire_boundary.exporter_cls.assert_called_once()
    headers = mock_logfire_boundary.exporter_cls.call_args.kwargs["headers"]
    assert headers["Authorization"] == secret_value
    # The singleton is restored on flow exit — the hydrated key does not leak past the run.
    assert settings.opik_api_key.get_secret_value() == ""


def test_init_tracing_secret_key_never_appears_in_the_flow_payload(
    monkeypatch, mocker, runtime_secret_name, mock_logfire_boundary
):
    """AC3 corollary: the secret-sourced OPIK key rides the trace config, never the serialized payload."""
    secret_value = "opik-key-not-in-payload-7c21"
    create_secret(runtime_secret_name, {"OPIK_API_KEY": secret_value}, private=True)
    monkeypatch.setenv("RUNTIME_SECRET_STORE_CONFIG", "true")
    reload_settings()
    monkeypatch_seam(mocker, "_build_runtime_agent", _bypass_durable("ok"))

    handle = run_agent_task.run(task="no key in the payload")
    assert flow_mod._load_runtime_output(handle.exec_id) == "ok"

    from zenml.client import Client

    run = Client().get_pipeline_run(handle.exec_id)
    assert set(run.config.parameters) == {"task", "model", "repo", "local"}
    assert secret_value not in run.config.model_dump_json()


# --- helper ------------------------------------------------------------------------------------


def monkeypatch_seam(mocker, seam_name: str, durable: KitaruAgent) -> None:
    """Patch a runtime seam (``_build_runtime_agent`` / ``_build_hitl_runtime_agent``) to a scripted agent."""
    mocker.patch.object(flow_mod, seam_name, lambda model=None: durable)


def test_hydration_logs_do_not_carry_the_opik_secret_value(
    monkeypatch, mocker, runtime_secret_name, mock_logfire_boundary, caplog
):
    """The activation is surfaced via a log line only (no stdout); the raw OPIK key never reaches the logs."""
    secret_value = "opik-key-never-logged-1a2b"
    create_secret(runtime_secret_name, {"OPIK_API_KEY": secret_value}, private=True)
    monkeypatch.setenv("RUNTIME_SECRET_STORE_CONFIG", "true")
    reload_settings()
    monkeypatch_seam(mocker, "_build_runtime_agent", _bypass_durable("ok"))

    with caplog.at_level(logging.INFO):
        handle = run_agent_task.run(task="log check")

    assert flow_mod._load_runtime_output(handle.exec_id) == "ok"
    assert secret_value not in caplog.text  # the key is never logged
    # The one activation INFO line fired (from init_tracing), naming the project, not the key.
    tracing_infos = [
        r
        for r in caplog.records
        if r.name == "decode.observability.tracing" and r.levelno == logging.INFO
    ]
    assert len(tracing_infos) == 1
    assert settings.opik_project_name in tracing_infos[0].getMessage()
