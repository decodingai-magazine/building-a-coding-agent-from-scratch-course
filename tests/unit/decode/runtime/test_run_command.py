"""``decode run "<task>"`` — the headless subcommand end to end (ADR-0008).

Drives the real Click ``run`` subcommand through ``CliRunner``, with the model boundary swapped via
the ``_build_runtime_agent`` seam and the Kitaru store isolated (the autouse fixture in this
package's ``conftest``). Covers the happy path (prints the agent's text) and both guards
(``RUNTIME_ENABLED=false`` and the provider-config guard) — each a friendly stderr line + non-zero
exit that never builds a flow.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from pydantic import SecretStr, ValidationError
from pydantic_ai.messages import ModelResponse, TextPart

import decode.cli as cli_mod
import decode.runtime.flow as flow_mod
from decode.cli import cli
from tests.unit.decode.runtime.conftest import make_scripted_agent

# The real flow boots the Kitaru/ZenML stack; scope its two third-party deprecation warnings (see
# test_flow.py) so the strict ``filterwarnings=["error"]`` gate stays green here too.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


@pytest.fixture
def _provider_ok(monkeypatch):
    """Seed the gemini provider config so the ``decode run`` provider guard passes (offline)."""
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)


def _patch_seam(monkeypatch, text):
    """Point the runtime seam at a scripted agent returning ``text``; return its leg counter."""
    from kitaru.adapters.pydantic_ai import KitaruAgent

    agent, counter = make_scripted_agent([ModelResponse(parts=[TextPart(content=text)])])
    durable = KitaruAgent(agent, name="decode-runtime", checkpoint_strategy="turn")
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda: durable)
    return counter


def test_run_command_prints_the_agents_output(monkeypatch, _provider_ok):
    """``decode run "<task>"`` runs the flow and prints the agent's final text, exiting zero."""
    _patch_seam(monkeypatch, "the headless answer")

    result = CliRunner().invoke(cli, ["run", "summarize the cli module"])

    assert result.exit_code == 0
    assert "the headless answer" in result.output


def test_run_command_disabled_runtime_guard_does_not_build_a_flow(monkeypatch, _provider_ok):
    """``RUNTIME_ENABLED=false`` → one friendly stderr line, non-zero exit, and no flow is built."""
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", False)
    built = {"seam": False}

    def _tripwire():
        built["seam"] = True
        raise AssertionError("the flow must not be built when the runtime is disabled")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _tripwire)

    result = CliRunner().invoke(cli, ["run", "do it"])

    assert result.exit_code != 0
    assert "headless runtime is disabled" in result.stderr
    assert "RUNTIME_ENABLED=true" in result.stderr
    assert built["seam"] is False


def test_run_command_provider_guard_fires_without_a_key(monkeypatch):
    """A missing provider key trips the same guard as the REPL: friendly line, non-zero, no flow."""
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)

    def _tripwire():
        raise AssertionError("the flow must not be built when the provider config is missing")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _tripwire)

    result = CliRunner().invoke(cli, ["run", "do it"])

    assert result.exit_code != 0
    assert "GEMINI_API_KEY" in result.stderr


# --- Credentials proxy: missing/incomplete Kitaru secret is a friendly line, not a traceback ----
# (task 061 QA blocker — User Story #3 "opt-in and safe by default"). The proxy-aware pre-flight
# resolves the Kitaru secret BEFORE building the durable flow, so a missing/incomplete secret exits
# with one friendly stderr line naming ``kitaru secrets set`` — never the ~30-frame KitaruRuntimeError
# traceback the unguarded ``run_agent_task.run(...).wait()`` used to dump.

_SECRET_NAME = "decode-llm-creds"


@pytest.fixture
def _proxy_on(monkeypatch):
    """Enable the credentials proxy for gemini with the runtime on (the secret is created per test)."""
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_credentials_proxy_enabled", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_secret_name", _SECRET_NAME)


