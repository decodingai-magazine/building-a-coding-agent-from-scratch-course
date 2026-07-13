from pathlib import Path

import pytest
from pydantic import ValidationError

from decode.config import settings as singleton
from decode.config.settings import Settings

# Each *_ENV_VARS tuple below is cleared in default/.env tests so a developer's real
# environment cannot leak into the assertions.

# Provider vars (ADR-0005).
_PROVIDER_ENV_VARS = (
    "LLM_PROVIDER",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "MODAL_ENDPOINT_URL",
    "MODAL_ENDPOINT_MODEL",
    "MODAL_PROXY_TOKEN_ID",
    "MODAL_PROXY_TOKEN_SECRET",
)

# Context compaction vars (ADR-0006).
_COMPACTION_ENV_VARS = (
    "COMPACTION_ENABLED",
    "COMPACTION_CONTEXT_WINDOW_TOKENS",
    "COMPACTION_RESERVE_FRACTION",
    "MICROCOMPACTION_RESERVE_FRACTION",
    "COMPACTION_KEEP_RECENT_TOKENS",
    "MEMORY_COMPRESSION_ENABLED",
)

# LSP / code intelligence vars (ADR-0007).
_LSP_ENV_VARS = (
    "LSP_ENABLED",
    "LSP_SERVER_COMMAND",
    "LSP_SERVER_ARGS",
    "LSP_DIAGNOSTICS_ON_EDIT",
    "LSP_REQUEST_TIMEOUT_S",
)

# Kitaru durable runtime vars (ADR-0008). The secret knobs are gone — config comes from DECODE_ENV
# (ADR-0015 §4); the gate + the Environment Bucket have their own file (test_env_bucket.py).
_RUNTIME_ENV_VARS = (
    "RUNTIME_ENABLED",
    "RUNTIME_CHECKPOINT_STRATEGY",
    "RUNTIME_WAIT_TIMEOUT_S",
)

# Sandboxing vars (ADR-0011). The Credential-Proxy knobs are gone with the proxy (ADR-0016 §1) —
# a clean break: a retired key left in a ``.env`` is simply ignored (``extra="ignore"``).
_SANDBOX_ENV_VARS = (
    "SANDBOX_MODE",
    "SANDBOX_IMAGE",
    "SANDBOX_TIMEOUT_S",
)

# Subagent tuning vars (ADR-0013).
_SUBAGENT_ENV_VARS = (
    "SUBAGENT_MAX_PARALLEL",
    "SUBAGENT_MAX_REQUESTS",
    "SUBAGENT_RESULT_MAX_BYTES",
)

# Observability: Opik vars (ADR-0014).
_OPIK_ENV_VARS = (
    "OPIK_API_KEY",
    "OPIK_WORKSPACE",
    "OPIK_PROJECT_NAME",
    "OPIK_URL_OVERRIDE",
)


def test_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    s = Settings(_env_file=None)
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.max_output_lines == 2000
    assert s.max_output_bytes == 50_000
    assert s.memory_max_lines == 200
    assert s.memory_max_bytes == 25_000
    assert s.decode_dir == Path(".decode")
    assert s.sessions_dir == Path(".decode/sessions")
    assert s.permissions_file == Path(".decode/settings.json")
    assert s.gemini_api_key.get_secret_value() == ""


def test_reads_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret123")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    s = Settings(_env_file=None)
    assert s.gemini_api_key.get_secret_value() == "secret123"
    assert s.gemini_model == "gemini-2.5-pro"


def test_loads_values_from_a_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=sk-from-dotenv\nGEMINI_MODEL=gemini-2.5-pro\n")
    s = Settings(_env_file=str(env))
    assert s.gemini_api_key.get_secret_value() == "sk-from-dotenv"
    assert s.gemini_model == "gemini-2.5-pro"


def test_process_env_var_overrides_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=sk-from-dotenv\n")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-from-process-env")
    s = Settings(_env_file=str(env))
    assert s.gemini_api_key.get_secret_value() == "sk-from-process-env"


def test_secret_not_in_repr():
    s = Settings(_env_file=None, gemini_api_key="topsecret")
    assert "topsecret" not in repr(s)


def test_module_singleton_is_settings_instance():
    assert isinstance(singleton, Settings)


