"""The operator sync script: ``.env`` → the Environment Bucket ``decode-<env>`` (ADR-0015 §7, task 100).

Hermetic: ``kitaru`` is a fake module in ``sys.modules`` and ``subprocess.run`` is monkeypatched, so no
secret store is ever touched. The two properties every test here defends:

* **ONE full-surface ``kitaru secrets set`` call.** ``kitaru secrets set`` REPLACES the whole key set
  (verified live), so a partial push destroys the other keys — full-surface-or-nothing is the only safe
  write, and it is what makes the bucket an exact mirror of the file.
* **Key NAMES only ever reach the operator.** A planted sentinel value must not appear in stdout, stderr
  or any log record — on the happy path, the diff path, or the kitaru-failed path.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import types

import pytest
from click.testing import CliRunner

from scripts.sync_secrets import EXCLUDED_KEYS, main

SENTINEL = "sk-SENTINEL-VALUE-9f3a-never-print-me"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def calls(monkeypatch) -> list[list[str]]:
    """Record every ``subprocess.run`` argv; return a success result (nothing is ever executed)."""
    recorded: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        recorded.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return recorded


def _install_fake_kitaru(monkeypatch, values: dict[str, str] | None = None) -> None:
    """Inject a fake ``kitaru``; ``values=None`` means the bucket does not exist yet (the create path)."""

    class _Secret:
        def __init__(self, vals: dict[str, str]) -> None:
            self.values = vals

    def _get_secret(name: str) -> _Secret:
        if values is None:
            raise RuntimeError(f"secret '{name}' does not exist")
        return _Secret(values)

    fake = types.ModuleType("kitaru")
    fake.get_secret = _get_secret  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kitaru", fake)


def _env_file(tmp_path, body: str) -> str:
    path = tmp_path / ".env"
    path.write_text(body)
    return str(path)


# --- The safety property: ONE call, carrying the WHOLE surface (replace semantics) ---


def test_pushes_the_whole_surface_in_exactly_one_kitaru_call(tmp_path, monkeypatch, runner, calls):
    """``kitaru secrets set`` replaces the key set, so the push must be one full-surface invocation."""
    _install_fake_kitaru(monkeypatch, {})
    env_file = _env_file(tmp_path, f"GEMINI_API_KEY={SENTINEL}\nGEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert calls == [
        [
            "kitaru",
            "secrets",
            "set",
            "decode-staging",
            "--private",
            f"--GEMINI_API_KEY={SENTINEL}",
            "--GEMINI_MODEL=gemini-2.5-pro",
        ]
    ]


def test_an_empty_value_is_mirrored(tmp_path, monkeypatch, runner, calls):
    _install_fake_kitaru(monkeypatch, {})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=\n")

    result = runner.invoke(main, ["--env", "dev", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert calls[0][-1] == "--GEMINI_MODEL="


def test_decode_env_is_never_pushed_into_the_bucket(tmp_path, monkeypatch, runner, calls):
    """The bucket is NAMED by the environment; a DECODE_ENV key inside it could contradict the gate."""
    _install_fake_kitaru(monkeypatch, {})
    env_file = _env_file(tmp_path, "DECODE_ENV=staging\nGEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert "DECODE_ENV" in EXCLUDED_KEYS
    assert calls[0] == [
        "kitaru",
        "secrets",
        "set",
        "decode-staging",
        "--private",
        "--GEMINI_MODEL=gemini-2.5-pro",
    ]


def test_a_key_that_is_not_a_settings_field_is_skipped(tmp_path, monkeypatch, runner, calls):
    """The surface is the ``Settings`` fields: the bucket source ignores anything else on the way in."""
    _install_fake_kitaru(monkeypatch, {})
    env_file = _env_file(tmp_path, f"MODAL_TOKEN_ID={SENTINEL}\nGEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "prod", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert calls[0] == [
        "kitaru",
        "secrets",
        "set",
        "decode-prod",
        "--private",
        "--GEMINI_MODEL=gemini-2.5-pro",
    ]
    assert "MODAL_TOKEN_ID" in result.output  # the skip is reported by NAME…
    assert SENTINEL not in result.output  # …never by value


def test_a_file_with_no_syncable_key_writes_nothing(tmp_path, monkeypatch, runner, calls):
    """Refuse to replace a bucket with an empty key set — that would wipe it."""
    _install_fake_kitaru(monkeypatch, {"GEMINI_MODEL": "gemini-2.5-pro"})
    env_file = _env_file(tmp_path, "NOT_A_SETTINGS_FIELD=x\n")

    result = runner.invoke(main, ["--env", "dev", "--env-file", env_file, "--yes"])

    assert result.exit_code != 0
    assert calls == []


# --- Values never reach the operator: names only, on every path ---


def test_no_value_appears_in_the_output_or_the_logs(tmp_path, monkeypatch, runner, calls, caplog):
    """The single most important property: a ``print(value)`` here would defeat the whole feature."""
    _install_fake_kitaru(
        monkeypatch, {"GEMINI_API_KEY": "sk-the-old-bucket-value", "OPENROUTER_API_KEY": "sk-gone"}
    )
    env_file = _env_file(
        tmp_path, f"GEMINI_API_KEY={SENTINEL}\nGEMINI_MODEL=gemini-2.5-pro\nLOG_LEVEL=INFO\n"
    )

    with caplog.at_level(logging.DEBUG):
        result = runner.invoke(main, ["--env", "staging", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert SENTINEL not in result.output
    assert SENTINEL not in caplog.text
    assert "sk-the-old-bucket-value" not in result.output  # nor the bucket's current values
    assert "sk-the-old-bucket-value" not in caplog.text


def test_a_failed_kitaru_call_redacts_values_out_of_the_reported_error(
    tmp_path, monkeypatch, runner
):
    """kitaru echoes the failing argv (which carries values) — the error line is scrubbed before it prints."""
    _install_fake_kitaru(monkeypatch, {})

    def _failing_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 2, stdout="", stderr=f"boom: --GEMINI_API_KEY={SENTINEL}"
        )

    monkeypatch.setattr(subprocess, "run", _failing_run)
    env_file = _env_file(tmp_path, f"GEMINI_API_KEY={SENTINEL}\n")

    result = runner.invoke(main, ["--env", "dev", "--env-file", env_file, "--yes"])

    assert result.exit_code != 0
    assert SENTINEL not in result.output
    assert "boom" in result.output  # the operator still gets the diagnosis…
    assert "GEMINI_API_KEY" in result.output  # …and the key name


# --- The diff: added / removed / changed / unchanged, by key name ---


def test_the_diff_classifies_added_removed_changed_and_unchanged(
    tmp_path, monkeypatch, runner, calls
):
    _install_fake_kitaru(
        monkeypatch,
        {
            "GEMINI_MODEL": "gemini-2.5-flash",  # changed (file says pro)
            "LOG_LEVEL": "INFO",  # unchanged
            "OPENROUTER_API_KEY": "sk-stale",  # removed (absent from the file)
        },
    )
    env_file = _env_file(
        tmp_path, f"GEMINI_MODEL=gemini-2.5-pro\nLOG_LEVEL=INFO\nGEMINI_API_KEY={SENTINEL}\n"
    )

    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert "+ GEMINI_API_KEY" in result.output
    assert "- OPENROUTER_API_KEY" in result.output
    assert "~ GEMINI_MODEL" in result.output
    assert "= LOG_LEVEL" in result.output
    assert SENTINEL not in result.output


def test_a_missing_bucket_is_the_create_path_not_an_error(tmp_path, monkeypatch, runner, calls):
    _install_fake_kitaru(monkeypatch, None)  # get_secret raises "does not exist"
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "prod", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert "+ GEMINI_MODEL" in result.output  # a fresh bucket shows as all-added
    assert len(calls) == 1


# --- Confirmation: --yes is for CI; a decline writes NOTHING ---


def test_a_declined_confirmation_writes_nothing(tmp_path, monkeypatch, runner, calls):
    _install_fake_kitaru(monkeypatch, {"GEMINI_MODEL": "gemini-2.5-flash"})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file], input="n\n")

    assert result.exit_code != 0
    assert calls == []  # nothing was written


def test_an_accepted_confirmation_pushes(tmp_path, monkeypatch, runner, calls):
    _install_fake_kitaru(monkeypatch, {})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file], input="y\n")

    assert result.exit_code == 0, result.output
    assert len(calls) == 1


def test_yes_skips_the_prompt(tmp_path, monkeypatch, runner, calls):
    _install_fake_kitaru(monkeypatch, {})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    # No input supplied at all: a prompt would abort the run on EOF.
    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1


# --- Friendly rejections: local has nothing to sync; a missing file is not a silent no-op ---


def test_env_local_is_rejected(tmp_path, monkeypatch, runner, calls):
    """``local`` reads ``.env`` directly — there is nothing to mirror."""
    _install_fake_kitaru(monkeypatch, {})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "local", "--env-file", env_file, "--yes"])

    assert result.exit_code != 0
    assert "local" in result.output
    assert calls == []


def test_a_missing_env_file_is_a_friendly_error(tmp_path, monkeypatch, runner, calls):
    _install_fake_kitaru(monkeypatch, {})

    result = runner.invoke(
        main, ["--env", "dev", "--env-file", str(tmp_path / "nope.env"), "--yes"]
    )

    assert result.exit_code != 0
    assert "nope.env" in result.output
    assert calls == []
