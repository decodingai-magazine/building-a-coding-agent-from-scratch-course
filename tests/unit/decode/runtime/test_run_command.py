"""``decode run "<task>"`` — the headless subcommand end to end (ADR-0008).

Drives the real Click ``run`` subcommand through ``CliRunner``, with the model boundary swapped via
the ``_build_runtime_agent`` seam and the Kitaru store isolated (the autouse rootdir fixture). Covers
the happy path (prints the agent's text) and the pre-flight guard chain — Environment Bucket (ADR-0015
§5), provider config, ``RUNTIME_ENABLED``, sandbox — each a friendly stderr line + non-zero exit that
never builds a flow.
"""

from __future__ import annotations

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


# the Workspace hand-back is the FLOW's job, not the submitter's (ADR-0012 §8; see test_flow.py)


def test_run_never_ships_from_the_submitting_process(monkeypatch, _provider_ok, mocker):
    """``decode run`` must not hand back its own ``.decode/sandbox`` — the flow owns the Workspace.

    On a remote stack the submitting process is a laptop whose ``.decode/sandbox`` the run never
    touched (its work lives in the Modal flow container). Shipping from here pushed that stranger
    directory; the hand-back now runs inside the flow, where the Workspace actually is.
    """
    _patch_seam(monkeypatch, "done")
    ship = mocker.patch("decode.sandbox.handback.ship_workspace")

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code == 0
    ship.assert_not_called()
    assert not hasattr(cli_mod, "_auto_ship_headless")  # the submitter-side ship is gone for good


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


# Environment Bucket: the `decode run` pre-flight guards a remote DECODE_ENV whose bucket could not be
# loaded (ADR-0015 §5, task 097). Hydration is process-scoped (it happened at settings import), so the
# bucket source records a failure instead of raising; the pre-flight turns it into ONE friendly line —
# FIRST in the chain, because at a remote env the provider key is EXPECTED to come from the bucket, so
# a bucket failure must name `make sync-secrets ENV=<env>`, never GEMINI_API_KEY.


def _no_flow_tripwires(monkeypatch):
    """Make both runtime seams blow up if reached — the guard must exit before any flow is built."""

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("no flow may be built when a startup guard trips")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", _tripwire)
    monkeypatch.setattr(flow_mod, "_build_hitl_runtime_agent", _tripwire)


@pytest.fixture
def _bucket_unloadable(monkeypatch):
    """Pin ``DECODE_ENV=staging`` with a captured bucket-load failure, and no provider key.

    The realistic remote shape: the key would have come from the bucket, so ``gemini_api_key`` is
    empty. The bucket guard must win over the provider guard (else the user is told to set
    GEMINI_API_KEY, which is not the fix).
    """
    monkeypatch.setattr(cli_mod.settings, "decode_env", "staging")
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    monkeypatch.setattr(cli_mod, "bucket_load_error", lambda: "decode-staging: secret not found")


def test_run_unloadable_bucket_is_a_friendly_line_not_a_traceback(monkeypatch, _bucket_unloadable):
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, (RuntimeError, ValidationError))
    assert "DECODE_ENV=staging" in result.stderr
    assert "decode-staging" in result.stderr  # the derived bucket name
    assert "make sync-secrets ENV=staging" in result.stderr  # ...and the fix
    assert "Traceback" not in result.stderr


def test_run_bucket_guard_precedes_the_provider_key_guard(monkeypatch, _bucket_unloadable):
    """The provider key is missing too — but the bucket line is the one that fires (ADR-0015 §5)."""
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert "make sync-secrets" in result.stderr
    assert "set GEMINI_API_KEY in your environment" not in result.stderr


def test_run_hitl_unloadable_bucket_is_a_friendly_line(monkeypatch, _bucket_unloadable):
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--hitl", "create config.toml"])

    assert result.exit_code != 0
    assert "make sync-secrets ENV=staging" in result.stderr


def test_run_model_does_not_bypass_the_bucket_guard(monkeypatch, _bucket_unloadable):
    _no_flow_tripwires(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--model", "gemini-2.5-pro", "list the files"])

    assert result.exit_code != 0
    assert "make sync-secrets ENV=staging" in result.stderr


def test_run_remote_env_with_a_healthy_bucket_runs_the_flow(monkeypatch, _provider_ok):
    """A remote env whose bucket loaded cleanly is invisible to the guard chain — the run proceeds."""
    monkeypatch.setattr(cli_mod.settings, "decode_env", "prod")
    monkeypatch.setattr(cli_mod, "bucket_load_error", lambda: None)
    _patch_seam(monkeypatch, "the hydrated answer")

    result = CliRunner().invoke(cli, ["run", "summarize the repo"])

    assert result.exit_code == 0
    assert "the hydrated answer" in result.output


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