def test_provider_defaults(monkeypatch):
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.llm_provider == "gemini"
    assert s.openrouter_model == "openrouter/free"
    assert s.modal_endpoint_model == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert s.modal_endpoint_url == ""
    assert s.openrouter_api_key.get_secret_value() == ""
    assert s.modal_proxy_token_id.get_secret_value() == ""
    assert s.modal_proxy_token_secret.get_secret_value() == ""


@pytest.mark.parametrize("provider", ["gemini", "openrouter", "modal"])
def test_llm_provider_accepts_each_valid_literal(monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    s = Settings(_env_file=None)
    assert s.llm_provider == provider


def test_llm_provider_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_reads_provider_vars_from_process_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-123")
    monkeypatch.setenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    monkeypatch.setenv("MODAL_ENDPOINT_URL", "https://example.modal.run")
    monkeypatch.setenv("MODAL_ENDPOINT_MODEL", "zai-org/GLM-5.2-FP8")
    monkeypatch.setenv("MODAL_PROXY_TOKEN_ID", "wk-abc")
    monkeypatch.setenv("MODAL_PROXY_TOKEN_SECRET", "ws-xyz")
    s = Settings(_env_file=None)
    assert s.llm_provider == "openrouter"
    assert s.openrouter_api_key.get_secret_value() == "sk-or-123"
    assert s.openrouter_model == "meta-llama/llama-3.3-70b-instruct:free"
    assert s.modal_endpoint_url == "https://example.modal.run"
    assert s.modal_endpoint_model == "zai-org/GLM-5.2-FP8"
    assert s.modal_proxy_token_id.get_secret_value() == "wk-abc"
    assert s.modal_proxy_token_secret.get_secret_value() == "ws-xyz"


def test_loads_provider_vars_from_a_dotenv_file(tmp_path, monkeypatch):
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "LLM_PROVIDER=modal\n"
        "OPENROUTER_API_KEY=sk-or-from-dotenv\n"
        "OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free\n"
        "MODAL_ENDPOINT_URL=https://dotenv.modal.run\n"
        "MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8\n"
        "MODAL_PROXY_TOKEN_ID=wk-dotenv\n"
        "MODAL_PROXY_TOKEN_SECRET=ws-dotenv\n"
    )
    s = Settings(_env_file=str(env))
    assert s.llm_provider == "modal"
    assert s.openrouter_api_key.get_secret_value() == "sk-or-from-dotenv"
    assert s.openrouter_model == "meta-llama/llama-3.3-70b-instruct:free"
    assert s.modal_endpoint_url == "https://dotenv.modal.run"
    assert s.modal_endpoint_model == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert s.modal_proxy_token_id.get_secret_value() == "wk-dotenv"
    assert s.modal_proxy_token_secret.get_secret_value() == "ws-dotenv"


def test_provider_secrets_not_in_repr():
    s = Settings(
        _env_file=None,
        openrouter_api_key="or-topsecret",
        modal_proxy_token_id="wk-topsecret",
        modal_proxy_token_secret="ws-topsecret",
    )
    text = repr(s)
    assert "or-topsecret" not in text
    assert "wk-topsecret" not in text
    assert "ws-topsecret" not in text


def test_compaction_defaults(monkeypatch):
    for var in _COMPACTION_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.compaction_enabled is True
    assert s.compaction_context_window_tokens == 1_048_576
    assert s.compaction_reserve_fraction == 0.20
    assert s.microcompaction_reserve_fraction == 0.40
    assert s.compaction_keep_recent_tokens == 20_000
    assert s.memory_compression_enabled is True


def test_microcompaction_reserves_more_than_full_on_defaults(monkeypatch):
    """Invariant (ADR-0006 §3): micro reserves more → fires first."""
    for var in _COMPACTION_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.microcompaction_reserve_fraction > s.compaction_reserve_fraction


def test_reads_compaction_vars_from_process_env(monkeypatch):
    monkeypatch.setenv("COMPACTION_ENABLED", "false")
    monkeypatch.setenv("COMPACTION_CONTEXT_WINDOW_TOKENS", "200000")
    monkeypatch.setenv("COMPACTION_RESERVE_FRACTION", "0.15")
    monkeypatch.setenv("MICROCOMPACTION_RESERVE_FRACTION", "0.35")
    monkeypatch.setenv("COMPACTION_KEEP_RECENT_TOKENS", "12345")
    monkeypatch.setenv("MEMORY_COMPRESSION_ENABLED", "false")
    s = Settings(_env_file=None)
    assert s.compaction_enabled is False
    assert s.compaction_context_window_tokens == 200_000
    assert s.compaction_reserve_fraction == 0.15
    assert s.microcompaction_reserve_fraction == 0.35
    assert s.compaction_keep_recent_tokens == 12_345
    assert s.memory_compression_enabled is False


