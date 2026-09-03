"""The operator sync script: ``.env`` → the Environment Bucket ``decode-<env>`` (ADR-0015 §7, task 100).

Hermetic: ``kitaru.client`` is a fake module in ``sys.modules`` (``support.kitaru_secrets``), so no
workspace is ever reached — while the request DTOs are the SDK's real ones, so a payload these tests
accept is a payload the server would accept. The two properties every test here defends:

* **ONE full-surface write.** The write swaps the secret's whole ``values`` map (``create`` sets it,
  ``update``'s PATCH replaces it), so a partial push destroys the other keys — full-surface-or-nothing
  is the only safe write, and it is what makes the bucket an exact mirror of the file.
* **Key NAMES only ever reach the operator.** A planted sentinel value must not appear in stdout, stderr
  or any log record — on the happy path, the diff path, or the workspace-rejected path.
"""

from __future__ import annotations

import logging

import pytest
from click.testing import CliRunner
from support.kitaru_secrets import install_fake_kitaru_client

from scripts.sync_secrets import EXCLUDED_KEYS, main

SENTINEL = "sk-SENTINEL-VALUE-9f3a-never-print-me"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _install_bucket(monkeypatch, env: str, values: dict[str, str] | None = None, **kwargs):
    """Fake a workspace holding ``decode-<env>``; ``values=None`` means it does not exist yet."""
    secrets = None if values is None else {f"decode-{env}": values}
    return install_fake_kitaru_client(monkeypatch, secrets, **kwargs)


def _env_file(tmp_path, body: str) -> str:
    path = tmp_path / ".env"
    path.write_text(body)
    return str(path)


# --- The safety property: ONE write, carrying the WHOLE surface (replace semantics) ---


