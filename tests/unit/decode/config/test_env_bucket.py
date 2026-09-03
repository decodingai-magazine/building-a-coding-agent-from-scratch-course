"""The ``DECODE_ENV`` gate + the Environment Bucket settings source (ADR-0015 §1-3, §5, task 097).

``DECODE_ENV`` selects the injection mechanism and nothing else: ``.env`` at ``local`` (the default),
the derived Kitaru bucket ``decode-<env>`` at every remote env — where **dotenv is dropped from the
source chain entirely**, so a key missing from the bucket fails loudly instead of being backfilled
from a developer's file.

Hermetic: ``kitaru.client`` is a fake module injected into ``sys.modules`` (no workspace, no
credentials, no network — see ``support.kitaru_secrets``); the ``local``-never-imports-kitaru
invariant is proven in a fresh subprocess so it is independent of what the rest of the suite
imported.
"""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError
from support.kitaru_secrets import install_fake_kitaru_client

from decode.config.settings import (
    EnvironmentBucketSettingsSource,
    Settings,
    bucket_load_error,
    environment_bucket_name,
)

# NOT ``import decode.config.settings as settings_mod``: ``decode/config/__init__.py`` re-exports the
# ``settings`` SINGLETON, which shadows the submodule of the same name on the package — the ``as``
# form resolves the attribute, so that spelling silently binds a ``Settings`` INSTANCE and every
# ``monkeypatch.setattr`` below would land on the model instead of the module global.
settings_mod = importlib.import_module("decode.config.settings")

# Cleared in every build below so a developer's real environment cannot leak into the assertions.
_CLEARED_ENV_VARS = (
    "DECODE_ENV",
    "LLM_PROVIDER",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPIK_PROJECT_NAME",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Clear the gate + provider vars and reset the module-level bucket-failure slot."""
    for var in _CLEARED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings_mod, "_bucket_load_error", None, raising=False)


def _install_bucket(
    monkeypatch,
    env: str,
    values: dict[str, str] | None = None,
    *,
    error: Exception | None = None,
):
    """Fake a Kitaru workspace holding the bucket ``decode-<env>``; return the recording workspace.

    ``values=None`` means the workspace has NO such secret (the missing-bucket path); ``error``
    makes every API call raise (an unreachable workspace / an expired login), so the
    no-crash-at-import failure capture can be exercised without touching a real workspace.
    """
    secrets = None if values is None else {environment_bucket_name(env): values}
    return install_fake_kitaru_client(monkeypatch, secrets, error=error)


# --- The gate itself: DECODE_ENV defaults to local and is a closed Literal (ADR-0015 §1) ---


def test_decode_env_defaults_to_local():
    s = Settings(_env_file=None)

    assert s.decode_env == "local"


@pytest.mark.parametrize("env", ["local", "dev", "staging", "prod"])
def test_decode_env_accepts_each_valid_literal(monkeypatch, env):
    _install_bucket(monkeypatch, env, {})
    monkeypatch.setenv("DECODE_ENV", env)

    s = Settings(_env_file=None)

    assert s.decode_env == env


def test_decode_env_rejects_unknown_value(monkeypatch):
    """A new environment is a code change — the Literal is closed (ADR-0015, Non-goals)."""
    monkeypatch.setenv("DECODE_ENV", "qa")

    with pytest.raises(ValidationError, match="decode_env"):
        Settings(_env_file=None)


# --- Remote: the bucket hydrates the whole surface, derived name, env still wins (§2-3) ---


def test_bucket_hydrates_known_fields_and_ignores_unknown_keys(monkeypatch):
    _install_bucket(
        monkeypatch,
        "staging",
        {
            "LLM_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "sk-from-the-bucket",
            "GEMINI_MODEL": "gemini-from-the-bucket",
            "NOT_A_DECODE_FIELD": "ignored",
        },
    )
    monkeypatch.setenv("DECODE_ENV", "staging")

    s = Settings(_env_file=None)

    assert s.llm_provider == "openrouter"
    assert s.openrouter_api_key.get_secret_value() == "sk-from-the-bucket"
    assert s.gemini_model == "gemini-from-the-bucket"
    assert not hasattr(s, "not_a_decode_field")


