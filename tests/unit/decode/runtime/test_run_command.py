"""``decode run "<task>"`` — the headless subcommand end to end (ADR-0019 §1).

Drives the real Click ``run`` subcommand through ``CliRunner`` with the runner boundary swapped at
``decode.runtime.run_headless_task``. Covers the UX contract the durable runtime used to own:
stdout carries ONLY the agent's answer (pipe-safe), ``--model`` / ``--repo`` / ``--local`` thread
through, and the pre-flight guard chain — Environment Bucket (ADR-0015 §5), provider config,
``RUNTIME_ENABLED``, sandbox backend, sandbox repo — each a friendly stderr line + non-zero exit
that never builds an agent.
"""

from __future__ import annotations

import logging

import pytest
from click.testing import CliRunner
from pydantic import SecretStr, ValidationError
from pydantic_ai.exceptions import ModelHTTPError
from support.kitaru_recording import kitaru_api_error

import decode.cli as cli_mod
import decode.runtime as runtime_mod
from decode.cli import cli
from decode.runtime.recording import RecordingUnavailableError


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
    assert captured["max_requests"] is None


# --- the request ceiling: --max-requests ----------------------------------------------------------


def test_run_max_requests_flag_threads_into_the_runner(monkeypatch, _provider_ok):
    captured = _recording_runner(monkeypatch, "answer")

    result = CliRunner().invoke(cli, ["run", "--max-requests", "40", "list the files"])

    assert result.exit_code == 0
    assert captured["max_requests"] == 40


def test_run_max_requests_must_be_a_positive_count(monkeypatch, _provider_ok):
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "--max-requests", "0", "list the files"])

    assert result.exit_code != 0
    assert "--max-requests" in result.output


def test_run_past_the_ceiling_is_one_friendly_line_and_a_non_zero_exit(monkeypatch, _provider_ok):
    from pydantic_ai.exceptions import UsageLimitExceeded

    def _capped(*_args, **_kwargs):
        raise UsageLimitExceeded("The next request would exceed the request_limit of 3")

    monkeypatch.setattr(runtime_mod, "run_headless_task", _capped)

    result = CliRunner().invoke(cli, ["run", "--max-requests", "3", "loop"])

    assert result.exit_code == 1
    assert result.stdout == ""  # no answer: stdout stays pipe-clean
    assert "Decode: the run stopped at its request ceiling" in result.stderr
    assert "request_limit of 3" in result.stderr
    assert "Traceback" not in result.stderr


def test_run_help_documents_the_max_requests_flag():
    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "--max-requests" in result.output
    assert "RUNTIME_MAX_REQUESTS" in result.output


def _recording_unavailable_runner(monkeypatch) -> None:
    """Make the runner raise the Seam's hard failure, as a Worker Task with a dead workspace does."""

    def _fails(*_args, **_kwargs):
        raise RecordingUnavailableError("[kitaru] recording is unavailable for this Worker Task")

    monkeypatch.setattr(runtime_mod, "run_headless_task", _fails)


def test_run_exits_non_zero_when_a_worker_task_cannot_be_recorded(monkeypatch, _provider_ok):
    """AC4: the Recording Seam's hard failure reaches the process exit code (ADR-0019 §3).

    The runner raises :class:`RecordingUnavailableError` under a Worker Task whose workspace is
    unreachable; ``decode run`` must NOT swallow it into a 0 — a Kitaru Worker reads the exit code to
    decide whether the replay produced anything trustworthy.
    """
    _recording_unavailable_runner(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "replay this"])

    assert result.exit_code != 0
    assert "recording is unavailable" in result.stderr
    assert result.stdout == ""  # nothing on stdout to mistake for an answer


def test_run_recording_hard_failure_is_a_friendly_line_not_a_traceback(monkeypatch, _provider_ok):
    """Same contract as every other guard: ONE stderr line, no framework frames (ADR-0019 §3).

    A Kitaru Worker captures this stderr; 40 lines of pydantic-ai/httpx frames bury the one fact
    that matters. The traceback still goes to the log file for whoever debugs the workspace.
    """
    _recording_unavailable_runner(monkeypatch)

    result = CliRunner().invoke(cli, ["run", "replay this"])

    assert result.stderr == "Decode: [kitaru] recording is unavailable for this Worker Task\n"
    assert "Traceback" not in result.stderr


# --- the Worker Task entry: the task arrives in the env when the CLI arg is absent (ADR-0019 §4) --


def _worker_env(monkeypatch, inputs: str | None) -> None:
    """Put the process in Worker Task mode with ``inputs`` as the raw ``KITARU_TASK_INPUTS``."""
    monkeypatch.setenv("KITARU_TASK_ID", "4d0a3a5e-0000-4000-8000-00000000beef")
    if inputs is not None:
        monkeypatch.setenv("KITARU_TASK_INPUTS", inputs)


def test_run_without_a_task_or_a_worker_context_is_a_friendly_line(monkeypatch, _provider_ok):
    """AC1: no arg, no Kitaru task context → ONE stderr line and a non-zero exit, no agent built."""
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code != 0
    assert "TASK" in result.stderr
    assert "KITARU_TASK_INPUTS" in result.stderr  # names the Worker Task channel too
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_run_takes_its_task_from_the_worker_task_inputs(monkeypatch, _provider_ok):
    """AC2: ``KITARU_TASK_ID`` + ``KITARU_TASK_INPUTS`` run the task with no CLI arg."""
    _worker_env(monkeypatch, '{"task":"say hi"}')
    captured = _recording_runner(monkeypatch, "hi there")

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert captured["task"] == "say hi"
    assert result.stdout == "hi there\n"


