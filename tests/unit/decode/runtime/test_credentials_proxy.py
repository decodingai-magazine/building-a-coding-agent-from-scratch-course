"""The Credentials Proxy through the real runtime seam + the payload invariant (ADR-0008 §5, task 061).

These run on the **real** local Kitaru stack (offline, no server) so the secrets round-trip is the
genuine one, not a mock: a secret is created with :func:`kitaru.create_secret` and read back through
the live ``build_agent(flow_mode=True)`` → ``_build_model`` → ``get_secret`` seam. The verify-first
finding (task log) is that this explicit-handle path round-trips fully offline on kitaru 0.18, so it
is what ships (not the env-injection fallback).

The payload test then proves the AGENTS.md invariant *"secrets never reach the model or the sandbox
payload"*: even when the proxy resolves a raw key inside the flow body, the serialized flow arguments
(``run.config.parameters``) carry only non-secret inputs — the task string, the Model Override
(``model=None`` here), and the Workspace clone inputs (``repo``/``local``, ADR-0012 §3) — never a
credential (a model id / repo path is not a secret, ADR-0010 §2).
"""

from __future__ import annotations

import pytest
from kitaru import create_secret
from kitaru.adapters.pydantic_ai import KitaruAgent
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, TextPart
from support.runtime_agents import make_scripted_agent

import decode.agent.factory as factory_mod
import decode.runtime.flow as flow_mod
from decode.agent.factory import build_agent
from decode.runtime import run_agent_task

# Booting the real Kitaru/ZenML stack emits two unrelated third-party deprecation warnings (see
# test_flow.py); scope the ignores here so the strict ``filterwarnings=["error"]`` gate stays green.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]

_KITARU_RAW_KEY = "KITARU-RAW-GEMINI-KEY-4be1f"
_SETTINGS_RAW_KEY = "SETTINGS-RAW-GEMINI-KEY-must-not-be-used"

# The Kitaru secret name comes from the ``runtime_secret_name`` fixture (a unique per-test
# ``decode-test-creds-<uuid>`` wired into ``settings.runtime_secret_name``) — never the hardcoded
# production default — so a hypothetical store-isolation fall-through can never collide with or leave
# a real-store ``decode-llm-creds`` (task 065).


def _enable_proxy(monkeypatch) -> None:
    """Turn the proxy on for gemini, with the settings key set to a never-to-be-used sentinel.

    ``runtime_secret_name`` is set by the same-named fixture (unique per test), not here.
    """
    monkeypatch.setattr(factory_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(factory_mod.settings, "gemini_model", "gemini-2.5-flash")
    monkeypatch.setattr(factory_mod.settings, "gemini_api_key", SecretStr(_SETTINGS_RAW_KEY))
    monkeypatch.setattr(factory_mod.settings, "runtime_credentials_proxy_enabled", True)


def test_real_kitaru_secret_round_trips_through_build_agent_in_flow_mode(
    monkeypatch, runtime_secret_name
):
    """End-to-end through the seam: a real Kitaru secret supplies the gemini key, not settings.

    Uses the **live** local-stack ``create_secret`` / ``get_secret`` round-trip (no mock) — the
    verify-first proof that the explicit-handle path works offline on kitaru 0.18.
    """
    create_secret(runtime_secret_name, {"GEMINI_API_KEY": _KITARU_RAW_KEY}, private=True)
    _enable_proxy(monkeypatch)

    agent = build_agent(flow_mode=True)

    resolved = agent.model._provider.client._api_client.api_key
    assert resolved == _KITARU_RAW_KEY  # the key came from Kitaru
    assert resolved != _SETTINGS_RAW_KEY  # ...not from settings


def test_build_runtime_agent_resolves_the_key_via_the_proxy(monkeypatch, runtime_secret_name):
    """The runtime seam wires ``flow_mode=True`` through, so ``_build_runtime_agent`` uses the proxy.

    Proves the wiring (``_build_runtime_agent`` → ``build_agent(flow_mode=True)``): with the proxy on
    and the settings key set to a sentinel, the wrapped agent's model carries the *Kitaru* key.
    """
    create_secret(runtime_secret_name, {"GEMINI_API_KEY": _KITARU_RAW_KEY}, private=True)
    _enable_proxy(monkeypatch)

    durable = flow_mod._build_runtime_agent()

    assert isinstance(durable, KitaruAgent)
    assert durable.model._provider.client._api_client.api_key == _KITARU_RAW_KEY


def test_flow_payload_carries_only_the_task_not_the_raw_key(monkeypatch, runtime_secret_name):
    """The serialized flow arguments carry only the task — never the proxy-resolved raw key.

    The patched seam first calls the real ``build_agent(flow_mode=True)`` (so the proxy genuinely
    resolves the raw key inside the flow body), then runs the turn on a scripted offline model. After
    the run, the persisted execution's input parameters (``run.config.parameters``) are inspected:
    they hold only ``{"task", "model", "repo", "local"}`` (the Model Override + Workspace clone inputs
    ride as flow params, ADR-0010 §2 / ADR-0012 §3), and the raw key appears nowhere in the serialized
    config. This is the AGENTS.md "secrets never reach the ... payload" invariant, proven on the real store.
    """
    create_secret(runtime_secret_name, {"GEMINI_API_KEY": _KITARU_RAW_KEY}, private=True)
    _enable_proxy(monkeypatch)

    def _seam(model: str | None = None) -> KitaruAgent:
        # Resolve the real proxy key (it materializes in the discarded real agent), then run the
        # turn on a scripted model so the flow stays offline (no network model call). ``"calls"`` is
        # the settings default since task 068, so the output is read from the artifact below.
        build_agent(flow_mode=True)
        scripted, _counter = make_scripted_agent([ModelResponse(parts=[TextPart(content="done")])])
        return KitaruAgent(scripted, name="decode-runtime", checkpoint_strategy="calls")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _seam)

    handle = run_agent_task.run(task="summarize the repository")
    assert flow_mod._load_runtime_output(handle.exec_id) == "done"

    from zenml.client import Client

    run = Client().get_pipeline_run(handle.exec_id)
    # The persisted flow arguments are the task string + the Model Override input (``model=None``
    # here) + the Workspace clone inputs (``repo``/``local``, ADR-0012 §3) — no credential rides in
    # the payload; a model id / repo path is not a secret (ADR-0010 §2).
    assert set(run.config.parameters) == {"task", "model", "repo", "local"}
    assert run.config.parameters["task"] == "summarize the repository"
    assert _KITARU_RAW_KEY not in run.config.model_dump_json()
    assert _SETTINGS_RAW_KEY not in run.config.model_dump_json()