def test_bucket_name_is_derived_from_decode_env(monkeypatch):
    """No override knob: ``DECODE_ENV=dev`` reads ``decode-dev``, always (ADR-0015 §3)."""
    workspace = _install_bucket(monkeypatch, "dev", {"GEMINI_MODEL": "m"})
    monkeypatch.setenv("DECODE_ENV", "dev")

    Settings(_env_file=None)

    assert workspace.requested_names == ["decode-dev"]
    assert environment_bucket_name("prod") == "decode-prod"


def test_process_env_overrides_a_bucket_value(monkeypatch):
    """Precedence at remote: process env > bucket > defaults."""
    _install_bucket(monkeypatch, "prod", {"GEMINI_MODEL": "gemini-from-the-bucket"})
    monkeypatch.setenv("DECODE_ENV", "prod")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-from-the-process-env")

    s = Settings(_env_file=None)

    assert s.gemini_model == "gemini-from-the-process-env"


def test_bucket_values_never_touch_os_environ(monkeypatch):
    """The invariant that keeps a model-chosen ``bash`` from inheriting a credential (ADR-0015 §2)."""
    _install_bucket(monkeypatch, "staging", {"GEMINI_API_KEY": "sk-never-in-the-process-env"})
    monkeypatch.setenv("DECODE_ENV", "staging")
    env_before = dict(os.environ)

    s = Settings(_env_file=None)

    assert s.gemini_api_key.get_secret_value() == "sk-never-in-the-process-env"
    assert dict(os.environ) == env_before
    assert "sk-never-in-the-process-env" not in os.environ.values()


def test_bucket_hydration_logs_field_names_not_values(monkeypatch, caplog):
    sentinel = "SENTINEL-BUCKET-VALUE-9f3a"
    _install_bucket(monkeypatch, "dev", {"GEMINI_API_KEY": sentinel})
    monkeypatch.setenv("DECODE_ENV", "dev")

    with caplog.at_level(logging.DEBUG, logger="decode.config.settings"):
        Settings(_env_file=None)

    assert sentinel not in caplog.text  # never the value
    assert "gemini_api_key" in caplog.text  # the field NAME is logged


# --- Remote: .env is DROPPED from the chain — the loud-failure property (§2) ---


def test_dotenv_is_dropped_at_a_remote_env(tmp_path, monkeypatch):
    """A key present ONLY in ``.env`` does not reach ``Settings`` at a remote env."""
    _install_bucket(monkeypatch, "staging", {"GEMINI_API_KEY": "sk-from-the-bucket"})
    monkeypatch.setenv("DECODE_ENV", "staging")
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_MODEL=gemini-from-dotenv\n")

    s = Settings(_env_file=str(env_file))

    assert s.gemini_model == "gemini-3.5-flash"  # the field default, NOT the dotenv value
    assert s.gemini_api_key.get_secret_value() == "sk-from-the-bucket"


def test_a_key_missing_from_the_bucket_is_not_backfilled_from_dotenv(tmp_path, monkeypatch):
    """The point of having environments: a provisioning gap fails loudly, not silently (ADR-0015 §2).

    The bucket carries no ``GEMINI_API_KEY`` while the developer's ``.env`` does. The key must NOT be
    backfilled — the empty key trips the cli's provider guard, which is exactly the loud failure.
    """
    _install_bucket(monkeypatch, "prod", {"LLM_PROVIDER": "gemini"})
    monkeypatch.setenv("DECODE_ENV", "prod")
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=sk-only-in-the-developers-dotenv\n")

    s = Settings(_env_file=str(env_file))

    assert s.gemini_api_key.get_secret_value() == ""  # no silent backfill


# --- Bootstrap: DECODE_ENV is resolved out-of-band, and fed back onto the field (§1) ---


def test_decode_env_in_the_dotenv_file_activates_the_bucket(tmp_path, monkeypatch):
    """The gate is read out-of-band from the dotenv file — even though dotenv is dropped at remote."""
    workspace = _install_bucket(monkeypatch, "staging", {"GEMINI_MODEL": "gemini-from-the-bucket"})
    env_file = tmp_path / ".env"
    env_file.write_text("DECODE_ENV=staging\nGEMINI_MODEL=gemini-from-dotenv\n")

    s = Settings(_env_file=str(env_file))

    assert workspace.requested_names == ["decode-staging"]
    assert s.gemini_model == "gemini-from-the-bucket"
    # Feedback: the value that opened the gate is the value on the object (they can never diverge).
    assert s.decode_env == "staging"