def test_loads_compaction_vars_from_a_dotenv_file(tmp_path, monkeypatch):
    for var in _COMPACTION_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "COMPACTION_ENABLED=false\n"
        "COMPACTION_CONTEXT_WINDOW_TOKENS=200000\n"
        "COMPACTION_RESERVE_FRACTION=0.10\n"
        "MICROCOMPACTION_RESERVE_FRACTION=0.30\n"
        "COMPACTION_KEEP_RECENT_TOKENS=9999\n"
        "MEMORY_COMPRESSION_ENABLED=false\n"
    )
    s = Settings(_env_file=str(env))
    assert s.compaction_enabled is False
    assert s.compaction_context_window_tokens == 200_000
    assert s.compaction_reserve_fraction == 0.10
    assert s.microcompaction_reserve_fraction == 0.30
    assert s.compaction_keep_recent_tokens == 9999
    assert s.memory_compression_enabled is False


def test_rejects_a_non_positive_context_window(monkeypatch):
    """A window <= 0 fails fast at load, not deep in the trigger (Field(gt=0))."""
    for var in _COMPACTION_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("COMPACTION_CONTEXT_WINDOW_TOKENS", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_rejects_a_reserve_fraction_outside_the_unit_interval(monkeypatch):
    """Reserve fractions must be in [0, 1] (Field(ge=0, le=1)) — a bad value is rejected at load."""
    for var in _COMPACTION_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("COMPACTION_RESERVE_FRACTION", "1.5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.delenv("COMPACTION_RESERVE_FRACTION", raising=False)
    monkeypatch.setenv("MICROCOMPACTION_RESERVE_FRACTION", "-0.1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_lsp_defaults(monkeypatch):
    for var in _LSP_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.lsp_enabled is True
    assert s.lsp_server_command == "ty"
    assert s.lsp_server_args == ["server"]
    assert s.lsp_diagnostics_on_edit is True
    assert s.lsp_request_timeout_s == 10.0


def test_reads_lsp_vars_from_process_env(monkeypatch):
    monkeypatch.setenv("LSP_ENABLED", "false")
    monkeypatch.setenv("LSP_SERVER_COMMAND", "pylsp")
    monkeypatch.setenv("LSP_SERVER_ARGS", '["-v"]')
    monkeypatch.setenv("LSP_DIAGNOSTICS_ON_EDIT", "false")
    monkeypatch.setenv("LSP_REQUEST_TIMEOUT_S", "2.5")
    s = Settings(_env_file=None)
    assert s.lsp_enabled is False
    assert s.lsp_server_command == "pylsp"
    assert s.lsp_server_args == ["-v"]
    assert s.lsp_diagnostics_on_edit is False
    assert s.lsp_request_timeout_s == 2.5


def test_loads_lsp_vars_from_a_dotenv_file(tmp_path, monkeypatch):
    for var in _LSP_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "LSP_ENABLED=false\n"
        "LSP_SERVER_COMMAND=pylsp\n"
        'LSP_SERVER_ARGS=["--check-parent-process"]\n'
        "LSP_DIAGNOSTICS_ON_EDIT=false\n"
        "LSP_REQUEST_TIMEOUT_S=5.0\n"
    )
    s = Settings(_env_file=str(env))
    assert s.lsp_enabled is False
    assert s.lsp_server_command == "pylsp"
    assert s.lsp_server_args == ["--check-parent-process"]
    assert s.lsp_diagnostics_on_edit is False
    assert s.lsp_request_timeout_s == 5.0


@pytest.mark.parametrize("bad", ["0", "-1.0"])
def test_rejects_a_non_positive_lsp_request_timeout(monkeypatch, bad):
    """A timeout <= 0 fails fast at load, not deep in a request (Field(gt=0))."""
    for var in _LSP_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LSP_REQUEST_TIMEOUT_S", bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_runtime_defaults(monkeypatch):
    for var in _RUNTIME_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.runtime_enabled is True
    # Default is "calls": every run is replay-ready (per-call checkpoints), loop-safe on gemini, the
    # wired provider (ADR-0010 §3). "turn" is the cheaper coarse opt-out (asserted in the literal test).
    assert s.runtime_checkpoint_strategy == "calls"
    assert s.runtime_wait_timeout_s == 600.0


def test_stale_secret_store_env_vars_are_silently_ignored(monkeypatch):
    """ADR-0015 §4 (clean break): the retired ``RUNTIME_SECRET_*`` family is deleted, not shimmed.

    An env / ``.env`` still carrying one of the retired knobs must change nothing and print nothing —
    ``extra="ignore"`` swallows it, and the fields are gone, so no reader can branch on them. Config
    now comes from ``DECODE_ENV``: ``.env`` at ``local``, the Environment Bucket at a remote env (the
    stale names are spelled out only in ``.env.example``, which is where the loud notice lives).
    """
    for stale in ("_STORE_MODEL_KEY", "_STORE_CONFIG", "_NAME"):
        monkeypatch.setenv(f"RUNTIME_SECRET{stale}", "true")

    s = Settings(_env_file=None)

    assert [f for f in Settings.model_fields if f.startswith("runtime_secret")] == []
    assert s.decode_env == "local"  # the surviving selector is untouched by the stale entries


def test_reads_runtime_vars_from_process_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_ENABLED", "false")
    monkeypatch.setenv("RUNTIME_CHECKPOINT_STRATEGY", "calls")
    monkeypatch.setenv("RUNTIME_WAIT_TIMEOUT_S", "120.0")
    s = Settings(_env_file=None)
    assert s.runtime_enabled is False
    assert s.runtime_checkpoint_strategy == "calls"
    assert s.runtime_wait_timeout_s == 120.0


def test_loads_runtime_vars_from_a_dotenv_file(tmp_path, monkeypatch):
    for var in _RUNTIME_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "RUNTIME_ENABLED=false\nRUNTIME_CHECKPOINT_STRATEGY=calls\nRUNTIME_WAIT_TIMEOUT_S=300.0\n"
    )
    s = Settings(_env_file=str(env))
    assert s.runtime_enabled is False
    assert s.runtime_checkpoint_strategy == "calls"
    assert s.runtime_wait_timeout_s == 300.0


@pytest.mark.parametrize("strategy", ["turn", "calls"])
def test_runtime_checkpoint_strategy_accepts_each_valid_literal(monkeypatch, strategy):
    monkeypatch.setenv("RUNTIME_CHECKPOINT_STRATEGY", strategy)
    s = Settings(_env_file=None)
    assert s.runtime_checkpoint_strategy == strategy


def test_runtime_checkpoint_strategy_rejects_unknown_value(monkeypatch):
    """A value outside {"turn", "calls"} fails fast at load, not inside the flow (Literal)."""
    for var in _RUNTIME_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RUNTIME_CHECKPOINT_STRATEGY", "hourly")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("bad", ["0", "-1.0"])
def test_rejects_a_non_positive_runtime_wait_timeout(monkeypatch, bad):
    """A wait timeout <= 0 fails fast at load, not deep in the durable wait (Field(gt=0))."""
    for var in _RUNTIME_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RUNTIME_WAIT_TIMEOUT_S", bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# Sandboxing — config surface only; the default ``sandbox_mode="none"`` means no sandbox var is
# required to build ``Settings``.


def test_sandbox_defaults(monkeypatch):
    for var in _SANDBOX_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.sandbox_mode == "none"
    assert s.sandbox_image == "ghcr.io/astral-sh/uv:python3.12-bookworm-slim"
    assert s.sandbox_timeout_s == 600.0


def test_reads_sandbox_vars_from_process_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    monkeypatch.setenv("SANDBOX_IMAGE", "python:3.13-slim")
    monkeypatch.setenv("SANDBOX_TIMEOUT_S", "120.0")
    s = Settings(_env_file=None)
    assert s.sandbox_mode == "docker"
    assert s.sandbox_image == "python:3.13-slim"
    assert s.sandbox_timeout_s == 120.0


def test_loads_sandbox_vars_from_a_dotenv_file(tmp_path, monkeypatch):
    for var in _SANDBOX_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text("SANDBOX_MODE=modal\nSANDBOX_IMAGE=python:3.11-slim\nSANDBOX_TIMEOUT_S=300.0\n")
    s = Settings(_env_file=str(env))
    assert s.sandbox_mode == "modal"
    assert s.sandbox_image == "python:3.11-slim"
    assert s.sandbox_timeout_s == 300.0


def test_a_retired_credential_proxy_key_in_a_dotenv_is_ignored(tmp_path, monkeypatch):
    """ADR-0016 §1 (clean break): a stale ``SANDBOX_CREDENTIAL_PROXY_ENABLED`` must not blow up.

    The knobs are deleted with no shim, so an old ``.env`` carrying them still loads — ``extra="ignore"``
    swallows the retired key exactly as ADR-0015 §9 handled its own — and the field is simply gone.
    """
    for var in _SANDBOX_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "SANDBOX_MODE=docker\n"
        "SANDBOX_CREDENTIAL_PROXY_ENABLED=true\n"
        "SANDBOX_PROXY_IMAGE=mitmproxy/mitmproxy\n"
    )

    s = Settings(_env_file=str(env))

    assert s.sandbox_mode == "docker"
    assert not hasattr(s, "sandbox_credential_proxy_enabled")
    assert not hasattr(s, "sandbox_proxy_image")


