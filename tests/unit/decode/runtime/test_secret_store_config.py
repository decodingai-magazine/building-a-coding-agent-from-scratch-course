"""The Kitaru secret-store config source through the real runtime seam (ADR-0008 §5, task 064).

These run on the **real** local Kitaru stack (offline, no server): a secret is created with
:func:`kitaru.create_secret`, the headless flow hydrates the ``settings`` singleton from it, and a
seam captures the config ``build_agent`` would see *inside* the flow. The model boundary is a
scripted ``FunctionModel`` (no network). Covers hydration precedence, singleton restore on success
and error, and the never-leak-a-secret payload/log invariants.
"""

from __future__ import annotations

import logging
import os

import pytest
from kitaru import create_secret
from kitaru.adapters.pydantic_ai import KitaruAgent
from pydantic_ai.messages import ModelResponse, TextPart
from support.runtime_agents import make_scripted_agent

import decode.runtime.flow as flow_mod
from decode.agent.factory import build_agent
from decode.config.settings import (
    Settings,
    is_secret_hydration_active,
    reload_settings,
    set_secret_hydration_active,
    settings,
)
from decode.runtime import run_agent_task

# Booting the real Kitaru/ZenML stack emits two unrelated third-party deprecation warnings (see
# test_flow.py); scope the ignores here so the strict ``filterwarnings=["error"]`` gate stays green.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]

# The Kitaru secret name comes from the ``runtime_secret_name`` fixture (a unique per-test
# ``decode-test-creds-<uuid>`` wired into BOTH ``settings.runtime_secret_name`` and the
# ``RUNTIME_SECRET_NAME`` env var, so the in-flow ``reload_settings`` keeps it) — never the hardcoded
# production default — so a hypothetical store-isolation fall-through can never collide with or leave
# a real-store ``decode-llm-creds`` (task 065).
# Vars cleared from the real env so the Kitaru secret is the authoritative source in the assertions.
_CLEARED_PROVIDER_ENV = ("GEMINI_API_KEY", "GEMINI_MODEL", "LLM_PROVIDER")


@pytest.fixture(autouse=True)
def restore_settings_singleton():
    """Snapshot + restore the ``settings`` singleton so a hydrating flow never leaks into the suite.

    The hydration mutates the module-level singleton in place; its own ``finally`` restores it, but
    this fixture is the belt-and-braces guarantee the *whole test file* leaves the singleton (and the
    hydration flag) byte-identical for every other test — the highest-risk hermeticity concern.
    """
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


def _enable_secret_store(monkeypatch) -> None:
    """Turn the secret-store source on via the env and sync the singleton (so context entry sees it).

    ``RUNTIME_SECRET_STORE_CONFIG`` is set in the real env (so the in-flow ``reload_settings`` keeps
    it on) and the singleton is reloaded once here so the flow's entry check reads it as ``True``.
    The provider vars are cleared so a value present only in the secret is unambiguously from it.
    """
    monkeypatch.setenv("RUNTIME_SECRET_STORE_CONFIG", "true")
    for var in _CLEARED_PROVIDER_ENV:
        monkeypatch.delenv(var, raising=False)
    reload_settings()


def _scripted_durable(text: str = "ok") -> KitaruAgent:
    """A scripted offline ``KitaruAgent`` returning ``text`` (the model boundary stays offline).

    Uses ``checkpoint_strategy="calls"`` (also the settings default) so these runs read their output from
    the ``_capture_runtime_output`` artifact via :func:`flow_mod._load_runtime_output` (``.wait()`` cannot
    extract under ``"calls"``); see :func:`_run_and_read`.
    """
    scripted, _counter = make_scripted_agent([ModelResponse(parts=[TextPart(content=text)])])
    return KitaruAgent(scripted, name="decode-runtime", checkpoint_strategy="calls")


def _run_and_read(task: str) -> str:
    """Run the bypass flow to completion and read its final text from the output artifact (task 068).

    The bypass ``decode run`` no longer uses ``.wait().output`` — under the ``"calls"`` default the run
    ends in several terminal per-call checkpoints Kitaru cannot auto-extract from, so the flow saves
    its text via ``_capture_runtime_output`` and we read it back by artifact name (mirrors the CLI).
    ``run(...)`` runs to completion in-process on the local stack (bypass never pauses).
    """
    handle = run_agent_task.run(task=task)
    return flow_mod._load_runtime_output(handle.exec_id)