def test_process_env_decode_env_beats_the_dotenv_file(tmp_path, monkeypatch):
    """Process env wins, consistent with every other setting (ADR-0015 §1)."""
    workspace = _install_bucket(monkeypatch, "dev", {})
    monkeypatch.setenv("DECODE_ENV", "dev")
    env_file = tmp_path / ".env"
    env_file.write_text("DECODE_ENV=prod\n")

    s = Settings(_env_file=str(env_file))

    assert workspace.requested_names == ["decode-dev"]
    assert s.decode_env == "dev"


def test_a_bucket_supplied_decode_env_cannot_override_the_resolved_gate(monkeypatch):
    """The gate decides whether the bucket is read, so the bucket can never restate it (§1)."""
    _install_bucket(monkeypatch, "dev", {"DECODE_ENV": "prod"})
    monkeypatch.setenv("DECODE_ENV", "dev")

    s = Settings(_env_file=None)

    assert s.decode_env == "dev"  # the gate that was actually applied


# --- Opik projects follow the environment: opik_project_name defaults to decode-<env> (§8) ---


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_opik_project_name_is_derived_from_a_remote_decode_env(monkeypatch, env):
    """A trace names the environment that produced it: ``DECODE_ENV=prod`` → project ``decode-prod``."""
    _install_bucket(monkeypatch, env, {})
    monkeypatch.setenv("DECODE_ENV", env)

    s = Settings(_env_file=None)

    assert s.opik_project_name == f"decode-{env}"


def test_a_bucket_supplied_opik_project_name_wins_over_the_derived_default(monkeypatch):
    """Explicit wins from the BUCKET too — it is a settings source like any other (ADR-0015 §8)."""
    _install_bucket(monkeypatch, "staging", {"OPIK_PROJECT_NAME": "proj-from-the-bucket"})
    monkeypatch.setenv("DECODE_ENV", "staging")

    s = Settings(_env_file=None)

    assert s.opik_project_name == "proj-from-the-bucket"
    assert (
        "opik_project_name" in s.model_fields_set
    )  # source-supplied → explicit, never derived over


def test_a_process_env_opik_project_name_wins_at_a_remote_env(monkeypatch):
    _install_bucket(monkeypatch, "prod", {"OPIK_PROJECT_NAME": "proj-from-the-bucket"})
    monkeypatch.setenv("DECODE_ENV", "prod")
    monkeypatch.setenv("OPIK_PROJECT_NAME", "proj-from-the-process-env")

    s = Settings(_env_file=None)

    assert s.opik_project_name == "proj-from-the-process-env"


def test_an_explicit_project_name_matching_the_declared_default_survives_a_remote_env(monkeypatch):
    """Anti-sentinel, the discriminating case: explicit ``decode-local`` at ``DECODE_ENV=dev``.

    A sentinel/value comparison against the declared default would see ``decode-local``, call it
    "unset", and overwrite it with ``decode-dev`` — silently ignoring an operator's explicit choice.
    ``model_fields_set`` cannot make that mistake: the bucket supplied the field, so it stands.
    """
    _install_bucket(monkeypatch, "dev", {"OPIK_PROJECT_NAME": "decode-local"})
    monkeypatch.setenv("DECODE_ENV", "dev")

    s = Settings(_env_file=None)

    assert s.opik_project_name == "decode-local"  # NOT decode-dev


# --- Failure capture: the singleton is built at import, so the source must never raise (§5) ---


def test_an_unreachable_workspace_is_captured_not_raised(monkeypatch):
    """An unreachable / unauthenticated Kitaru workspace must not crash the settings import."""
    workspace = _install_bucket(
        monkeypatch, "staging", {}, error=RuntimeError("401: the API key has expired")
    )
    monkeypatch.setenv("DECODE_ENV", "staging")

    s = Settings(_env_file=None)  # must NOT raise

    assert s.decode_env == "staging"  # the gate still made it onto the object
    assert s.gemini_api_key.get_secret_value() == ""  # nothing hydrated
    error = bucket_load_error()
    assert error is not None
    assert "decode-staging" in error
    assert workspace.closed == 1  # …and the http client is not leaked on the failure path