@pytest.mark.parametrize("mode", ["none", "docker", "modal"])
def test_sandbox_mode_accepts_each_valid_literal(monkeypatch, mode):
    monkeypatch.setenv("SANDBOX_MODE", mode)
    s = Settings(_env_file=None)
    assert s.sandbox_mode == mode


def test_sandbox_mode_rejects_unknown_value(monkeypatch):
    """A value outside {none, docker, modal} fails fast at load, not inside the seam (Literal)."""
    for var in _SANDBOX_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SANDBOX_MODE", "firecracker")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("bad", ["0", "-1.0"])
def test_rejects_a_non_positive_sandbox_timeout(monkeypatch, bad):
    """A sandbox timeout <= 0 fails fast at load, not deep in a remote sandbox (Field(gt=0))."""
    for var in _SANDBOX_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SANDBOX_TIMEOUT_S", bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# Subagents


def test_subagent_defaults(monkeypatch):
    for var in _SUBAGENT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.subagent_max_parallel == 4  # concurrent-children cap (Gemini free-tier friendly)
    assert s.subagent_max_requests == 25  # per-child UsageLimits(request_limit=…) runaway cap
    assert s.subagent_result_max_bytes == 16_000  # the child-report truncation cap


def test_reads_subagent_vars_from_process_env(monkeypatch):
    monkeypatch.setenv("SUBAGENT_MAX_PARALLEL", "8")
    monkeypatch.setenv("SUBAGENT_MAX_REQUESTS", "50")
    monkeypatch.setenv("SUBAGENT_RESULT_MAX_BYTES", "32000")
    s = Settings(_env_file=None)
    assert s.subagent_max_parallel == 8
    assert s.subagent_max_requests == 50
    assert s.subagent_result_max_bytes == 32000


