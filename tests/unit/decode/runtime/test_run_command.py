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
from pydantic import SecretStr
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
