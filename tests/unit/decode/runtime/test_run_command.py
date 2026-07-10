"""``decode run "<task>"`` — the headless subcommand end to end (ADR-0008).

Drives the real Click ``run`` subcommand through ``CliRunner``, with the model boundary swapped via
the ``_build_runtime_agent`` seam and the Kitaru store isolated (the autouse fixture in this
package's ``conftest``). Covers the happy path (prints the agent's text) and both guards
(``RUNTIME_ENABLED=false`` and the provider-config guard) — each a friendly stderr line + non-zero
exit that never builds a flow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import SecretStr, ValidationError
from pydantic_ai.messages import ModelResponse, TextPart
from support.runtime_agents import make_scripted_agent

import decode.cli as cli_mod
import decode.runtime.flow as flow_mod
from decode.cli import cli

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
    """Point the runtime seam at a scripted agent returning ``text``; return its leg counter.

    Uses ``checkpoint_strategy="calls"`` (also the settings default) so the command exercises the
    multi-terminal-checkpoint path — the real ``decode run`` reads its output from the
    ``_capture_runtime_output`` artifact (``.wait()`` cannot extract under ``"calls"``); the read-back is
    identical under the ``"turn"`` opt-out.
    """
    from kitaru.adapters.pydantic_ai import KitaruAgent

    agent, counter = make_scripted_agent([ModelResponse(parts=[TextPart(content=text)])])
    durable = KitaruAgent(agent, name="decode-runtime", checkpoint_strategy="calls")
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)
    return counter


def _recording_seam(monkeypatch, text):
    """Point the bypass seam at a scripted agent, recording the ``model`` the flow forwards to it.

    Returns a mutable ``captured`` dict whose ``"model"`` key holds the value the ``@flow`` passed to
    :func:`_build_runtime_agent` — i.e. the Model Override the ``--model`` flag threads through as a
    durable flow input (ADR-0010 §2,4). The scripted agent uses ``"calls"`` like :func:`_patch_seam`,
    so the command drives the real artifact-read output path.
    """
    from kitaru.adapters.pydantic_ai import KitaruAgent

    agent, _counter = make_scripted_agent([ModelResponse(parts=[TextPart(content=text)])])
    durable = KitaruAgent(agent, name="decode-runtime", checkpoint_strategy="calls")
    captured = {"model": "SENTINEL"}

    def _seam(model=None):
        captured["model"] = model
        return durable

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _seam)
    return captured


# task 069: `decode run --model X` + surface the exec_id + paste-ready replay hint (ADR-0010 §4)


def test_run_help_documents_the_model_flag():
    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "--model" in result.output
    assert "gemini-2.5-pro" in result.output  # the help's example model id
    assert "LLM_PROVIDER" in result.output  # notes it does NOT change the provider


def test_run_model_flag_threads_the_override_to_the_seam_and_prints_output(
    monkeypatch, _provider_ok
):
    captured = _recording_seam(monkeypatch, "the overridden answer")

    result = CliRunner().invoke(cli, ["run", "--model", "gemini-2.5-pro", "refactor the parser"])

    assert result.exit_code == 0
    assert captured["model"] == "gemini-2.5-pro"  # the flag reached the flow's model seam
    assert "the overridden answer" in result.stdout


def test_run_without_model_passes_none_to_the_seam(monkeypatch, _provider_ok):
    captured = _recording_seam(monkeypatch, "the default answer")

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code == 0
    assert captured["model"] is None  # no override → the settings default id is used downstream
    assert "the default answer" in result.stdout


def test_run_exec_id_and_replay_hint_go_to_stderr_not_stdout(monkeypatch, _provider_ok):
    """The exec_id + ``decode replay`` hint land on stderr; stdout stays the clean answer (AC3).

    The pipe-clean split: a piped ``decode run`` must yield exactly the agent's answer on stdout, so
    the discoverability scaffolding (exec_id anchor + paste-ready replay hint) is echoed to stderr —
    and the hint carries the run's own model id when ``--model`` was given.
    """
    _recording_seam(monkeypatch, "the piped answer")

    result = CliRunner().invoke(cli, ["run", "--model", "gemini-2.5-pro", "summarize the module"])

    assert result.exit_code == 0
    # stdout is pipe-clean: the answer is there, none of the replay scaffolding is.
    assert "the piped answer" in result.stdout
    assert "exec_id:" not in result.stdout
    assert "decode replay" not in result.stdout
    # stderr carries the exec_id anchor + a paste-ready decode replay hint using the run's model id.
    assert "exec_id:" in result.stderr
    assert "decode replay" in result.stderr
    assert "--model gemini-2.5-pro" in result.stderr


def test_run_replay_hint_uses_a_placeholder_when_no_model_given(monkeypatch, _provider_ok):
    _recording_seam(monkeypatch, "answer")

    result = CliRunner().invoke(cli, ["run", "do the thing"])

    assert result.exit_code == 0
    assert "decode replay" in result.stderr
    assert "--model <model-id>" in result.stderr


def test_run_model_does_not_bypass_the_disabled_runtime_guard(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", False)

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("the flow must not be built when the runtime is disabled")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _tripwire)

    result = CliRunner().invoke(cli, ["run", "--model", "gemini-2.5-pro", "do it"])

    assert result.exit_code != 0
    assert "headless runtime is disabled" in result.stderr


def test_run_model_does_not_bypass_the_provider_key_guard(monkeypatch):
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("the flow must not be built when the provider config is missing")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _tripwire)

    result = CliRunner().invoke(cli, ["run", "--model", "gemini-2.5-pro", "do it"])

    assert result.exit_code != 0
    assert "GEMINI_API_KEY" in result.stderr


def test_run_command_prints_the_agents_output(monkeypatch, _provider_ok):
    _patch_seam(monkeypatch, "the headless answer")

    result = CliRunner().invoke(cli, ["run", "summarize the cli module"])

    assert result.exit_code == 0
    assert "the headless answer" in result.output


# task 083: auto-ship the Workspace after a headless `decode run --repo` completes (ADR-0012 §8)


def test_run_invokes_the_auto_ship_with_the_run_exec_id(monkeypatch, _provider_ok):
    _patch_seam(monkeypatch, "done")
    calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        cli_mod, "_auto_ship_headless", lambda repo, exec_id: calls.append((repo, exec_id))
    )

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code == 0
    # Wired once, with the resolved repo (None in none mode) and a real exec_id string.
    assert len(calls) == 1
    repo, exec_id = calls[0]
    assert repo is None  # none mode → an empty Workspace, nothing to ship
    assert isinstance(exec_id, str) and exec_id


def test_auto_ship_headless_no_repo_is_a_silent_noop(mocker, capsys):
    ship = mocker.patch("decode.sandbox.handback.ship_workspace")

    cli_mod._auto_ship_headless(None, "exec-abc")

    ship.assert_not_called()  # not even imported/called
    assert capsys.readouterr().err == ""


def test_auto_ship_headless_prints_the_outcome_on_stderr(mocker, capsys):
    from decode.sandbox.handback import ShipResult

    ship = mocker.patch(
        "decode.sandbox.handback.ship_workspace",
        return_value=ShipResult(branch="decode/exec-abc", pushed=True, message="handed it back."),
    )

    cli_mod._auto_ship_headless("/src", "exec-abc")

    ship.assert_called_once_with(Path.cwd(), repo="/src", session_id="exec-abc")
    captured = capsys.readouterr()
    assert captured.out == ""  # pipe-clean stdout
    assert "handed it back." in captured.err  # the outcome lands on stderr


def test_auto_ship_headless_skip_prints_nothing(mocker, capsys):
    from decode.sandbox.handback import ShipResult

    mocker.patch(
        "decode.sandbox.handback.ship_workspace",
        return_value=ShipResult(branch=None, pushed=False, message="nothing to hand back."),
    )

    cli_mod._auto_ship_headless("/src", "exec-abc")

    assert capsys.readouterr().err == ""


def test_auto_ship_headless_swallows_errors(mocker, capsys):
    mocker.patch("decode.sandbox.handback.ship_workspace", side_effect=RuntimeError("boom"))

    cli_mod._auto_ship_headless("/src", "exec-abc")  # must not raise

    assert "boom" not in capsys.readouterr().err  # the raw error is logged, not surfaced


def test_run_command_disabled_runtime_guard_does_not_build_a_flow(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", False)
    built = {"seam": False}

    def _tripwire(*_args, **_kwargs):
        built["seam"] = True
        raise AssertionError("the flow must not be built when the runtime is disabled")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _tripwire)

    result = CliRunner().invoke(cli, ["run", "do it"])

    assert result.exit_code != 0
    assert "headless runtime is disabled" in result.stderr
    assert "RUNTIME_ENABLED=true" in result.stderr
    assert built["seam"] is False


def test_run_command_provider_guard_fires_without_a_key(monkeypatch):
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("the flow must not be built when the provider config is missing")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _tripwire)

    result = CliRunner().invoke(cli, ["run", "do it"])

    assert result.exit_code != 0
    assert "GEMINI_API_KEY" in result.stderr


# Credentials proxy: missing/incomplete Kitaru secret is a friendly line, not a traceback
# (task 061 QA blocker — User Story #3 "opt-in and safe by default"). The proxy-aware pre-flight
# resolves the Kitaru secret BEFORE building the durable flow, so a missing/incomplete secret exits
# with one friendly stderr line naming ``kitaru secrets set`` — never the ~30-frame KitaruRuntimeError
# traceback the unguarded ``run_agent_task.run(...).wait()`` used to dump.

# The Kitaru secret name comes from the ``runtime_secret_name`` fixture (a unique per-test
# ``decode-test-creds-<uuid>`` wired into ``settings.runtime_secret_name`` + ``RUNTIME_SECRET_NAME``) —
# never the hardcoded production default — so a hypothetical store-isolation fall-through can never
# collide with or leave a real-store ``decode-llm-creds``, and the missing-secret guards assert a name
# that is genuinely absent in any store (task 065).


@pytest.fixture
def _proxy_on(monkeypatch, runtime_secret_name):
    """Enable the credentials proxy for gemini with the runtime on (the secret is created per test).

    ``runtime_secret_name`` (unique per test) is wired by the same-named fixture, not here.
    """
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_credentials_proxy_enabled", True)


def _no_flow_tripwires(monkeypatch):
    """Make both runtime seams blow up if reached — the guard must exit before any flow is built."""

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("no flow may be built when the Kitaru secret is missing/incomplete")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _tripwire)
    monkeypatch.setattr(flow_mod, "_build_hitl_runtime_agent", _tripwire)


def test_run_command_proxy_missing_secret_is_a_friendly_line_not_a_traceback(
    monkeypatch, _proxy_on, runtime_secret_name
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
    assert runtime_secret_name in result.stderr
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


def test_run_command_proxy_secret_missing_provider_key_is_friendly(
    monkeypatch, _proxy_on, runtime_secret_name
):
    from kitaru import create_secret

    create_secret(runtime_secret_name, {"SOME_OTHER_KEY": "x"}, private=True)
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr("leftover-from-the-repl"))
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, RuntimeError)
    assert runtime_secret_name in result.stderr
    assert "GEMINI_API_KEY" in result.stderr


def test_run_hitl_proxy_missing_secret_is_a_friendly_line_not_a_traceback(
    monkeypatch, _proxy_on, runtime_secret_name
):
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr("leftover-from-the-repl"))
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--hitl", "create config.toml"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, RuntimeError)
    assert runtime_secret_name in result.stderr
    assert "kitaru secrets set" in result.stderr


def test_run_command_proxy_with_a_valid_secret_runs_the_flow(
    monkeypatch, _proxy_on, runtime_secret_name
):
    from kitaru import create_secret

    create_secret(runtime_secret_name, {"GEMINI_API_KEY": "real-kitaru-key"}, private=True)
    _patch_seam(monkeypatch, "the proxied answer")

    result = CliRunner().invoke(cli, ["run", "summarize the repo"])

    assert result.exit_code == 0
    assert "the proxied answer" in result.output


# Secret-store config source: the `decode run` guard is RUNTIME_SECRET_STORE_CONFIG-aware
# (task 064 follow-up). When the secret-store source is on, the provider config (key/model/tuning) is
# hydrated from a Kitaru secret — but the cli's provider-config guard runs BEFORE the flow hydrates, so
# without a pre-flight a key living only in the secret tripped the misleading ``set GEMINI_API_KEY``
# line and a missing/malformed secret dumped a deep traceback from inside the flow. The pre-flight
# (mirroring the 061 ``_proxy_credential_error``) hydrates + validates up front: a secret-only key
# satisfies the guard, and a missing/malformed secret is one friendly stderr line, never a traceback.


@pytest.fixture
def _secret_store_on(monkeypatch, runtime_secret_name):
    """Enable the secret-store config source for gemini, runtime on, proxy off (secret created per test).

    Provider vars are cleared from the real env so a key/model living only in the Kitaru secret is the
    unambiguous source. The flag is set on the singleton directly; the source keys off the in-flow
    hydration flag the context manager flips, so this is enough for the cli pre-flight to engage it.
    ``runtime_secret_name`` (unique per test) is wired by the same-named fixture, not here.
    """
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_secret_store_config", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_credentials_proxy_enabled", False)
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    for var in ("GEMINI_API_KEY", "GEMINI_MODEL", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


def test_run_secret_store_only_key_satisfies_the_provider_guard(
    monkeypatch, _secret_store_on, runtime_secret_name
):
    """A key living ONLY in the Kitaru secret (proxy off) satisfies the guard — the run proceeds.

    Symptom 1 of the Tester-flagged gap: with RUNTIME_SECRET_STORE_CONFIG on and the key only in the
    secret, the old guard tripped ``set GEMINI_API_KEY`` and exited 1 even though the key WAS present.
    The secret-store pre-flight now hydrates Settings up front, so the guard sees the key and the flow
    runs. Asserted via the scripted seam — no real model call.
    """
    from kitaru import create_secret

    create_secret(
        runtime_secret_name,
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
    monkeypatch, _secret_store_on, runtime_secret_name
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
    assert runtime_secret_name in result.stderr
    assert "kitaru secrets set" in result.stderr


def test_run_hitl_secret_store_missing_secret_is_a_friendly_line_not_a_traceback(
    monkeypatch, _secret_store_on, runtime_secret_name
):
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--hitl", "create config.toml"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, (RuntimeError, ValidationError))
    assert "RUNTIME_SECRET_STORE_CONFIG" in result.stderr
    assert runtime_secret_name in result.stderr
    assert "kitaru secrets set" in result.stderr


def test_run_secret_store_malformed_secret_is_a_friendly_line_not_a_traceback(
    monkeypatch, _secret_store_on, runtime_secret_name
):
    """A stored value that fails a pydantic field (bogus LLM_PROVIDER) → friendly line, exit 1, no traceback.

    The malformed-secret half of symptom 2: a typo'd value used to raise a pydantic ValidationError
    from inside the flow. The pre-flight catches it (LLM_PROVIDER was cleared from the env, so the
    secret's bogus value is authoritative) and emits the same friendly line.
    """
    from kitaru import create_secret

    create_secret(runtime_secret_name, {"LLM_PROVIDER": "totally-bogus"}, private=True)
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, (RuntimeError, ValidationError))
    assert "RUNTIME_SECRET_STORE_CONFIG" in result.stderr
    assert runtime_secret_name in result.stderr


# task 069 (AC5): `--model` never alters the proxy / secret-store guard chain, no flow built


def test_run_model_does_not_bypass_the_proxy_secret_guard(
    monkeypatch, _proxy_on, runtime_secret_name
):
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--model", "gemini-2.5-pro", "list the files"])

    assert result.exit_code != 0
    assert runtime_secret_name in result.stderr
    assert "kitaru secrets set" in result.stderr


def test_run_model_does_not_bypass_the_secret_store_guard(
    monkeypatch, _secret_store_on, runtime_secret_name
):
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--model", "gemini-2.5-pro", "list the files"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, (RuntimeError, ValidationError))
    assert "RUNTIME_SECRET_STORE_CONFIG" in result.stderr
    assert runtime_secret_name in result.stderr


# task 071: the sandbox backend guard shares the `decode run` pre-flight (ADR-0011 §1)
# The same ``_sandbox_config_error`` the REPL uses is wired into ``_runtime_config_preflight``, so
# ``decode run`` refuses an unavailable sandbox backend the same friendly way — one stderr line,
# non-zero exit, before any flow is built. The probes are PATCHED (no real docker daemon / modal
# creds); ``sandbox_mode`` is pinned ``none`` suite-wide (rootdir conftest), overridden per test here.


def test_run_sandbox_docker_unreachable_is_a_friendly_line_no_flow(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(cli_mod, "_docker_daemon_reachable", lambda: False)
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert "SANDBOX_MODE=docker" in result.stderr
    assert "Docker daemon" in result.stderr
    assert "Traceback" not in result.stderr


def test_run_sandbox_modal_missing_creds_is_a_friendly_line_no_flow(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_mode", "modal")
    monkeypatch.setattr(cli_mod, "_modal_credentials_present", lambda: False)
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert "SANDBOX_MODE=modal" in result.stderr
    assert "modal token set" in result.stderr
    assert "Traceback" not in result.stderr


def test_run_sandbox_none_default_runs_no_probe_and_runs_the_flow(monkeypatch, _provider_ok):
    calls = {"docker": 0, "modal": 0}

    def _docker() -> bool:
        calls["docker"] += 1
        return False

    def _modal() -> bool:
        calls["modal"] += 1
        return False

    monkeypatch.setattr(cli_mod, "_docker_daemon_reachable", _docker)
    monkeypatch.setattr(cli_mod, "_modal_credentials_present", _modal)
    _patch_seam(monkeypatch, "the headless answer")

    result = CliRunner().invoke(cli, ["run", "summarize the module"])

    assert result.exit_code == 0
    assert "the headless answer" in result.output
    assert calls == {"docker": 0, "modal": 0}  # none mode probes nothing


# task 082: the Workspace repo — threaded into the flow, guarded in none mode (ADR-0012 §3)


def _recording_flow_run(monkeypatch, text):
    """Replace ``decode.runtime.run_agent_task`` with a fake recording the ``.run(...)`` kwargs.

    The cli imports the flow lazily (``from decode.runtime import run_agent_task``), so patching the
    attribute on ``decode.runtime`` is what the ``run`` body resolves at call time. Returns the
    ``captured`` kwargs dict so a test can assert the ``repo`` / ``local`` (and ``model``) the cli
    threaded into the durable flow (ADR-0012 §3). ``_load_runtime_output`` is stubbed to ``text``.
    """
    import decode.runtime as runtime_mod

    captured: dict[str, object] = {}

    class _FakeHandle:
        exec_id = "exec-fake"

    class _FakeFlow:
        def run(self, **kwargs):
            captured.update(kwargs)
            return _FakeHandle()

    monkeypatch.setattr(runtime_mod, "run_agent_task", _FakeFlow())
    monkeypatch.setattr(flow_mod, "_load_runtime_output", lambda exec_id: text)
    return captured


def test_run_help_documents_the_repo_and_local_flags():
    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "--repo" in result.output
    assert "--local" in result.output


def test_run_repo_and_local_threaded_into_the_flow(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(cli_mod, "_docker_daemon_reachable", lambda: True)
    captured = _recording_flow_run(monkeypatch, "the sandbox answer")

    result = CliRunner().invoke(cli, ["run", "--repo", "/some/repo", "--local", "build it"])

    assert result.exit_code == 0
    assert captured["repo"] == "/some/repo"  # the resolved --repo rode into the durable flow
    assert captured["local"] is True  # ...and the --local flag
    assert "the sandbox answer" in result.stdout


def test_run_repo_falls_back_to_sandbox_repo_setting(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(cli_mod.settings, "sandbox_repo", "https://from.env/repo.git")
    monkeypatch.setattr(cli_mod, "_docker_daemon_reachable", lambda: True)
    captured = _recording_flow_run(monkeypatch, "answer")

    result = CliRunner().invoke(cli, ["run", "build it"])

    assert result.exit_code == 0
    assert captured["repo"] == "https://from.env/repo.git"


def test_run_no_repo_threads_none_into_the_flow(monkeypatch, _provider_ok):
    captured = _recording_flow_run(monkeypatch, "answer")

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code == 0
    assert captured["repo"] is None
    assert captured["local"] is False


def test_run_repo_in_none_mode_is_a_friendly_line_no_flow(monkeypatch, _provider_ok):
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--repo", "/some/repo", "do it"])

    assert result.exit_code != 0
    assert "--repo/SANDBOX_REPO" in result.stderr
    assert "SANDBOX_MODE=docker" in result.stderr  # names the fix
    assert "Traceback" not in result.stderr


def test_run_sandbox_repo_env_in_none_mode_is_a_friendly_line_no_flow(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_repo", "https://from.env/repo.git")
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert "--repo/SANDBOX_REPO" in result.stderr


def test_replay_sandbox_repo_env_in_none_mode_is_a_friendly_line(monkeypatch, _provider_ok):

    def _tripwire(*_a, **_k):
        raise AssertionError("no replay may run when the sandbox-repo guard trips")

    monkeypatch.setattr(cli_mod.settings, "sandbox_repo", "https://from.env/repo.git")
    monkeypatch.setattr(flow_mod, "replay_agent_task", _tripwire)

    result = CliRunner().invoke(cli, ["replay", "exec-123", "--from", "some_checkpoint"])

    assert result.exit_code != 0
    assert "--repo/SANDBOX_REPO" in result.stderr
    assert "Traceback" not in result.stderr