def _no_flow_tripwires(monkeypatch):
    """Make both runtime seams blow up if reached — the guard must exit before any flow is built."""

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("no flow may be built when the Kitaru secret is missing/incomplete")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _tripwire)
    monkeypatch.setattr(flow_mod, "_build_hitl_runtime_agent", _tripwire)


def test_run_command_proxy_missing_secret_is_a_friendly_line_not_a_traceback(
    monkeypatch, _proxy_on
):
    """Scenario B: proxy ON + a leftover settings key + NO secret → friendly line, no raw traceback.

    The realistic regression: an operator who used the REPL still has ``GEMINI_API_KEY`` in ``.env``,
    flips the proxy on, and forgets ``kitaru secrets set``. The old guard passed on the stale settings
    key and the unguarded flow then dumped a ``KitaruRuntimeError`` traceback. Now the pre-flight names
    the real fix and exits cleanly.
    """
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr("leftover-from-the-repl"))
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    # The raw credential error did not escape as a traceback.
    assert not isinstance(result.exception, RuntimeError)
    # The friendly line names the Kitaru secret + the real fix, not the misleading settings message.
    assert _SECRET_NAME in result.stderr
    assert "kitaru secrets set" in result.stderr
    assert "set GEMINI_API_KEY in your environment" not in result.stderr


def test_run_command_proxy_no_settings_key_names_the_secret_not_the_settings_var(
    monkeypatch, _proxy_on
):
    """Scenario A: proxy ON + NO settings key + NO secret → the line names the secret, not settings.

    With the proxy on the key comes from Kitaru, so the old ``set GEMINI_API_KEY`` message misdirected.
    The proxy-aware guard points the operator at ``kitaru secrets set`` instead.
    """
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert "kitaru secrets set" in result.stderr
    assert "set GEMINI_API_KEY in your environment" not in result.stderr


def test_run_command_proxy_secret_missing_provider_key_is_friendly(monkeypatch, _proxy_on):
    """Proxy ON + the secret exists but lacks ``GEMINI_API_KEY`` → a friendly line, non-zero, no flow."""
    from kitaru import create_secret

    create_secret(_SECRET_NAME, {"SOME_OTHER_KEY": "x"}, private=True)
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr("leftover-from-the-repl"))
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, RuntimeError)
    assert _SECRET_NAME in result.stderr
    assert "GEMINI_API_KEY" in result.stderr


def test_run_hitl_proxy_missing_secret_is_a_friendly_line_not_a_traceback(monkeypatch, _proxy_on):
    """``decode run --hitl`` shares the proxy-aware pre-flight: missing secret → friendly line, no flow."""
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr("leftover-from-the-repl"))
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--hitl", "create config.toml"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, RuntimeError)
    assert _SECRET_NAME in result.stderr
    assert "kitaru secrets set" in result.stderr


def test_run_command_proxy_with_a_valid_secret_runs_the_flow(monkeypatch, _proxy_on):
    """Proxy ON + a valid secret → the pre-flight passes and the flow runs, printing the output.

    Confirms the proxy-aware guard does not block the happy path: with the Kitaru secret present, the
    pre-flight resolves it, the flow is built (here the scripted seam), and the agent's text prints.
    """
    from kitaru import create_secret

    create_secret(_SECRET_NAME, {"GEMINI_API_KEY": "real-kitaru-key"}, private=True)
    _patch_seam(monkeypatch, "the proxied answer")

    result = CliRunner().invoke(cli, ["run", "summarize the repo"])

    assert result.exit_code == 0
    assert "the proxied answer" in result.output


# --- Secret-store config source: the `decode run` guard is RUNTIME_SECRET_STORE_CONFIG-aware --------
# (task 064 follow-up). When the secret-store source is on, the provider config (key/model/tuning) is
# hydrated from a Kitaru secret — but the cli's provider-config guard runs BEFORE the flow hydrates, so
# without a pre-flight a key living only in the secret tripped the misleading ``set GEMINI_API_KEY``
# line and a missing/malformed secret dumped a deep traceback from inside the flow. The pre-flight
# (mirroring the 061 ``_proxy_credential_error``) hydrates + validates up front: a secret-only key
# satisfies the guard, and a missing/malformed secret is one friendly stderr line, never a traceback.