def test_headless_flow_hydrates_settings_seen_by_build_agent(monkeypatch, runtime_secret_name):
    create_secret(
        runtime_secret_name,
        {
            "LLM_PROVIDER": "gemini",
            "GEMINI_MODEL": "gemini-from-secret",
            "GEMINI_API_KEY": "sk-secret-only",
        },
        private=True,
    )
    _enable_secret_store(monkeypatch)
    seen: dict[str, str] = {}

    def _seam(model: str | None = None) -> KitaruAgent:
        # Stands in for ``_build_runtime_agent`` → ``build_agent``: record the hydrated config the
        # real factory would read at this point, then run the turn on a scripted offline model.
        seen["provider"] = settings.llm_provider
        seen["model"] = settings.gemini_model
        seen["key"] = settings.gemini_api_key.get_secret_value()
        return _scripted_durable()

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _seam)

    assert _run_and_read("hydrate me") == "ok"
    assert seen["provider"] == "gemini"
    assert seen["model"] == "gemini-from-secret"  # not in the real env — only in the secret
    assert seen["key"] == "sk-secret-only"


def test_real_env_overrides_kitaru_secret_in_flow(monkeypatch, runtime_secret_name):
    create_secret(runtime_secret_name, {"GEMINI_MODEL": "gemini-from-secret"}, private=True)
    monkeypatch.setenv("RUNTIME_SECRET_STORE_CONFIG", "true")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-from-env")  # real env wins
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    reload_settings()
    seen: dict[str, str] = {}

    def _seam(model: str | None = None) -> KitaruAgent:
        seen["model"] = settings.gemini_model
        return _scripted_durable()

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _seam)

    assert _run_and_read("env wins") == "ok"

    assert seen["model"] == "gemini-from-env"


def test_hydration_never_writes_os_environ(monkeypatch, runtime_secret_name):
    create_secret(
        runtime_secret_name,
        {"GEMINI_MODEL": "gemini-from-secret", "GEMINI_API_KEY": "sk-secret-not-in-env"},
        private=True,
    )
    _enable_secret_store(monkeypatch)
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: _scripted_durable())
    env_before = dict(os.environ)

    assert _run_and_read("no env writes") == "ok"

    assert dict(os.environ) == env_before  # nothing added/changed
    assert "sk-secret-not-in-env" not in os.environ.values()


def test_context_restores_settings_on_success(monkeypatch, runtime_secret_name):
    create_secret(runtime_secret_name, {"GEMINI_MODEL": "gemini-from-secret"}, private=True)
    _enable_secret_store(monkeypatch)
    before_model = settings.gemini_model  # the default — GEMINI_MODEL was cleared from the env

    with flow_mod._config_from_secret_store():
        assert settings.gemini_model == "gemini-from-secret"  # hydrated inside the context

    assert settings.gemini_model == before_model  # restored on exit
    assert is_secret_hydration_active() is False


def test_context_restores_settings_on_error(monkeypatch, runtime_secret_name):
    create_secret(runtime_secret_name, {"GEMINI_MODEL": "gemini-from-secret"}, private=True)
    _enable_secret_store(monkeypatch)
    before_model = settings.gemini_model

    with pytest.raises(RuntimeError, match="boom"), flow_mod._config_from_secret_store():
        assert settings.gemini_model == "gemini-from-secret"
        raise RuntimeError("boom")

    assert settings.gemini_model == before_model
    assert is_secret_hydration_active() is False
    # A subsequent in-process ``Settings`` read sees the original config (the source is inert again).
    fresh = Settings(_env_file=None)
    assert fresh.gemini_model == before_model