@pytest.mark.parametrize(
    "var", ["SUBAGENT_MAX_PARALLEL", "SUBAGENT_MAX_REQUESTS", "SUBAGENT_RESULT_MAX_BYTES"]
)
@pytest.mark.parametrize("bad", ["0", "-1"])
def test_rejects_non_positive_subagent_caps(monkeypatch, var, bad):
    """Each subagent cap <= 0 fails fast at load, not deep in a fan-out (Field(gt=0))."""
    for name in _SUBAGENT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(var, bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# Observability: Opik


def test_opik_defaults(monkeypatch):
    for var in _OPIK_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("DECODE_ENV", raising=False)
    s = Settings(_env_file=None)
    assert s.opik_api_key.get_secret_value() == ""  # presence trigger — empty == tracing off
    assert s.opik_workspace == "default"
    assert s.opik_project_name == "decode-local"  # DERIVED: decode-<DECODE_ENV> (ADR-0015 §8)
    assert s.opik_url_override is None  # None == Comet cloud OTLP base


def test_opik_project_name_is_derived_from_decode_env_when_unset(monkeypatch):
    """A trace must name the environment that produced it: the default is ``decode-<DECODE_ENV>``.

    At ``local`` (the default gate) that is ``decode-local`` — the suffix is applied ALWAYS, there is
    no bare ``decode`` project any more (ADR-0015 §8). The remote envs are covered in
    ``test_env_bucket.py`` (they need a stubbed bucket).
    """
    for var in _OPIK_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("DECODE_ENV", raising=False)

    s = Settings(_env_file=None)

    assert s.decode_env == "local"
    assert s.opik_project_name == "decode-local"
    # The mechanism: nobody supplied the field, so it is absent from ``model_fields_set`` — that (not
    # a sentinel value) is what marks it derivable, and deriving must not forge an "explicit" mark.
    assert "opik_project_name" not in s.model_fields_set


def test_an_explicit_opik_project_name_equal_to_the_derived_default_is_honoured(monkeypatch):
    """Anti-sentinel: setting the field to the literal the default derives to is still an EXPLICIT set.

    A sentinel/value comparison (``if opik_project_name == "decode-local"``) cannot tell this apart
    from "nobody set it" — ``model_fields_set`` can, and the value must survive untouched.
    """
    for var in _OPIK_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("DECODE_ENV", raising=False)
    monkeypatch.setenv("OPIK_PROJECT_NAME", "decode-local")

    s = Settings(_env_file=None)

    assert s.opik_project_name == "decode-local"
    assert "opik_project_name" in s.model_fields_set  # source-supplied → explicit


def test_reads_opik_vars_from_process_env(monkeypatch):
    monkeypatch.setenv("OPIK_API_KEY", "opik-secret-123")
    monkeypatch.setenv("OPIK_WORKSPACE", "my-workspace")
    monkeypatch.setenv("OPIK_PROJECT_NAME", "my-project")
    monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api/v1/private/otel")
    s = Settings(_env_file=None)
    assert s.opik_api_key.get_secret_value() == "opik-secret-123"
    assert s.opik_workspace == "my-workspace"
    # An explicit OPIK_PROJECT_NAME from the PROCESS ENV beats the derived decode-<env> default.
    assert s.opik_project_name == "my-project"
    assert "opik_project_name" in s.model_fields_set
    assert s.opik_url_override == "http://localhost:5173/api/v1/private/otel"


def test_loads_opik_vars_from_a_dotenv_file(tmp_path, monkeypatch):
    for var in _OPIK_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "OPIK_API_KEY=sk-opik-dotenv\n"
        "OPIK_WORKSPACE=ws-dotenv\n"
        "OPIK_PROJECT_NAME=proj-dotenv\n"
        "OPIK_URL_OVERRIDE=https://opik.example.com/api/v1/private/otel\n"
    )
    s = Settings(_env_file=str(env))
    assert s.opik_api_key.get_secret_value() == "sk-opik-dotenv"
    assert s.opik_workspace == "ws-dotenv"
    # An explicit OPIK_PROJECT_NAME from the DOTENV file beats the derived decode-<env> default.
    assert s.opik_project_name == "proj-dotenv"
    assert "opik_project_name" in s.model_fields_set
    assert s.opik_url_override == "https://opik.example.com/api/v1/private/otel"