def test_run_cli_task_wins_over_the_worker_task_inputs(monkeypatch, _provider_ok):
    """AC3: an explicit TASK beats the env channel."""
    _worker_env(monkeypatch, '{"task":"from the worker"}')
    captured = _recording_runner(monkeypatch, "answer")

    result = CliRunner().invoke(cli, ["run", "from the cli"])

    assert result.exit_code == 0
    assert captured["task"] == "from the cli"


def test_run_worker_task_inputs_model_threads_into_the_model_override(monkeypatch, _provider_ok):
    """AC4: ``model`` in the inputs reaches the runner exactly like ``--model`` (ADR-0019 §4)."""
    _worker_env(monkeypatch, '{"task":"say hi","model":"gemini-2.5-pro"}')
    captured = _recording_runner(monkeypatch, "answer")

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert captured["model"] == "gemini-2.5-pro"


def test_run_malformed_worker_task_inputs_exits_non_zero_naming_the_parse_failure(
    monkeypatch, _provider_ok
):
    """AC5: a Worker replay must never guess — the parse failure is named, exit is non-zero."""
    _worker_env(monkeypatch, "{not json")
    _no_runner_tripwire(monkeypatch)

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code != 0
    assert "KITARU_TASK_INPUTS" in result.stderr
    assert "JSONDecodeError" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_run_help_documents_the_optional_task_and_its_worker_source():
    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "[TASK]" in result.output  # Click renders an optional argument in brackets
    assert "KITARU_TASK_INPUTS" in result.output


# --- the lazily-created Kitaru Session: a worker-gated one-liner too (task 139, ADR-0019 §3) ------
#
# ``kitaru-pydantic-ai`` creates the Kitaru Session INSIDE ``agent.run``, after the Recording Seam's
# wrap-time probe — so a session-creation failure escapes the runner as a raw kitaru client error.
# Under a Worker Task that owes the operator the same ONE ``Decode:`` line every other recording
# failure gets; everywhere else, and for every failure that is the agent's own, it must propagate
# untouched.


def _failing_runner(monkeypatch, error: BaseException) -> None:
    """Make the headless runner raise ``error``, as an escaping ``agent.run`` failure does."""

    def _fails(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(runtime_mod, "run_headless_task", _fails)


def test_a_worker_session_creation_failure_is_one_friendly_line(monkeypatch, _provider_ok):
    """AC1: the reproduced 422 — ONE stderr line naming the cause, no traceback, non-zero exit."""
    _worker_env(monkeypatch, '{"task":"say hi"}')
    _failing_runner(
        monkeypatch,
        kitaru_api_error(
            422, "Session names no agent and no task to infer one from", name="ValidationError"
        ),
    )

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code != 0
    assert result.stderr.startswith("Decode: [kitaru] ")
    assert result.stderr.count("\n") == 1  # exactly ONE line
    assert "ValidationError: 422: Session names no agent" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""  # nothing on stdout to mistake for an answer


def test_a_worker_session_creation_failure_keeps_the_traceback_in_the_log(
    monkeypatch, _provider_ok, caplog
):
    """The stderr line is short BECAUSE the full traceback went to ``.decode/logs/decode.log``."""
    _worker_env(monkeypatch, '{"task":"say hi"}')
    _failing_runner(monkeypatch, kitaru_api_error(422, "no such task", name="ValidationError"))

    with caplog.at_level(logging.WARNING, logger="decode.cli"):
        CliRunner().invoke(cli, ["run"])

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is not None  # the frames the operator needs, in the log only


def test_a_worker_403_names_the_agent_id_trap(monkeypatch, _provider_ok):
    """The line diagnoses the misconfiguration it almost always is (08_evals_replays §7.3)."""
    _worker_env(monkeypatch, '{"task":"say hi"}')
    _failing_runner(
        monkeypatch,
        kitaru_api_error(
            403, "Task credentials are not accepted on this route", name="AuthorizationError"
        ),
    )

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code != 0
    assert "KITARU_AGENT_ID" in result.stderr
    assert result.stderr.count("\n") == 1


def test_a_worker_agent_failure_is_never_rewritten_as_a_recording_line(monkeypatch, _provider_ok):
    """AC3: a provider 503 inside a replay is an AGENT failure — the Worker log must say so."""
    _worker_env(monkeypatch, '{"task":"say hi"}')
    error = ModelHTTPError(status_code=503, model_name="gemini-2.5-flash", body="upstream down")
    _failing_runner(monkeypatch, error)

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code != 0
    assert result.exception is error  # propagated, not swallowed
    assert "[kitaru]" not in result.stderr


def test_a_user_launched_kitaru_failure_still_propagates(monkeypatch, _provider_ok):
    """AC4: the catch is worker-gated — outside a Worker Task nothing about this path changes."""
    error = kitaru_api_error(422, "no such task", name="ValidationError")
    _failing_runner(monkeypatch, error)

    result = CliRunner().invoke(cli, ["run", "record me"])

    assert result.exit_code != 0
    assert result.exception is error
    assert "[kitaru] recording is unavailable" not in result.stderr


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
