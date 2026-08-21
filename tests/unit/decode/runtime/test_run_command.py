"""``decode run "<task>"`` — the headless subcommand end to end (ADR-0019 §1).

Drives the real Click ``run`` subcommand through ``CliRunner`` with the runner boundary swapped at
``decode.runtime.run_headless_task``. Covers the UX contract the durable runtime used to own:
stdout carries ONLY the agent's answer (pipe-safe), ``--model`` / ``--repo`` / ``--local`` thread
through, and the pre-flight guard chain — Environment Bucket (ADR-0015 §5), provider config,
``RUNTIME_ENABLED``, sandbox backend, sandbox repo — each a friendly stderr line + non-zero exit
that never builds an agent.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from pydantic import SecretStr, ValidationError

import decode.cli as cli_mod
import decode.runtime as runtime_mod
from decode.cli import cli


@pytest.fixture
def _provider_ok(monkeypatch):
    """Seed the gemini provider config so the ``decode run`` provider guard passes (offline)."""
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)


def _recording_runner(monkeypatch, text: str) -> dict[str, object]:
    """Replace the headless runner with a fake recording its kwargs; it returns ``text``.

    The cli imports the runner lazily (``from decode.runtime import run_headless_task``), so
    patching the attribute on ``decode.runtime`` is what the ``run`` body resolves at call time.
    """
    captured: dict[str, object] = {}

    def _fake(task: str, **kwargs: object) -> str:
        captured["task"] = task
        captured.update(kwargs)
        return text

    monkeypatch.setattr(runtime_mod, "run_headless_task", _fake)
    return captured


def _no_runner_tripwire(monkeypatch) -> None:
    """Make the runner blow up if reached — a tripped guard must exit before any agent is built."""

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("no agent may be built when a startup guard trips")

    monkeypatch.setattr(runtime_mod, "run_headless_task", _tripwire)


# --- the happy path: the answer, on stdout, and nothing else ------------------------------------


def test_run_command_prints_the_agents_output(monkeypatch, _provider_ok):
    _recording_runner(monkeypatch, "the headless answer")

    result = CliRunner().invoke(cli, ["run", "summarize the cli module"])

    assert result.exit_code == 0
    assert "the headless answer" in result.stdout


def test_run_stdout_is_exactly_the_answer_and_stderr_is_silent(monkeypatch, _provider_ok):
    """AC3: a piped ``decode run`` yields the answer alone — the exec_id/replay hints are gone."""
    _recording_runner(monkeypatch, "the piped answer")

    result = CliRunner().invoke(cli, ["run", "summarize the module"])

    assert result.exit_code == 0
    assert result.stdout == "the piped answer\n"
    assert result.stderr == ""


def test_run_threads_the_task_and_defaults_into_the_runner(monkeypatch, _provider_ok):
    captured = _recording_runner(monkeypatch, "answer")

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code == 0
    assert captured["task"] == "list the files"
    assert captured["model"] is None
    assert captured["repo"] is None
    assert captured["local"] is False


# --- the deleted surfaces: `decode replay` and `decode run --hitl` -------------------------------


def test_replay_command_no_longer_exists():
    """AC2: the what-if replay CLI died with the flow — Click reports no such command."""
    assert "replay" not in cli.commands

    result = CliRunner().invoke(cli, ["replay", "exec-123", "--from", "some_checkpoint"])

    assert result.exit_code != 0
    assert "No such command" in result.stderr


def test_run_hitl_flag_no_longer_exists(monkeypatch, _provider_ok):
    """AC2: HITL is removed, not deferred — upstream has no wait primitive (ADR-0019 §1)."""
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--hitl", "create config.toml"])

    assert result.exit_code != 0
    assert "No such option" in result.stderr


def test_run_help_no_longer_advertises_waits_or_replay():
    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "--hitl" not in result.output
    assert "replay" not in result.output.lower()


# --- the model override ---------------------------------------------------------------------------


def test_run_help_documents_the_model_flag():
    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "--model" in result.output
    assert "gemini-2.5-pro" in result.output  # the help's example model id
    assert "LLM_PROVIDER" in result.output  # notes it does NOT change the provider


def test_run_model_flag_threads_the_override_to_the_runner(monkeypatch, _provider_ok):
    captured = _recording_runner(monkeypatch, "the overridden answer")

    result = CliRunner().invoke(cli, ["run", "--model", "gemini-2.5-pro", "refactor the parser"])

    assert result.exit_code == 0
    assert captured["model"] == "gemini-2.5-pro"
    assert "the overridden answer" in result.stdout


# --- the guard chain: one friendly stderr line, non-zero exit, no agent built ---------------------


def test_run_command_disabled_runtime_guard_does_not_build_an_agent(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", False)
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "do it"])

    assert result.exit_code != 0
    assert "headless runtime is disabled" in result.stderr
    assert "RUNTIME_ENABLED=true" in result.stderr


def test_run_command_provider_guard_fires_without_a_key(monkeypatch):
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "do it"])

    assert result.exit_code != 0
    assert "GEMINI_API_KEY" in result.stderr


def test_run_model_does_not_bypass_the_disabled_runtime_guard(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", False)
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--model", "gemini-2.5-pro", "do it"])

    assert result.exit_code != 0
    assert "headless runtime is disabled" in result.stderr


# Environment Bucket: the `decode run` pre-flight guards a remote DECODE_ENV whose bucket could not
# be loaded (ADR-0015 §5). Hydration is process-scoped (it happened at settings import), so the
# bucket source records a failure instead of raising; the pre-flight turns it into ONE friendly line
# — FIRST in the chain, because at a remote env the provider key is EXPECTED to come from the
# bucket, so a bucket failure must name `make sync-secrets ENV=<env>`, never GEMINI_API_KEY.


@pytest.fixture
def _bucket_unloadable(monkeypatch):
    """Pin ``DECODE_ENV=staging`` with a captured bucket-load failure, and no provider key."""
    monkeypatch.setattr(cli_mod.settings, "decode_env", "staging")
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    monkeypatch.setattr(cli_mod, "bucket_load_error", lambda: "decode-staging: secret not found")


def test_run_unloadable_bucket_is_a_friendly_line_not_a_traceback(monkeypatch, _bucket_unloadable):
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, (RuntimeError, ValidationError))
    assert "DECODE_ENV=staging" in result.stderr
    assert "decode-staging" in result.stderr  # the derived bucket name
    assert "make sync-secrets ENV=staging" in result.stderr  # ...and the fix
    assert "Traceback" not in result.stderr


def test_run_bucket_guard_precedes_the_provider_key_guard(monkeypatch, _bucket_unloadable):
    """The provider key is missing too — but the bucket line is the one that fires (ADR-0015 §5)."""
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert "make sync-secrets" in result.stderr
    assert "set GEMINI_API_KEY in your environment" not in result.stderr


def test_run_remote_env_with_a_healthy_bucket_runs_the_task(monkeypatch, _provider_ok):
    """A remote env whose bucket loaded cleanly is invisible to the guard chain — the run proceeds."""
    monkeypatch.setattr(cli_mod.settings, "decode_env", "prod")
    monkeypatch.setattr(cli_mod, "bucket_load_error", lambda: None)
    _recording_runner(monkeypatch, "the hydrated answer")

    result = CliRunner().invoke(cli, ["run", "summarize the repo"])

    assert result.exit_code == 0
    assert "the hydrated answer" in result.stdout


# The sandbox backend guard shares the `decode run` pre-flight (ADR-0011 §1). The probes are PATCHED
# (no real docker daemon / modal creds); ``sandbox_mode`` is pinned ``none`` suite-wide (rootdir
# conftest), overridden per test here.


def test_run_sandbox_docker_unreachable_is_a_friendly_line_no_agent(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(cli_mod, "_docker_daemon_reachable", lambda: False)
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert "SANDBOX_MODE=docker" in result.stderr
    assert "Docker daemon" in result.stderr
    assert "Traceback" not in result.stderr


def test_run_sandbox_modal_missing_creds_is_a_friendly_line_no_agent(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_mode", "modal")
    monkeypatch.setattr(cli_mod, "_modal_credentials_present", lambda: False)
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert "SANDBOX_MODE=modal" in result.stderr
    assert "modal token set" in result.stderr
    assert "Traceback" not in result.stderr


def test_run_sandbox_none_default_runs_no_probe_and_runs_the_task(monkeypatch, _provider_ok):
    calls = {"docker": 0, "modal": 0}

    def _docker() -> bool:
        calls["docker"] += 1
        return False

    def _modal() -> bool:
        calls["modal"] += 1
        return False

    monkeypatch.setattr(cli_mod, "_docker_daemon_reachable", _docker)
    monkeypatch.setattr(cli_mod, "_modal_credentials_present", _modal)
    _recording_runner(monkeypatch, "the headless answer")

    result = CliRunner().invoke(cli, ["run", "summarize the module"])

    assert result.exit_code == 0
    assert "the headless answer" in result.stdout
    assert calls == {"docker": 0, "modal": 0}  # none mode probes nothing


# --- the Workspace repo: threaded into the runner, guarded in none mode (ADR-0012 §3) ------------


def test_run_help_documents_the_repo_and_local_flags():
    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "--repo" in result.output
    assert "--local" in result.output


def test_run_repo_and_local_threaded_into_the_runner(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(cli_mod, "_docker_daemon_reachable", lambda: True)
    captured = _recording_runner(monkeypatch, "the sandbox answer")

    result = CliRunner().invoke(cli, ["run", "--repo", "/some/repo", "--local", "build it"])

    assert result.exit_code == 0
    assert captured["repo"] == "/some/repo"
    assert captured["local"] is True
    assert "the sandbox answer" in result.stdout


def test_run_repo_falls_back_to_sandbox_repo_setting(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(cli_mod.settings, "sandbox_repo", "https://from.env/repo.git")
    monkeypatch.setattr(cli_mod, "_docker_daemon_reachable", lambda: True)
    captured = _recording_runner(monkeypatch, "answer")

    result = CliRunner().invoke(cli, ["run", "build it"])

    assert result.exit_code == 0
    assert captured["repo"] == "https://from.env/repo.git"


def test_run_repo_in_none_mode_is_a_friendly_line_no_agent(monkeypatch, _provider_ok):
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--repo", "/some/repo", "do it"])

    assert result.exit_code != 0
    assert "--repo/SANDBOX_REPO" in result.stderr
    assert "SANDBOX_MODE=docker" in result.stderr  # names the fix
    assert "Traceback" not in result.stderr


def test_run_sandbox_repo_env_in_none_mode_is_a_friendly_line_no_agent(monkeypatch, _provider_ok):
    monkeypatch.setattr(cli_mod.settings, "sandbox_repo", "https://from.env/repo.git")
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "list the files"])

    assert result.exit_code != 0
    assert "--repo/SANDBOX_REPO" in result.stderr