def test_a_bucket_absent_from_the_workspace_is_captured_not_raised(monkeypatch):
    """The provisioning gap ``make sync-secrets`` fixes: no such named secret, no traceback (§5)."""
    workspace = _install_bucket(monkeypatch, "staging", None)  # the workspace holds no secret
    monkeypatch.setenv("DECODE_ENV", "staging")

    s = Settings(_env_file=None)  # must NOT raise

    assert workspace.requested_names == ["decode-staging"]  # it was looked for by name…
    assert s.gemini_api_key.get_secret_value() == ""  # …and nothing was hydrated
    error = bucket_load_error()
    assert error is not None
    assert "decode-staging" in error


def test_a_successful_read_lists_by_name_then_gets_the_values(monkeypatch):
    """The two-step the 0.22.2 secrets resource forces: no get-by-name endpoint exists (ADR-0019 §5)."""
    workspace = _install_bucket(monkeypatch, "dev", {"GEMINI_MODEL": "m"})
    monkeypatch.setenv("DECODE_ENV", "dev")

    Settings(_env_file=None)

    methods = [method for method, _ in workspace.calls]
    assert methods == ["list", "get"]  # never a create/update — the source is read-only
    assert workspace.opened == 1
    assert workspace.closed == 1  # the client is closed even on the happy path


def test_bucket_load_error_is_none_after_a_successful_load(monkeypatch):
    _install_bucket(monkeypatch, "dev", {"GEMINI_MODEL": "m"})
    monkeypatch.setenv("DECODE_ENV", "dev")

    Settings(_env_file=None)

    assert bucket_load_error() is None


def test_bucket_load_error_is_none_at_local():
    Settings(_env_file=None)

    assert bucket_load_error() is None


# --- local: byte-identical to before the cutover — dotenv works, kitaru is never imported (§5) ---


def test_local_still_loads_from_the_dotenv_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=sk-from-dotenv\nGEMINI_MODEL=gemini-2.5-pro\n")

    s = Settings(_env_file=str(env_file))

    assert s.decode_env == "local"
    assert s.gemini_api_key.get_secret_value() == "sk-from-dotenv"
    assert s.gemini_model == "gemini-2.5-pro"


def test_the_bucket_source_is_never_built_at_local(monkeypatch):
    """At ``local`` the source is absent from the chain entirely — it cannot even reach kitaru."""
    workspace = _install_bucket(monkeypatch, "local", {"GEMINI_MODEL": "gemini-from-the-bucket"})

    s = Settings(_env_file=None)

    assert workspace.calls == []  # the kitaru client was never even constructed
    assert s.gemini_model == "gemini-3.5-flash"


def test_the_source_is_inert_when_constructed_at_local(monkeypatch):
    """Belt-and-braces: even hand-built at ``local``, the source hydrates nothing and imports nothing."""
    workspace = _install_bucket(monkeypatch, "local", {"GEMINI_MODEL": "gemini-from-the-bucket"})

    source = EnvironmentBucketSettingsSource(Settings, "local")

    assert source() == {"decode_env": "local"}
    assert workspace.calls == []


def test_at_decode_env_local_decode_never_imports_kitaru(tmp_path):
    """The restated invariant (ADR-0015 §5): at ``DECODE_ENV=local`` (the default), no kitaru.

    Run in a clean subprocess so it is independent of what the rest of the suite already imported,
    from a ``tmp_path`` cwd (no repo ``.env``) with ``DECODE_ENV`` scrubbed from the child env — the
    default gate, the local chain, no bucket source, no kitaru anywhere in ``sys.modules``.
    """
    code = (
        "import sys\n"
        "import decode.cli\n"  # the REPL entrypoint module
        "from decode.config.settings import Settings\n"
        "Settings(_env_file=None)\n"  # ...and a settings build on the local chain
        "leaked = sorted(m for m in sys.modules if m == 'kitaru' or m.startswith('kitaru.'))\n"
        "assert not leaked, leaked\n"
        "print('NO_KITARU_OK')\n"
    )
    child_env = {k: v for k, v in os.environ.items() if k != "DECODE_ENV"}

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=child_env,
    )

    assert result.returncode == 0, result.stderr
    assert "NO_KITARU_OK" in result.stdout
