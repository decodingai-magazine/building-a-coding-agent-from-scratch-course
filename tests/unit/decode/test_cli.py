"""Unit tests for the ``decode`` CLI entrypoint.

``CliRunner`` feeds an empty stdin, so the REPL hits EOF (Ctrl-D) immediately and exits
cleanly without ever issuing a model request — no network. The agent is *built* at startup
(construction is offline), which needs a Gemini key, so each test injects a dummy one via
the settings the factory reads.
"""

import pytest
from click.testing import CliRunner
from pydantic import SecretStr
from support.settings_env import hermetic_settings

from decode import cli as cli_mod
from decode.agent import context_window
from decode.cli import cli
from decode.permissions.types import PermissionMode
from decode.tui import app as app_mod


@pytest.fixture(autouse=True)
def _dummy_provider_config(mocker):
    """Seed every LLM Provider's required config so startup construction succeeds (offline).

    Both the CLI's per-provider startup guard (``decode.cli.settings``) and the agent factory
    (``decode.agent.factory.settings``) read the same ``settings`` singleton. Seeding each
    provider's required config here lets the default test runs (provider ``gemini``) — and any test
    that flips ``llm_provider`` to a configured provider — reach ``run_app`` without tripping the
    guard (task 039). Individual tests use ``_select_provider`` to choose a provider and clear
    specific vars to exercise the failure cases. Modal defaults to the *unauthenticated* shape
    (neither proxy token), which is valid.
    """
    # Pin the provider to the gemini default so the suite is hermetic from a developer's local
    # `.env` (e.g. `LLM_PROVIDER=modal`); tests that exercise another provider override this via
    # `_select_provider`.
    mocker.patch.object(cli_mod.settings, "llm_provider", "gemini")
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    mocker.patch.object(cli_mod.settings, "gemini_api_key", SecretStr("test-key"))
    mocker.patch.object(cli_mod.settings, "openrouter_api_key", SecretStr("test-key"))
    mocker.patch.object(cli_mod.settings, "modal_endpoint_url", "https://decode--test.modal.run")
    mocker.patch.object(cli_mod.settings, "modal_endpoint_model", "openai/gpt-oss-120b")
    mocker.patch.object(cli_mod.settings, "modal_proxy_token_id", SecretStr(""))
    mocker.patch.object(cli_mod.settings, "modal_proxy_token_secret", SecretStr(""))


def _select_provider(mocker, provider, **overrides):
    """Select ``provider`` on the shared settings singleton and apply field overrides.

    The guard and the factory share one ``settings`` object, so patching it here is seen by both.
    ``overrides`` are attribute=value pairs (e.g. ``openrouter_api_key=SecretStr("")``).
    """
    mocker.patch.object(cli_mod.settings, "llm_provider", provider)
    for name, value in overrides.items():
        mocker.patch.object(cli_mod.settings, name, value)


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


def test_run_and_remote_are_the_only_subcommands():
    """ADR-0019 §1: ``run`` is the whole local headless surface (``replay`` died with the Durable
    Flow); ``remote`` is its Modal launcher (ADR-0020)."""
    assert set(cli.commands) == {"run", "remote"}


def test_run_subcommand_is_registered_without_breaking_the_bare_repl(mocker):
    """ADR-0008: ``cli`` is now a group exposing ``run``, yet bare ``decode`` still reaches the REPL.

    The group uses ``invoke_without_command=True`` so a bare ``decode`` (no subcommand) launches the
    REPL exactly as before — proving the backward-compat the task requires — while ``decode run``
    is available as a sibling subcommand.
    """
    assert "run" in cli.commands  # the headless subcommand exists

    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())
    result = CliRunner().invoke(cli, ["--agent", "plan", "--mode", "edit"])

    assert result.exit_code == 0
    run_app.assert_awaited_once()  # bare decode (no subcommand) still drives the REPL
    assert run_app.await_args.kwargs.get("agent") == "plan"
    assert run_app.await_args.kwargs.get("mode") == "edit"


