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