def test_opik_api_key_not_in_repr():
    s = Settings(_env_file=None, opik_api_key="topsecretopik")
    assert "topsecretopik" not in repr(s)


def test_copying_env_example_to_dotenv_does_not_activate_opik(monkeypatch):
    """A copied .env.example must NOT set a truthy OPIK_API_KEY (presence-based silent-no-op default).

    The Opik block ships fully commented out, so loading .env.example verbatim as the .env file leaves
    ``opik_api_key`` empty — tracing stays off. Guards the task-091 regression where an uncommented
    ``OPIK_API_KEY=changeme`` would make a copied .env try to trace against Comet with a bogus token.
    """
    for var in _OPIK_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    env_example = Path(__file__).parents[4] / ".env.example"
    s = Settings(_env_file=str(env_example))
    assert s.opik_api_key.get_secret_value() == ""


# .env.example drift is covered GLOBALLY (every field, both directions, no allowlist) by
# tests/unit/decode/config/test_env_example_drift.py — it subsumes the per-section
# ``test_env_example_lists_every_*_var`` guards that used to live here (ADR-0015 §9).
#
# The DECODE_ENV gate + the Environment Bucket settings source (ADR-0015) have their own file:
# tests/unit/decode/config/test_env_bucket.py — including the restated "at DECODE_ENV=local, decode
# never imports kitaru" invariant (a fresh-subprocess import check).