def test_importing_the_cli_does_not_import_kitaru():
    """The REPL entrypoint must stay kitaru-free (ADR-0015 §1; ADR-0019 §3).

    Importing ``decode.cli`` in a fresh interpreter must not pull in ``kitaru`` — at
    ``DECODE_ENV=local`` nothing does. A subprocess keeps the check honest regardless of what the
    rest of the suite already imported.

    Tightened for the Recording Seam (ADR-0019 §3): the headless package and the seam module itself
    are imported too, and BOTH kitaru distributions are checked (``kitaru`` and the adapter
    ``kitaru_pydantic_ai``) — the seam's imports live inside its configured branch, so an unconfigured
    process must still come up with neither in ``sys.modules``.
    """
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import decode.cli\n"
        "import decode.runtime\n"
        "import decode.runtime.recording\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'kitaru', 'kitaru_pydantic_ai'})\n"
        "assert not leaked, leaked\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr


def test_importing_the_cli_does_not_import_a_sandbox_backend():
    """The default (``none``) REPL pulls in no sandbox backend module at all (ADR-0011 §4).

    The backends are imported **lazily**, only when ``SANDBOX_MODE`` selects one — so importing
    ``decode.cli`` (the REPL path, ``none`` by default) must never pull docker/modal wiring in. A
    fresh interpreter keeps the check honest regardless of the rest of the suite.
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import decode.cli, sys; "
            "assert 'decode.sandbox.docker_backend' not in sys.modules; "
            "assert 'decode.sandbox.modal_backend' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr


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


# task 097: the Environment-Bucket startup guard — FIRST in the REPL chain (ADR-0015 §5)
# Hydration is process-scoped and surface-agnostic now: at a remote DECODE_ENV the TUI hydrates from
# the bucket exactly like headless does, so the REPL needs the same friendly failure. It precedes the
# provider guard because at a remote env the key is EXPECTED to come from the bucket.


def test_repl_unloadable_bucket_exits_nonzero_with_a_friendly_line(mocker):
    mocker.patch.object(cli_mod.settings, "decode_env", "staging")
    mocker.patch.object(cli_mod, "bucket_load_error", lambda: "decode-staging: secret not found")
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "DECODE_ENV=staging" in result.output
    assert "decode-staging" in result.output  # the derived bucket name
    assert "make sync-secrets ENV=staging" in result.output  # ...and the fix
    assert "Traceback" not in result.output
    run_app.assert_not_awaited()  # the REPL never starts


def test_repl_bucket_guard_precedes_the_provider_key_guard(mocker):
    """The bucket was supposed to supply the key, so ``make sync-secrets`` is the fix, not the key."""
    mocker.patch.object(cli_mod.settings, "decode_env", "prod")
    mocker.patch.object(cli_mod.settings, "gemini_api_key", SecretStr(""))
    mocker.patch.object(cli_mod, "bucket_load_error", lambda: "decode-prod: secret not found")
    mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "make sync-secrets ENV=prod" in result.output
    assert "set GEMINI_API_KEY in your environment" not in result.output


def test_repl_at_local_never_consults_the_bucket(mocker):
    """``DECODE_ENV=local`` (the default): the guard is a pure no-op — the REPL starts as before."""
    calls = {"n": 0}

    def _error():
        calls["n"] += 1
        return "should never be read at local"

    mocker.patch.object(cli_mod, "bucket_load_error", _error)
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert calls["n"] == 0  # never even asked
    run_app.assert_awaited_once()


# task 004 carryover: the no-key startup guard (friendly line, no traceback)


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
    mocker.patch.object(cli_mod.settings, "gemini_api_key", SecretStr("a-real-looking-key"))
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    run_app.assert_awaited_once()


# the --agent startup flag (ADR-0003 §9, task 020)


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
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--agent", "does-not-exist"])

    assert result.exit_code != 0
    run_app.assert_not_awaited()


@pytest.mark.parametrize("name", ["build", "plan", "code-reviewer"])
def test_cli_each_primary_agent_still_starts(mocker, name):
    # The three primaries stay selectable as the main agent (only explore is demoted — ADR-0013 §3).
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--agent", name])

    assert result.exit_code == 0
    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("agent") == name


def test_cli_with_the_explore_subagent_exits_nonzero_listing_primaries(mocker):
    """``--agent explore`` is rejected (a subagent, ADR-0013 §3): friendly stderr line, primaries only.

    Like the unknown-name guard — non-zero exit, no traceback, and only the primary agents (build /
    code-reviewer / plan) are offered — never the subagent-only explore.
    """
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--agent", "explore"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "available agents: build, code-reviewer, plan" in result.output
    # The guard short-circuited before the REPL — no agent built for a subagent name.
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


# the --mode startup flag (ADR-0003 §9, task 022)


def test_cli_passes_no_mode_by_default(mocker):
    # No --mode → the agent's own default mode is used (run_app gets mode=None).
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    CliRunner().invoke(cli, [])

    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("mode") is None


def test_cli_passes_a_named_mode_through(mocker):
    # --mode plan → run_app gets mode="plan".
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--mode", "plan"])

    assert result.exit_code == 0
    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("mode") == "plan"


def test_cli_with_an_unknown_mode_exits_nonzero_with_a_friendly_line(mocker):
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--mode", "nope"])

    assert result.exit_code != 0
    assert "nope" in result.output  # names the bad mode
    assert "Traceback" not in result.output
    # The valid modes are listed so the user can pick one.
    assert "default" in result.output
    # The guard short-circuited before the REPL.
    run_app.assert_not_awaited()


def test_cli_mode_plan_starts_the_real_repl_in_plan_mode(mocker):
    """End-to-end through the real ``run_app``: ``--mode plan`` overrides the agent default mode.

    ``CliRunner`` feeds empty stdin so the REPL hits EOF immediately. The build agent's default is
    ``DEFAULT``; ``--mode plan`` must override it, so the gate ``run_app`` built ends in plan mode.
    We spy on the gate constructor and assert its final mode (proving the override reaches the gate,
    not just the ``run_app`` kwarg).
    """
    gate_spy = mocker.spy(app_mod, "PermissionGate")

    result = CliRunner().invoke(cli, ["--mode", "plan"])

    assert result.exit_code == 0
    gate = gate_spy.spy_return
    assert gate.mode is PermissionMode.PLAN


def test_cli_mode_overrides_the_selected_agents_default_mode(mocker):
    gate_spy = mocker.spy(app_mod, "PermissionGate")

    result = CliRunner().invoke(cli, ["--agent", "plan", "--mode", "default"])

    assert result.exit_code == 0
    gate = gate_spy.spy_return
    assert gate.mode is PermissionMode.DEFAULT


# task 039: the generalized per-provider startup guard (ADR-0005 §6)
#
# ``_provider_config_error()`` returns ONE friendly line (or ``None``) for the selected provider's
# required config. These first tests pin the helper's contract directly (decidable message text /
# which vars it names); the CLI-level tests below prove the exit code + no-traceback behaviour.


def test_provider_config_error_gemini_missing_key_returns_the_unchanged_message(mocker):
    # gemini keeps the verbatim task-004 message for backward-compat.
    _select_provider(mocker, "gemini", gemini_api_key=SecretStr(""))

    assert cli_mod._provider_config_error() == cli_mod._NO_KEY_MESSAGE


def test_provider_config_error_gemini_present_returns_none(mocker):
    _select_provider(mocker, "gemini", gemini_api_key=SecretStr("k"))

    assert cli_mod._provider_config_error() is None


def test_provider_config_error_openrouter_missing_key_names_var_and_provider(mocker):
    _select_provider(mocker, "openrouter", openrouter_api_key=SecretStr(""))

    msg = cli_mod._provider_config_error()

    assert msg is not None
    assert "OPENROUTER_API_KEY" in msg
    assert "openrouter" in msg


def test_provider_config_error_openrouter_present_returns_none(mocker):
    _select_provider(mocker, "openrouter", openrouter_api_key=SecretStr("k"))

    assert cli_mod._provider_config_error() is None


def test_provider_config_error_modal_missing_url_names_only_url(mocker):
    # model present, url absent → name only the absent var.
    _select_provider(mocker, "modal", modal_endpoint_url="", modal_endpoint_model="m")

    msg = cli_mod._provider_config_error()

    assert msg is not None
    assert "MODAL_ENDPOINT_URL" in msg
    assert "MODAL_ENDPOINT_MODEL" not in msg


def test_provider_config_error_modal_missing_model_names_only_model(mocker):
    _select_provider(
        mocker, "modal", modal_endpoint_url="https://x.modal.run", modal_endpoint_model=""
    )

    msg = cli_mod._provider_config_error()

    assert msg is not None
    assert "MODAL_ENDPOINT_MODEL" in msg
    assert "MODAL_ENDPOINT_URL" not in msg


def test_provider_config_error_modal_missing_both_names_both(mocker):
    _select_provider(mocker, "modal", modal_endpoint_url="", modal_endpoint_model="")

    msg = cli_mod._provider_config_error()

    assert msg is not None
    assert "MODAL_ENDPOINT_URL" in msg
    assert "MODAL_ENDPOINT_MODEL" in msg


def test_provider_config_error_modal_both_proxy_tokens_returns_none(mocker):
    # url + model present, both proxy tokens set → authenticated endpoint, valid.
    _select_provider(
        mocker,
        "modal",
        modal_endpoint_url="https://x.modal.run",
        modal_endpoint_model="m",
        modal_proxy_token_id=SecretStr("wk-1"),
        modal_proxy_token_secret=SecretStr("ws-1"),
    )

    assert cli_mod._provider_config_error() is None


def test_provider_config_error_modal_neither_proxy_token_returns_none(mocker):
    # url + model present, neither token set → --unauthenticated endpoint, valid.
    _select_provider(
        mocker,
        "modal",
        modal_endpoint_url="https://x.modal.run",
        modal_endpoint_model="m",
        modal_proxy_token_id=SecretStr(""),
        modal_proxy_token_secret=SecretStr(""),
    )

    assert cli_mod._provider_config_error() is None


def test_provider_config_error_modal_only_token_id_is_both_or_neither(mocker):
    _select_provider(
        mocker,
        "modal",
        modal_endpoint_url="https://x.modal.run",
        modal_endpoint_model="m",
        modal_proxy_token_id=SecretStr("wk-1"),
        modal_proxy_token_secret=SecretStr(""),
    )

    msg = cli_mod._provider_config_error()

    assert msg is not None
    assert "both-or-neither" in msg
    assert "MODAL_PROXY_TOKEN_ID" in msg
    assert "MODAL_PROXY_TOKEN_SECRET" in msg


def test_provider_config_error_modal_only_token_secret_is_both_or_neither(mocker):
    _select_provider(
        mocker,
        "modal",
        modal_endpoint_url="https://x.modal.run",
        modal_endpoint_model="m",
        modal_proxy_token_id=SecretStr(""),
        modal_proxy_token_secret=SecretStr("ws-1"),
    )

    msg = cli_mod._provider_config_error()

    assert msg is not None
    assert "both-or-neither" in msg
    assert "MODAL_PROXY_TOKEN_ID" in msg
    assert "MODAL_PROXY_TOKEN_SECRET" in msg


# task 039: CLI behaviour — friendly line + non-zero exit, no traceback


def test_cli_openrouter_with_no_key_exits_nonzero_with_a_friendly_line(mocker):
    _select_provider(mocker, "openrouter", openrouter_api_key=SecretStr(""))
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "OPENROUTER_API_KEY" in result.output  # names the missing var
    assert "openrouter" in result.output  # names the provider
    assert ".env.example" in result.output
    assert "Traceback" not in result.output
    assert "Decode:" in result.output
    run_app.assert_not_awaited()


def test_cli_modal_missing_url_exits_nonzero_naming_the_missing_var(mocker):
    _select_provider(mocker, "modal", modal_endpoint_url="", modal_endpoint_model="m")
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "MODAL_ENDPOINT_URL" in result.output
    assert "Traceback" not in result.output
    assert "Decode:" in result.output
    run_app.assert_not_awaited()


def test_cli_modal_both_proxy_tokens_passes_the_guard(mocker):
    _select_provider(
        mocker,
        "modal",
        modal_endpoint_url="https://x.modal.run",
        modal_endpoint_model="m",
        modal_proxy_token_id=SecretStr("wk-1"),
        modal_proxy_token_secret=SecretStr("ws-1"),
    )
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    run_app.assert_awaited_once()


def test_cli_modal_unauthenticated_passes_the_guard(mocker):
    _select_provider(
        mocker,
        "modal",
        modal_endpoint_url="https://x.modal.run",
        modal_endpoint_model="m",
        modal_proxy_token_id=SecretStr(""),
        modal_proxy_token_secret=SecretStr(""),
    )
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    run_app.assert_awaited_once()


def test_cli_modal_only_token_id_exits_nonzero_both_or_neither(mocker):
    _select_provider(
        mocker,
        "modal",
        modal_endpoint_url="https://x.modal.run",
        modal_endpoint_model="m",
        modal_proxy_token_id=SecretStr("wk-1"),
        modal_proxy_token_secret=SecretStr(""),
    )
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "both-or-neither" in result.output
    assert "Traceback" not in result.output
    run_app.assert_not_awaited()


def test_cli_modal_only_token_secret_exits_nonzero_both_or_neither(mocker):
    _select_provider(
        mocker,
        "modal",
        modal_endpoint_url="https://x.modal.run",
        modal_endpoint_model="m",
        modal_proxy_token_id=SecretStr(""),
        modal_proxy_token_secret=SecretStr("ws-1"),
    )
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "both-or-neither" in result.output
    assert "Traceback" not in result.output
    run_app.assert_not_awaited()


@pytest.mark.parametrize(
    ("provider", "overrides"),
    [
        ("gemini", {"gemini_api_key": SecretStr("k")}),
        ("openrouter", {"openrouter_api_key": SecretStr("k")}),
        (
            "modal",
            {
                "modal_endpoint_url": "https://x.modal.run",
                "modal_endpoint_model": "m",
                "modal_proxy_token_id": SecretStr(""),
                "modal_proxy_token_secret": SecretStr(""),
            },
        ),
        (
            "modal",
            {
                "modal_endpoint_url": "https://x.modal.run",
                "modal_endpoint_model": "m",
                "modal_proxy_token_id": SecretStr("wk-1"),
                "modal_proxy_token_secret": SecretStr("ws-1"),
            },
        ),
    ],
)
def test_cli_with_each_provider_configured_starts_the_real_repl(mocker, provider, overrides):
    """With the selected provider's config present the guard passes and the real REPL starts.

    Reaches the real ``build_agent`` + ``run_app`` (empty stdin → EOF, exits 0), proving model
    construction for every provider is offline and the guard does not trip. No network: an empty
    conversation short-circuits the on-exit memory write-back.
    """
    _select_provider(mocker, provider, **overrides)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert "Decode" in result.output


@pytest.mark.parametrize(
    ("provider", "overrides"),
    [
        ("gemini", {"gemini_api_key": SecretStr("")}),
        ("openrouter", {"openrouter_api_key": SecretStr("")}),
        ("modal", {"modal_endpoint_url": "", "modal_endpoint_model": ""}),
    ],
)
def test_cli_provider_guard_precedes_agent_and_mode_validation(mocker, provider, overrides):
    """For every provider, a missing-config exit fires before ``--agent`` / ``--mode`` validation.

    Invoking with an invalid ``--agent`` and ``--mode`` while the provider is misconfigured must
    still report the provider guard's message (not the unknown-agent / unknown-mode message) — and
    never leak a raw ``pydantic_ai.UserError`` traceback, since no agent is built.
    """
    _select_provider(mocker, provider, **overrides)
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--agent", "nope", "--mode", "bogus"])

    assert result.exit_code != 0
    # The provider guard short-circuited: neither downstream guard's bad value appears.
    assert "nope" not in result.output
    assert "bogus" not in result.output
    assert "Traceback" not in result.output
    assert "UserError" not in result.output
    run_app.assert_not_awaited()


# task 071: the sandbox backend-availability guard — the helper contract (ADR-0011 §1)
#
# ``_sandbox_config_error()`` returns ONE friendly line (or ``None``) for the selected Sandbox Mode
# when its backend is unavailable. Presence/reachability only, like the provider-key guards — a
# present-but-wrong value is NOT rejected here. The probes are PATCHED: no real docker daemon is
# contacted and no modal credentials/import/network are touched.


def test_sandbox_config_error_none_returns_none_and_runs_no_probe(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "none")
    docker_probe = mocker.patch("decode.cli._docker_daemon_reachable")
    modal_probe = mocker.patch("decode.cli._modal_credentials_present")

    assert cli_mod._sandbox_config_error() is None
    docker_probe.assert_not_called()
    modal_probe.assert_not_called()


def test_sandbox_config_error_docker_unreachable_returns_the_docker_line(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "docker")
    mocker.patch("decode.cli._docker_daemon_reachable", return_value=False)

    msg = cli_mod._sandbox_config_error()

    assert msg is not None
    assert "SANDBOX_MODE=docker" in msg
    assert "Docker daemon" in msg
    assert ".env.example" in msg


def test_sandbox_config_error_docker_reachable_returns_none(mocker):
    """Presence, not correctness: a reachable-but-fake docker probe passes the guard (AC)."""
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "docker")
    mocker.patch("decode.cli._docker_daemon_reachable", return_value=True)

    assert cli_mod._sandbox_config_error() is None


def test_sandbox_config_error_modal_missing_creds_returns_the_modal_line(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "modal")
    mocker.patch("decode.cli._modal_credentials_present", return_value=False)

    msg = cli_mod._sandbox_config_error()

    assert msg is not None
    assert "SANDBOX_MODE=modal" in msg
    assert "modal token set" in msg
    assert ".env.example" in msg


def test_sandbox_config_error_modal_present_creds_returns_none(mocker):
    """Presence, not correctness: present-but-fake modal creds pass the guard (AC)."""
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "modal")
    mocker.patch("decode.cli._modal_credentials_present", return_value=True)

    assert cli_mod._sandbox_config_error() is None


# task 071: the docker daemon-reachability probe (missing binary / non-zero / timeout)


def test_docker_daemon_reachable_true_on_zero_exit(mocker):
    mocker.patch("decode.cli.subprocess.run", return_value=mocker.Mock(returncode=0))

    assert cli_mod._docker_daemon_reachable() is True


def test_docker_daemon_reachable_false_on_nonzero_exit(mocker):
    mocker.patch("decode.cli.subprocess.run", return_value=mocker.Mock(returncode=1))

    assert cli_mod._docker_daemon_reachable() is False


def test_docker_daemon_reachable_false_when_binary_missing(mocker):
    mocker.patch("decode.cli.subprocess.run", side_effect=FileNotFoundError)

    assert cli_mod._docker_daemon_reachable() is False


def test_docker_daemon_reachable_false_on_timeout(mocker):
    import subprocess

    mocker.patch(
        "decode.cli.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="docker info", timeout=1.0),
    )

    assert cli_mod._docker_daemon_reachable() is False


# task 071: the modal credential-presence probe (no network, no modal import)


def test_modal_credentials_present_true_with_env_token_pair(monkeypatch, mocker, tmp_path):
    """Both MODAL_TOKEN_ID + MODAL_TOKEN_SECRET in env → present (the modal CLI's own contract)."""
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-1")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-1")
    mocker.patch("decode.cli.Path.home", return_value=tmp_path)  # ~/.modal.toml absent regardless

    assert cli_mod._modal_credentials_present() is True


