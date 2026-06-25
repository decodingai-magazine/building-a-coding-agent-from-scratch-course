"""Unit tests for the ``decode`` CLI entrypoint.

``CliRunner`` feeds an empty stdin, so the REPL hits EOF (Ctrl-D) immediately and exits
cleanly without ever issuing a model request — no network. The agent is *built* at startup
(construction is offline), which needs a Gemini key, so each test injects a dummy one via
the settings the factory reads.
"""

import pytest
from click.testing import CliRunner
from pydantic import SecretStr

from decode import cli as cli_mod
from decode.cli import cli
from decode.tui import app as app_mod


@pytest.fixture(autouse=True)
def _dummy_gemini_key(mocker):
    """Give the agent factory a non-empty key so startup construction succeeds (offline).

    Both the CLI's no-key startup guard (``decode.cli.settings``) and the agent factory
    (``decode.agent.factory.settings``) read the same singleton, so a non-empty key here lets
    the default test runs reach ``run_app`` without tripping the guard.
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    mocker.patch.object(cli_mod.settings, "gemini_api_key", SecretStr("test-key"))


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
    # Fix 4: user-facing prose says "Decode" (the banner/goodbye); the command stays lowercase.
    assert "Decode" in result.output


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


# --- task 004 carryover: the no-key startup guard (friendly line, no traceback) -------------


def test_cli_with_no_gemini_key_exits_nonzero_with_a_friendly_line(mocker):
    """No ``GEMINI_API_KEY`` → one friendly line on stderr, non-zero exit, NO traceback.

    Without the guard, ``build_agent()`` raises a raw ``pydantic_ai.UserError`` from
    ``GoogleProvider`` (mentioning ``GOOGLE_API_KEY`` — the wrong var for this project), which
    surfaces as an ugly traceback. The guard checks ``settings.gemini_api_key`` *before* building
    the agent and exits cleanly instead.
    """
    mocker.patch.object(cli_mod.settings, "gemini_api_key", SecretStr(""))
    # run_app must never be reached — the guard fires first, so a stub proves it is not called.
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    # A friendly one-liner naming the fix, not a traceback.
    assert "GEMINI_API_KEY" in result.output
    assert ".env.example" in result.output
    assert "Traceback" not in result.output
    # Fix 4: user-facing prose capitalizes "Decode".
    assert "Decode:" in result.output
    # The guard short-circuited before the REPL / agent build.
    run_app.assert_not_awaited()


def test_cli_with_a_present_gemini_key_does_not_trip_the_guard(mocker):
    """A present key does NOT trigger the guard: the CLI proceeds to ``run_app`` normally."""
    mocker.patch.object(cli_mod.settings, "gemini_api_key", SecretStr("a-real-looking-key"))
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    run_app.assert_awaited_once()


# --- the --agent startup flag (ADR-0003 §9, task 020) ---------------------------------------


def test_cli_defaults_to_the_build_agent(mocker):
    # No --agent → the build persona (run_app gets agent="build").
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    CliRunner().invoke(cli, [])

    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("agent") == "build"


def test_cli_passes_a_named_agent_through(mocker):
    # --agent plan → run_app gets agent="plan".
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--agent", "plan"])

    assert result.exit_code == 0
    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("agent") == "plan"


def test_cli_with_an_unknown_agent_exits_nonzero_with_a_friendly_line(mocker):
    """``--agent nope`` → one friendly stderr line + non-zero exit, NO traceback (like no-key)."""
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--agent", "nope"])

    assert result.exit_code != 0
    assert "nope" in result.output  # names the bad agent
    assert "Traceback" not in result.output
    # The available agents are listed so the user can pick a valid one.
    assert "build" in result.output
    # The guard short-circuited before the REPL.
    run_app.assert_not_awaited()


def test_cli_validates_the_agent_before_building_the_agent(mocker):
    """The unknown-agent guard fires before run_app, so no agent is ever built for a bad name."""
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--agent", "does-not-exist"])

    assert result.exit_code != 0
    run_app.assert_not_awaited()


def test_cli_agent_plan_starts_the_real_repl_in_plan_mode(mocker):
    """End-to-end through the real ``run_app``: ``--agent plan`` selects plan before the loop.

    ``CliRunner`` feeds empty stdin so the REPL hits EOF immediately (no model request). We spy
    on the real ``select_agent`` the ``run_app`` startup calls and assert it was applied with the
    plan persona — proving ``--agent plan`` reaches the gate/deps wiring (ADR-0003 §7,9), not just
    the ``run_app`` kwarg.
    """
    spy = mocker.spy(app_mod, "select_agent")

    result = CliRunner().invoke(cli, ["--agent", "plan"])

    assert result.exit_code == 0
    spy.assert_called_once()
    assert spy.call_args.args[0] == "plan"
    # The gate it was handed ended up in plan mode (selecting plan resets the mode).
    selected = spy.spy_return
    assert selected.name == "plan"
    assert selected.mode.value == "plan"