def test_pushes_the_whole_surface_in_exactly_one_write(tmp_path, monkeypatch, runner):
    """The write replaces the key set, so it must carry every syncable key in one call."""
    workspace = _install_bucket(monkeypatch, "staging", {"GEMINI_MODEL": "stale"})
    env_file = _env_file(tmp_path, f"GEMINI_API_KEY={SENTINEL}\nGEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    writes = [(method, target) for method, target in workspace.calls if method != "list"]
    assert [method for method, _ in writes] == [
        "get",
        "update",
    ]  # read for the diff, then ONE write
    assert workspace.values["decode-staging"] == {
        "GEMINI_API_KEY": SENTINEL,
        "GEMINI_MODEL": "gemini-2.5-pro",
    }


def test_a_missing_bucket_is_created_not_updated(tmp_path, monkeypatch, runner):
    workspace = _install_bucket(monkeypatch, "prod", None)
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "prod", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert "+ GEMINI_MODEL" in result.output  # a fresh bucket shows as all-added
    assert ("create", "decode-prod") in workspace.calls
    assert workspace.values["decode-prod"] == {"GEMINI_MODEL": "gemini-2.5-pro"}


def test_a_stale_key_absent_from_the_file_is_dropped_from_the_bucket(tmp_path, monkeypatch, runner):
    """Mirror, not merge: the bucket ends up with exactly the file's keys."""
    workspace = _install_bucket(monkeypatch, "dev", {"OPENROUTER_API_KEY": "sk-stale"})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "dev", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert workspace.values["decode-dev"] == {"GEMINI_MODEL": "gemini-2.5-pro"}


def test_an_empty_value_is_mirrored(tmp_path, monkeypatch, runner):
    workspace = _install_bucket(monkeypatch, "dev", {})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=\n")

    result = runner.invoke(main, ["--env", "dev", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert workspace.values["decode-dev"] == {"GEMINI_MODEL": ""}


def test_decode_env_is_never_pushed_into_the_bucket(tmp_path, monkeypatch, runner):
    """The bucket is NAMED by the environment; a DECODE_ENV key inside it could contradict the gate."""
    workspace = _install_bucket(monkeypatch, "staging", {})
    env_file = _env_file(tmp_path, "DECODE_ENV=staging\nGEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert "DECODE_ENV" in EXCLUDED_KEYS
    assert workspace.values["decode-staging"] == {"GEMINI_MODEL": "gemini-2.5-pro"}


def test_a_key_that_is_not_a_settings_field_is_skipped(tmp_path, monkeypatch, runner):
    """The surface is the ``Settings`` fields: the bucket source ignores anything else on the way in."""
    workspace = _install_bucket(monkeypatch, "prod", {})
    env_file = _env_file(tmp_path, f"MODAL_TOKEN_ID={SENTINEL}\nGEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "prod", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert workspace.values["decode-prod"] == {"GEMINI_MODEL": "gemini-2.5-pro"}
    assert "MODAL_TOKEN_ID" in result.output  # the skip is reported by NAME…
    assert SENTINEL not in result.output  # …never by value


def test_a_file_with_no_syncable_key_writes_nothing(tmp_path, monkeypatch, runner):
    """Refuse to replace a bucket with an empty key set — that would wipe it."""
    workspace = _install_bucket(monkeypatch, "dev", {"GEMINI_MODEL": "gemini-2.5-pro"})
    env_file = _env_file(tmp_path, "NOT_A_SETTINGS_FIELD=x\n")

    result = runner.invoke(main, ["--env", "dev", "--env-file", env_file, "--yes"])

    assert result.exit_code != 0
    assert workspace.calls == []  # not even a read


# --- Values never reach the operator: names only, on every path ---


def test_no_value_appears_in_the_output_or_the_logs(tmp_path, monkeypatch, runner, caplog):
    """The single most important property: a ``print(value)`` here would defeat the whole feature."""
    _install_bucket(
        monkeypatch,
        "staging",
        {"GEMINI_API_KEY": "sk-the-old-bucket-value", "OPENROUTER_API_KEY": "sk-gone"},
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


def test_a_rejected_write_redacts_values_out_of_the_reported_error(tmp_path, monkeypatch, runner):
    """A 422 echoes the offending input back — the error line is scrubbed before it prints."""
    _install_bucket(
        monkeypatch,
        "dev",
        {},
        error=RuntimeError(f"422: invalid value for GEMINI_API_KEY: {SENTINEL}"),
        error_on=frozenset({"create", "update"}),  # the read succeeds; the WRITE is rejected
    )
    env_file = _env_file(tmp_path, f"GEMINI_API_KEY={SENTINEL}\n")

    result = runner.invoke(main, ["--env", "dev", "--env-file", env_file, "--yes"])

    assert result.exit_code != 0
    assert SENTINEL not in result.output
    assert "GEMINI_API_KEY" in result.output  # the operator still gets the key name…
    assert "422" in result.output  # …and the diagnosis


def test_an_unreachable_workspace_fails_loudly_instead_of_claiming_a_create(
    tmp_path, monkeypatch, runner
):
    """A read failure must not masquerade as "the bucket does not exist yet" (that lies about the diff).

    The reported read error is redacted too — the file's values are the ones a re-run expects to
    find in the bucket, so they are exactly what a leaky error message would echo back.
    """
    _install_bucket(
        monkeypatch,
        "prod",
        {},
        error=RuntimeError(f"connection refused while sending GEMINI_API_KEY={SENTINEL}"),
    )
    env_file = _env_file(tmp_path, f"GEMINI_MODEL=gemini-2.5-pro\nGEMINI_API_KEY={SENTINEL}\n")

    result = runner.invoke(main, ["--env", "prod", "--env-file", env_file, "--yes"])

    assert result.exit_code != 0
    assert "decode-prod" in result.output
    assert "will be created" not in result.output
    assert SENTINEL not in result.output


# --- The diff: added / removed / changed / unchanged, by key name ---


def test_the_diff_classifies_added_removed_changed_and_unchanged(tmp_path, monkeypatch, runner):
    _install_bucket(
        monkeypatch,
        "staging",
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


# --- Confirmation: --yes is for CI; a decline writes NOTHING ---


def test_a_declined_confirmation_writes_nothing(tmp_path, monkeypatch, runner):
    workspace = _install_bucket(monkeypatch, "staging", {"GEMINI_MODEL": "gemini-2.5-flash"})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file], input="n\n")

    assert result.exit_code != 0
    assert workspace.values["decode-staging"] == {"GEMINI_MODEL": "gemini-2.5-flash"}  # untouched
    assert not [method for method, _ in workspace.calls if method in {"create", "update"}]


def test_an_accepted_confirmation_pushes(tmp_path, monkeypatch, runner):
    workspace = _install_bucket(monkeypatch, "staging", {})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file], input="y\n")

    assert result.exit_code == 0, result.output
    assert workspace.values["decode-staging"] == {"GEMINI_MODEL": "gemini-2.5-pro"}


def test_yes_skips_the_prompt(tmp_path, monkeypatch, runner):
    workspace = _install_bucket(monkeypatch, "staging", {})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    # No input supplied at all: a prompt would abort the run on EOF.
    result = runner.invoke(main, ["--env", "staging", "--env-file", env_file, "--yes"])

    assert result.exit_code == 0, result.output
    assert workspace.values["decode-staging"] == {"GEMINI_MODEL": "gemini-2.5-pro"}


# --- Friendly rejections: local has nothing to sync; a missing file is not a silent no-op ---


def test_env_local_is_rejected(tmp_path, monkeypatch, runner):
    """``local`` reads ``.env`` directly — there is nothing to mirror."""
    workspace = _install_bucket(monkeypatch, "local", {})
    env_file = _env_file(tmp_path, "GEMINI_MODEL=gemini-2.5-pro\n")

    result = runner.invoke(main, ["--env", "local", "--env-file", env_file, "--yes"])

    assert result.exit_code != 0
    assert "local" in result.output
    assert workspace.calls == []


def test_a_missing_env_file_is_a_friendly_error(tmp_path, monkeypatch, runner):
    workspace = _install_bucket(monkeypatch, "dev", {})

    result = runner.invoke(
        main, ["--env", "dev", "--env-file", str(tmp_path / "nope.env"), "--yes"]
    )

    assert result.exit_code != 0
    assert "nope.env" in result.output
    assert workspace.calls == []