def test_modal_credentials_present_true_with_modal_toml(monkeypatch, mocker, tmp_path):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    (tmp_path / ".modal.toml").write_text("[default]\n")
    mocker.patch("decode.cli.Path.home", return_value=tmp_path)

    assert cli_mod._modal_credentials_present() is True


def test_modal_credentials_present_inside_a_modal_container(monkeypatch, mocker, tmp_path):
    """task 142 / ADR-0020 §3: a Modal container has neither the token pair nor ~/.modal.toml — it
    carries an ambient identity, marked by modal's own MODAL_IS_REMOTE. Without this branch a
    nested-sandbox run on the Modal Headless App is rejected by its own guard."""
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.setenv("MODAL_IS_REMOTE", "1")
    mocker.patch("decode.cli.Path.home", return_value=tmp_path)

    assert cli_mod._modal_credentials_present() is True


def test_modal_credentials_absent_with_no_env_and_no_toml(monkeypatch, mocker, tmp_path):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("MODAL_IS_REMOTE", raising=False)
    mocker.patch("decode.cli.Path.home", return_value=tmp_path)  # empty tmp dir → no ~/.modal.toml

    assert cli_mod._modal_credentials_present() is False


def test_modal_credentials_absent_with_only_one_env_token(monkeypatch, mocker, tmp_path):
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-1")
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    mocker.patch("decode.cli.Path.home", return_value=tmp_path)

    assert cli_mod._modal_credentials_present() is False


