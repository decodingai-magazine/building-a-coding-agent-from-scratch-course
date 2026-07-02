"""``decode replay <exec_id> [--from] [--model]`` — the what-if subcommand (ADR-0010 §5-6, task 070).

Drives the real Click ``replay`` subcommand through ``CliRunner``, mocking only the kitaru boundary
(``is_hitl_execution`` / ``replay_agent_task``, re-exported from :mod:`decode.runtime`) so the cli's
own logic — the guard chain, the ``--from`` requirement, the bypass-only HITL refusal, and the
friendly rendering of each Kitaru replay failure — is exercised offline without booting a flow. The
REAL flow-object replay (a model swap re-executing downstream turns) is proven end to end, on an
isolated Kitaru store, by ``test_model_swap_replay_re_executes_downstream_turns`` in the runtime
capstone — this file is the cli-contract half.

Detection / replay are patched on the ``decode.runtime`` package because the command binds them with
``from decode.runtime import is_hitl_execution, replay_agent_task`` at call time (so the package
attribute is what it reads).
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from kitaru.errors import KitaruBackendError, KitaruDivergenceError, KitaruStateError
from pydantic import SecretStr

import decode.cli as cli_mod
import decode.runtime as runtime_pkg
from decode.cli import cli
from decode.runtime import ReplayResult

# The replay command imports kitaru lazily but the mocks below import ``kitaru.errors`` at module load
# (cheap, no store). Scope the two third-party deprecation warnings the runtime stack emits, matching
# the sibling runtime tests, so the strict ``filterwarnings=["error"]`` gate stays green.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


@pytest.fixture
def _provider_ok(monkeypatch):
    """Seed the gemini provider config + runtime-on so the ``decode replay`` guard chain passes (offline)."""
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)


def _patch_replay(monkeypatch, *, hitl=False, result=None, replay_exc=None, detect_exc=None):
    """Mock the kitaru boundary the command imports from ``decode.runtime``.

    ``is_hitl_execution`` returns ``hitl`` (or raises ``detect_exc``); ``replay_agent_task`` returns
    ``result`` (or raises ``replay_exc``). Returns a ``calls`` dict recording whether each was invoked,
    so a guard/refusal test can assert no replay was attempted.
    """
    calls = {"detect": 0, "replay": 0}

    def _detect(exec_id):
        calls["detect"] += 1
        if detect_exc is not None:
            raise detect_exc
        return hitl

    def _replay(exec_id, *, from_, model):
        calls["replay"] += 1
        if replay_exc is not None:
            raise replay_exc
        return result

    monkeypatch.setattr(runtime_pkg, "is_hitl_execution", _detect)
    monkeypatch.setattr(runtime_pkg, "replay_agent_task", _replay)
    return calls


# --- help + the --from requirement (Kitaru 1:1, no decode-invented default) -------------------------


def test_replay_help_documents_from_and_model():
    """``decode replay --help`` documents ``--from`` and ``--model`` and the bypass-only scope."""
    result = CliRunner().invoke(cli, ["replay", "--help"])

    assert result.exit_code == 0
    assert "--from" in result.output
    assert "--model" in result.output
    assert "bypass-only" in result.output


def test_replay_without_from_surfaces_kitarus_requirement(monkeypatch, _provider_ok):
    """Omitting ``--from`` exits non-zero with one friendly line — Kitaru requires an anchor (AC3).

    decode invents no default anchor; it mirrors Kitaru's own requirement. No replay is attempted.
    """
    calls = _patch_replay(monkeypatch, result=_ok_result())

    result = CliRunner().invoke(cli, ["replay", "kr-abc123", "--model", "gemini-2.5-pro"])

    assert result.exit_code != 0
    assert "--from" in result.stderr
    assert "no default" in result.stderr
    assert calls == {"detect": 0, "replay": 0}  # exited before touching kitaru
    assert result.stdout == ""


# --- bypass-only: a HITL exec_id is refused with guidance -------------------------------------------


def test_replay_refuses_a_hitl_execution(monkeypatch, _provider_ok):
    """A HITL exec_id exits non-zero, points at ``kitaru executions replay``, and never replays (AC4)."""
    calls = _patch_replay(monkeypatch, hitl=True)

    result = CliRunner().invoke(cli, ["replay", "kr-hitl-1", "--from", "cp", "--model", "x"])

    assert result.exit_code != 0
    assert "bypass-only" in result.stderr
    assert "kitaru executions replay kr-hitl-1" in result.stderr
    assert calls["detect"] == 1  # detection ran
    assert calls["replay"] == 0  # but the replay was refused, not attempted
    assert not isinstance(result.exception, KitaruStateError)


# --- happy path: prints the (possibly changed) answer + the fork hint -------------------------------


def _ok_result():
    return ReplayResult(
        exec_id="kr-fork-9", original_exec_id="kr-abc123", output="the swapped answer"
    )


def test_replay_prints_answer_on_stdout_and_fork_hint_on_stderr(monkeypatch, _provider_ok):
    """A successful replay prints the answer on stdout; the fork id + source + diff hint on stderr (AC1).

    stdout stays pipe-clean (only the answer); the discoverability scaffolding — the new Fork exec_id,
    the source exec_id, and a CONFIRMED-surface diff hint (``kitaru executions get``) — is on stderr.
    """
    _patch_replay(monkeypatch, result=_ok_result())

    result = CliRunner().invoke(
        cli, ["replay", "kr-abc123", "--from", "decode_runtime_model_request", "--model", "gp"]
    )

    assert result.exit_code == 0
    # stdout is pipe-clean: exactly the (possibly changed) answer, none of the scaffolding.
    assert "the swapped answer" in result.stdout
    assert "exec_id:" not in result.stdout
    assert "kitaru executions get" not in result.stdout
    # stderr carries the fork id, the source id, and a diff hint on the CONFIRMED kitaru surface.
    assert "kr-fork-9" in result.stderr
    assert "kr-abc123" in result.stderr
    assert "kitaru executions get kr-fork-9" in result.stderr
    # No unconfirmed `kitaru diff` command is presented as fact (there is none in kitaru 0.18).
    assert "kitaru diff" not in result.stderr


def test_replay_without_model_replays_as_is(monkeypatch, _provider_ok):
    """Omitting ``--model`` forwards ``model=None`` (replay as-is) and still prints + hints."""
    captured = {}

    def _detect(exec_id):
        return False

    def _replay(exec_id, *, from_, model):
        captured["model"] = model
        captured["from_"] = from_
        return _ok_result()

    monkeypatch.setattr(runtime_pkg, "is_hitl_execution", _detect)
    monkeypatch.setattr(runtime_pkg, "replay_agent_task", _replay)

    result = CliRunner().invoke(cli, ["replay", "kr-abc123", "--from", "cp"])

    assert result.exit_code == 0
    assert captured == {"model": None, "from_": "cp"}  # --from threaded 1:1, no model swap
    assert "the swapped answer" in result.stdout


# --- Kitaru replay failures: each is one friendly stderr line, no raw traceback ---------------------


def test_replay_invalid_from_is_a_friendly_line(monkeypatch, _provider_ok):
    """An ambiguous/invalid ``--from`` (``KitaruStateError``) → one friendly line, non-zero, no traceback (AC5)."""
    exc = KitaruStateError(
        "Unknown checkpoint selector 'nope'. Available checkpoints: _capture_runtime_output, read_tool."
    )
    _patch_replay(monkeypatch, replay_exc=exc)

    result = CliRunner().invoke(cli, ["replay", "kr-abc123", "--from", "nope"])

    assert result.exit_code != 0
    # The raw kitaru error did not escape as a traceback…
    assert not isinstance(result.exception, KitaruStateError)
    # …and the friendly line surfaces Kitaru's own available-checkpoints hint so the operator can retry.
    assert "--from anchor" in result.stderr
    assert "Available checkpoints" in result.stderr
    assert "kitaru executions get" in result.stderr
    assert result.stdout == ""


def test_replay_diverged_swap_is_a_friendly_line(monkeypatch, _provider_ok):
    """A swap that diverged the recorded call sequence (``KitaruDivergenceError``) → friendly line (AC5)."""
    _patch_replay(
        monkeypatch, replay_exc=KitaruDivergenceError("call sequence compatibility broken")
    )

    result = CliRunner().invoke(cli, ["replay", "kr-abc123", "--from", "cp", "--model", "big"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, KitaruDivergenceError)
    assert "diverged" in result.stderr
    assert "kr-abc123" in result.stderr
    assert result.stdout == ""


def test_replay_missing_exec_id_is_a_friendly_line(monkeypatch, _provider_ok):
    """A missing/unloadable exec_id (``KitaruBackendError`` from detection) → friendly line, no traceback."""
    exc = KitaruBackendError("Failed to load execution 'kr-nope': No runs have been found …")
    _patch_replay(monkeypatch, detect_exc=exc)

    result = CliRunner().invoke(cli, ["replay", "kr-nope", "--from", "cp"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, KitaruBackendError)
    assert "could not load or execute kr-nope" in result.stderr
    assert "kitaru executions list" in result.stderr
    assert result.stdout == ""


# --- the full run guard chain fires for replay too (no flow/replay attempted) -----------------------


def test_replay_disabled_runtime_guard_does_not_replay(monkeypatch, _provider_ok):
    """``RUNTIME_ENABLED=false`` → friendly line, non-zero, and no detection/replay attempted (AC6)."""
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", False)
    calls = _patch_replay(monkeypatch, result=_ok_result())

    result = CliRunner().invoke(cli, ["replay", "kr-abc123", "--from", "cp"])

    assert result.exit_code != 0
    assert "headless runtime is disabled" in result.stderr
    assert calls == {"detect": 0, "replay": 0}


def test_replay_provider_key_guard_does_not_replay(monkeypatch):
    """A missing provider key trips the same guard as ``decode run``: friendly line, no replay (AC6)."""
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    calls = _patch_replay(monkeypatch, result=_ok_result())

    result = CliRunner().invoke(cli, ["replay", "kr-abc123", "--from", "cp"])

    assert result.exit_code != 0
    assert "GEMINI_API_KEY" in result.stderr
    assert calls == {"detect": 0, "replay": 0}


def test_replay_proxy_missing_secret_guard_does_not_replay(monkeypatch, runtime_secret_name):
    """Proxy on + a missing Kitaru secret → the proxy pre-flight fires for replay too, no replay (AC6)."""
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_credentials_proxy_enabled", True)
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    calls = _patch_replay(monkeypatch, result=_ok_result())

    result = CliRunner().invoke(cli, ["replay", "kr-abc123", "--from", "cp"])

    assert result.exit_code != 0
    assert runtime_secret_name in result.stderr
    assert "kitaru secrets set" in result.stderr
    assert calls == {"detect": 0, "replay": 0}


def test_replay_secret_store_missing_secret_guard_does_not_replay(monkeypatch, runtime_secret_name):
    """Secret-store on + a missing secret → the secret-store pre-flight fires for replay too, no replay (AC6)."""
    monkeypatch.setattr(cli_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(cli_mod.settings, "runtime_enabled", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_secret_store_config", True)
    monkeypatch.setattr(cli_mod.settings, "runtime_credentials_proxy_enabled", False)
    monkeypatch.setattr(cli_mod.settings, "gemini_api_key", SecretStr(""))
    for var in ("GEMINI_API_KEY", "GEMINI_MODEL", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    calls = _patch_replay(monkeypatch, result=_ok_result())

    result = CliRunner().invoke(cli, ["replay", "kr-abc123", "--from", "cp"])

    assert result.exit_code != 0
    assert "RUNTIME_SECRET_STORE_CONFIG" in result.stderr
    assert calls == {"detect": 0, "replay": 0}