@pytest.fixture
def _secret_store_on(monkeypatch):
    """Enable the secret-store config source for gemini, runtime on, proxy off (secret created per test).

    Provider vars are cleared from the real env so a key/model living only in the Kitaru secret is the
    unambiguous source. The flag is set on the singleton directly; the source keys off the in-flow
    hydration flag the context manager flips, so this is enough for the cli pre-flight to engage it.
    """
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_secret_store_config", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_credentials_proxy_enabled", False)
    monkeypatch.setattr(cli_mod.settings, "runtime_secret_name", _SECRET_NAME)
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    for var in ("GEMINI_API_KEY", "GEMINI_MODEL", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


def test_run_secret_store_only_key_satisfies_the_provider_guard(monkeypatch, _secret_store_on):
    """A key living ONLY in the Kitaru secret (proxy off) satisfies the guard — the run proceeds.

    Symptom 1 of the Tester-flagged gap: with RUNTIME_SECRET_STORE_CONFIG on and the key only in the
    secret, the old guard tripped ``set GEMINI_API_KEY`` and exited 1 even though the key WAS present.
    The secret-store pre-flight now hydrates Settings up front, so the guard sees the key and the flow
    runs. Asserted via the scripted seam — no real model call.
    """
    from kitaru import create_secret

    create_secret(
        _SECRET_NAME,
        {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "sk-only-in-the-secret"},
        private=True,
    )
    _patch_seam(monkeypatch, "the secret-store answer")

    result = CliRunner().invoke(cli, ["run", "summarize the repo"])

    assert result.exit_code == 0
    assert "the secret-store answer" in result.output
    # The misleading provider-key line must NOT appear — the secret satisfied the guard.
    assert "set GEMINI_API_KEY in your environment" not in result.stderr


def test_run_secret_store_missing_secret_is_a_friendly_line_not_a_traceback(
    monkeypatch, _secret_store_on
):
    """RUNTIME_SECRET_STORE_CONFIG on + NO secret → one friendly line naming the secret, no flow, no traceback.

    Symptom 2: the missing secret used to surface as a deep KitaruRuntimeError traceback from inside
    the flow body. The pre-flight converts it into one friendly stderr line naming the real fix.
    """
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    # The raw secret error did not escape as a traceback.
    assert not isinstance(result.exception, (RuntimeError, ValidationError))
    assert "RUNTIME_SECRET_STORE_CONFIG" in result.stderr
    assert _SECRET_NAME in result.stderr
    assert "kitaru secrets set" in result.stderr


def test_run_hitl_secret_store_missing_secret_is_a_friendly_line_not_a_traceback(
    monkeypatch, _secret_store_on
):
    """``decode run --hitl`` shares the secret-store pre-flight: missing secret → friendly line, no flow."""
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--hitl", "create config.toml"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, (RuntimeError, ValidationError))
    assert "RUNTIME_SECRET_STORE_CONFIG" in result.stderr
    assert _SECRET_NAME in result.stderr
    assert "kitaru secrets set" in result.stderr


def test_run_secret_store_malformed_secret_is_a_friendly_line_not_a_traceback(
    monkeypatch, _secret_store_on
):
    """A stored value that fails a pydantic field (bogus LLM_PROVIDER) → friendly line, exit 1, no traceback.

    The malformed-secret half of symptom 2: a typo'd value used to raise a pydantic ValidationError
    from inside the flow. The pre-flight catches it (LLM_PROVIDER was cleared from the env, so the
    secret's bogus value is authoritative) and emits the same friendly line.
    """
    from kitaru import create_secret

    create_secret(_SECRET_NAME, {"LLM_PROVIDER": "totally-bogus"}, private=True)
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, (RuntimeError, ValidationError))
    assert "RUNTIME_SECRET_STORE_CONFIG" in result.stderr
    assert _SECRET_NAME in result.stderr