# task 071: the sandbox guard in the REPL startup chain (friendly line, no traceback)


def test_cli_sandbox_docker_unreachable_exits_nonzero_with_a_friendly_line(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "docker")
    mocker.patch("decode.cli._docker_daemon_reachable", return_value=False)
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "SANDBOX_MODE=docker" in result.output  # names the mode + backend
    assert "Docker daemon" in result.output
    assert ".env.example" in result.output
    assert "Traceback" not in result.output
    assert "Decode:" in result.output
    run_app.assert_not_awaited()  # short-circuited before the REPL


def test_cli_sandbox_modal_missing_creds_exits_nonzero_with_a_friendly_line(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "modal")
    mocker.patch("decode.cli._modal_credentials_present", return_value=False)
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "SANDBOX_MODE=modal" in result.output
    assert "modal token set" in result.output  # names the real fix
    assert ".env.example" in result.output
    assert "Traceback" not in result.output
    assert "Decode:" in result.output
    run_app.assert_not_awaited()


def test_cli_sandbox_none_default_starts_the_repl_and_runs_no_probe(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "none")
    docker_probe = mocker.patch("decode.cli._docker_daemon_reachable")
    modal_probe = mocker.patch("decode.cli._modal_credentials_present")
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    run_app.assert_awaited_once()
    docker_probe.assert_not_called()
    modal_probe.assert_not_called()


