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
from decode.tui import app as app_mod


@pytest.fixture(autouse=True)
def _dummy_gemini_key(mocker):
    """Give the agent factory a non-empty key so startup construction succeeds (offline)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


@pytest.fixture(autouse=True)
def _isolate_sessions_dir(tmp_path, monkeypatch):
    """Redirect the JSONL session log under a per-test tmp dir (ADR-0002 §9, task 014).

    The tests that drive the real ``cli`` reach the real ``run_app``, which opens a session log
    under ``settings.sessions_dir`` (``tui/app.py``). Without this redirect every run would write
    header-only ``.jsonl`` files into the repo's real ``.decode/sessions``. Mirrors the autouse
    fixture in ``tests/unit/decode/tui/test_app_e2e.py``; patches the ``settings`` object the
    ``run_app`` code path actually reads (``decode.tui.app.settings``).
    """
    monkeypatch.setattr(app_mod.settings, "sessions_dir", tmp_path / "sessions", raising=False)


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


def test_cli_passes_no_resume_by_default(mocker):
    # Without --resume the CLI starts a fresh session (run_app gets resume=None).
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    CliRunner().invoke(cli, [])

    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("resume") is None


def test_cli_passes_latest_resume_for_the_bare_flag(mocker):
    # `--resume` with no value resumes the latest session (run_app gets resume="latest").
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    CliRunner().invoke(cli, ["--resume"])

    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("resume") == "latest"


def test_cli_passes_named_resume_through(mocker):
    # `--resume <id>` resumes that named session (run_app gets resume=<id>).
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    CliRunner().invoke(cli, ["--resume", "2026-06-19_abc"])

    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("resume") == "2026-06-19_abc"