def test_context_is_a_noop_when_flag_off(monkeypatch):
    monkeypatch.delenv("RUNTIME_SECRET_STORE_CONFIG", raising=False)
    reload_settings()
    assert settings.runtime_secret_store_config is False
    snapshot = dict(settings.__dict__)

    with flow_mod._config_from_secret_store():
        assert settings.__dict__ == snapshot  # no hydration happened
        assert is_secret_hydration_active() is False

    assert settings.__dict__ == snapshot


def test_hydration_logs_field_names_not_secret_values(monkeypatch, caplog, runtime_secret_name):
    sentinel = "SENTINEL-SECRET-VALUE-9f3a"
    create_secret(
        runtime_secret_name,
        {"GEMINI_API_KEY": sentinel, "GEMINI_MODEL": "gemini-from-secret"},
        private=True,
    )
    _enable_secret_store(monkeypatch)

    with (
        caplog.at_level(logging.DEBUG, logger="decode.config.settings"),
        flow_mod._config_from_secret_store(),
    ):
        pass

    assert sentinel not in caplog.text  # the raw value never reaches the logs
    assert "gemini_api_key" in caplog.text  # but the field name is logged for observability


def test_flow_payload_carries_only_the_task_not_the_secret_value(monkeypatch, runtime_secret_name):
    sentinel = "SENTINEL-SECRET-VALUE-payload-7c21"
    create_secret(
        runtime_secret_name,
        {"GEMINI_MODEL": "gemini-from-secret", "GEMINI_API_KEY": sentinel},
        private=True,
    )
    _enable_secret_store(monkeypatch)
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: _scripted_durable())

    handle = run_agent_task.run(task="summarize the repository")
    assert flow_mod._load_runtime_output(handle.exec_id) == "ok"

    from zenml.client import Client

    run = Client().get_pipeline_run(handle.exec_id)
    # task + the Model Override + the Workspace clone inputs (repo/local, ADR-0012 §3) ride as flow
    # params (ADR-0010 §2); none is a secret, and the hydrated secret value appears nowhere.
    assert set(run.config.parameters) == {"task", "model", "repo", "local"}
    assert run.config.parameters["task"] == "summarize the repository"
    assert sentinel not in run.config.model_dump_json()


def test_both_flags_on_produce_a_coherent_run_with_no_raw_key_leak(
    monkeypatch, runtime_secret_name
):
    raw_key = "RAW-KITARU-GEMINI-KEY-both-flags-1a2b"
    create_secret(
        runtime_secret_name,
        {
            "LLM_PROVIDER": "gemini",
            "GEMINI_MODEL": "gemini-from-secret",
            "GEMINI_API_KEY": raw_key,
        },
        private=True,
    )
    # Both flags via the env so the in-flow reload preserves them; provider vars cleared.
    monkeypatch.setenv("RUNTIME_SECRET_STORE_CONFIG", "true")
    monkeypatch.setenv("RUNTIME_CREDENTIALS_PROXY_ENABLED", "true")
    for var in _CLEARED_PROVIDER_ENV:
        monkeypatch.delenv(var, raising=False)
    reload_settings()
    observed: dict[str, str] = {}

    def _seam(model: str | None = None) -> KitaruAgent:
        # Real build_agent(flow_mode=True): secret-store hydrated the model id; the Credentials Proxy
        # resolves the key from the SAME secret. Then run the turn on a scripted offline model.
        agent = build_agent(flow_mode=True)
        observed["model"] = settings.gemini_model
        observed["resolved_key"] = agent.model._provider.client._api_client.api_key
        return _scripted_durable()

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _seam)

    handle = run_agent_task.run(task="both flags on")
    assert flow_mod._load_runtime_output(handle.exec_id) == "ok"

    assert observed["model"] == "gemini-from-secret"  # secret-store hydrated the model
    assert observed["resolved_key"] == raw_key  # the proxy resolved the key from the same secret

    from zenml.client import Client

    run = Client().get_pipeline_run(handle.exec_id)
    # task + the Model Override + the Workspace clone inputs (repo/local, ADR-0012 §3) ride as flow
    # params (ADR-0010 §2); none is a secret, and the raw key appears nowhere in the payload.
    assert set(run.config.parameters) == {"task", "model", "repo", "local"}
    assert raw_key not in run.config.model_dump_json()  # no raw key in the payload