def test_cli_sandbox_docker_reachable_starts_the_repl(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "docker")
    mocker.patch("decode.cli._docker_daemon_reachable", return_value=True)
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    run_app.assert_awaited_once()


def test_cli_provider_guard_precedes_the_sandbox_guard(mocker):
    mocker.patch.object(cli_mod.settings, "gemini_api_key", SecretStr(""))
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "docker")
    # If the sandbox guard ran first this would flip the message; assert it never runs.
    docker_probe = mocker.patch("decode.cli._docker_daemon_reachable", return_value=False)
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "GEMINI_API_KEY" in result.output  # the provider message, not the sandbox one
    assert "SANDBOX_MODE=docker" not in result.output
    docker_probe.assert_not_called()
    run_app.assert_not_awaited()


# task 082: the Workspace repo resolution + the none-mode guard (ADR-0012 §3)
#
# ``_resolve_sandbox_repo`` (--repo > SANDBOX_REPO > None) and ``_sandbox_repo_config_error`` (a repo
# requested while SANDBOX_MODE=none is a config error) are the decidable helpers; the CLI-level tests
# below prove the exit code + no-traceback behaviour and the flag threading into ``run_app``.


def test_resolve_sandbox_repo_flag_wins_over_the_setting(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_repo", "https://from.env/repo.git")

    assert cli_mod._resolve_sandbox_repo("/flag/repo") == "/flag/repo"


def test_resolve_sandbox_repo_falls_back_to_the_setting(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_repo", "https://from.env/repo.git")

    assert cli_mod._resolve_sandbox_repo(None) == "https://from.env/repo.git"


def test_resolve_sandbox_repo_none_when_neither_is_set(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_repo", "")  # the default (unset)

    assert cli_mod._resolve_sandbox_repo(None) is None


def test_sandbox_repo_config_error_repo_in_none_mode_returns_the_message(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "none")

    msg = cli_mod._sandbox_repo_config_error("/some/repo")

    assert msg is not None
    assert "--repo/SANDBOX_REPO" in msg
    assert "SANDBOX_MODE=docker" in msg
    assert ".env.example" in msg


def test_sandbox_repo_config_error_repo_in_a_sandbox_mode_returns_none(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "docker")

    assert cli_mod._sandbox_repo_config_error("/some/repo") is None


def test_sandbox_repo_config_error_no_repo_returns_none(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "none")

    assert cli_mod._sandbox_repo_config_error(None) is None


def test_cli_repo_and_local_flags_are_documented():
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "--repo" in result.output
    assert "--local" in result.output


def test_cli_repo_in_none_mode_exits_nonzero_with_a_friendly_line(mocker):
    # sandbox_mode is pinned none by the autouse fixture — a repo is contradictory there.
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--repo", "/some/repo"])

    assert result.exit_code != 0
    assert "--repo/SANDBOX_REPO" in result.output
    assert "SANDBOX_MODE=docker" in result.output  # names the fix
    assert "Traceback" not in result.output
    assert "Decode:" in result.output
    run_app.assert_not_awaited()  # short-circuited before the REPL


def test_cli_sandbox_repo_env_in_none_mode_exits_nonzero(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_repo", "https://from.env/repo.git")
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code != 0
    assert "--repo/SANDBOX_REPO" in result.output
    run_app.assert_not_awaited()


def test_cli_repo_threaded_to_run_app_in_a_sandbox_mode(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "docker")
    mocker.patch("decode.cli._docker_daemon_reachable", return_value=True)
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, ["--repo", "/some/repo", "--local"])

    assert result.exit_code == 0
    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("repo") == "/some/repo"
    assert run_app.await_args.kwargs.get("local") is True


def test_cli_no_repo_passes_none_to_run_app(mocker):
    mocker.patch.object(cli_mod.settings, "sandbox_mode", "docker")
    mocker.patch("decode.cli._docker_daemon_reachable", return_value=True)
    run_app = mocker.patch("decode.cli.run_app", new=mocker.AsyncMock())

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    run_app.assert_awaited_once()
    assert run_app.await_args.kwargs.get("repo") is None
    assert run_app.await_args.kwargs.get("local") is False


# --version flag


def test_cli_version_option_exists():
    """`--version` is registered on the CLI group and exits cleanly."""
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "decode" in result.output
    assert "0.1.0" in result.output


def test_version_option_does_not_trigger_startups():
    """--version exits before any startup guards or env checks — pure metadata."""
    # Even with no provider configured, --version must succeed (no real provider key needed).
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "GEMINI_API_KEY" not in result.output
    assert "Traceback" not in result.output


# Context-window notice + the no-inference paths (task 123)


def test_help_performs_no_provider_probe(mocker):
    """``decode --help`` must never cold-start a GPU to print usage text."""
    probe = mocker.patch.object(context_window, "_probe")

    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    probe.assert_not_called()


def test_version_performs_no_provider_probe(mocker):
    """Same for ``--version`` — pure metadata, no inference, no network."""
    probe = mocker.patch.object(context_window, "_probe")

    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    probe.assert_not_called()


def test_run_help_performs_no_provider_probe(mocker):
    """The ``run`` subcommand's own help is a no-inference path too."""
    probe = mocker.patch.object(context_window, "_probe")

    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    probe.assert_not_called()


@pytest.fixture
def notice_settings(monkeypatch, mocker):
    """Point BOTH the resolver and the cli at a purpose-built Settings for the notice tests.

    Not the shared singleton: by the time the whole suite has run, another test has assigned to it
    directly and permanently marked ``compaction_context_window_tokens`` as explicitly set, which
    is precisely the signal the notice branches on (see ``support.settings_env``).
    """

    def _use(**overrides):
        config = hermetic_settings(monkeypatch, **overrides)
        mocker.patch.object(context_window, "settings", config)
        mocker.patch.object(cli_mod, "settings", config)
        return config

    return _use


def test_context_window_notice_warns_only_when_nothing_could_resolve_it(notice_settings, mocker):
    """An unresolvable model gets the one assumed-window line, naming the fallback."""
    mocker.patch.object(context_window, "_probe", return_value=None)
    notice_settings(llm_provider="gemini", gemini_model="acme/unlisted-model-v1")

    notice = cli_mod._context_window_notice()

    assert notice is not None
    assert "acme/unlisted-model-v1" in notice
    assert "200,000" in notice


def test_context_window_notice_is_silent_after_a_successful_probe(notice_settings, mocker):
    """A probed window is KNOWN — claiming "assuming" would be a lie the operator learns to skip."""
    mocker.patch.object(context_window, "_probe", return_value=262_144)
    notice_settings(llm_provider="gemini", gemini_model="acme/unlisted-model-v1")

    assert cli_mod._context_window_notice() is None


def test_context_window_notice_is_silent_for_a_table_model(notice_settings, mocker):
    mocker.patch.object(context_window, "_probe", return_value=None)
    notice_settings(llm_provider="gemini", gemini_model="gemini-3.5-flash")

    assert cli_mod._context_window_notice() is None


def test_context_window_notice_is_silent_when_the_operator_set_the_window(notice_settings, mocker):
    """An explicit setting is owned by the operator — never warned about, and never probed."""
    probe = mocker.patch.object(context_window, "_probe")
    notice_settings(
        llm_provider="gemini",
        gemini_model="acme/unlisted-model-v1",
        compaction_context_window_tokens=8192,
    )

    assert cli_mod._context_window_notice() is None
    probe.assert_not_called()


def test_context_window_notice_describes_the_model_override(notice_settings, mocker):
    """``decode run --model <unknown>`` warns about the OVERRIDE, not the configured model."""
    mocker.patch.object(context_window, "_probe", return_value=None)
    notice_settings(llm_provider="gemini", gemini_model="gemini-3.5-flash")

    notice = cli_mod._context_window_notice("acme/unlisted-model-v1")

    assert notice is not None
    assert "acme/unlisted-model-v1" in notice
