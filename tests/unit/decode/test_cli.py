"""Unit tests for the ``decode`` CLI entrypoint.

``CliRunner`` feeds an empty stdin, so the REPL hits EOF (Ctrl-D) immediately and exits
cleanly without ever issuing a model request — no network. The agent is *built* at startup
(construction is offline), which needs a Gemini key, so each test injects a dummy one via
the settings the factory reads.
"""

import pytest
from click.testing import CliRunner
from pydantic import SecretStr

from decode.cli import cli


@pytest.fixture(autouse=True)
def _dummy_gemini_key(mocker):
    """Give the agent factory a non-empty key so startup construction succeeds (offline)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


def test_cli_runs_and_exits_zero():
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0
    assert "decode" in result.output


def test_cli_accepts_resume_flag():
    result = CliRunner().invoke(cli, ["--resume"])
    assert result.exit_code == 0


def test_cli_accepts_named_resume():
    result = CliRunner().invoke(cli, ["--resume", "2026-06-19_abc"])
    assert result.exit_code == 0
