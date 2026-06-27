from pathlib import Path

import pytest
from pydantic import ValidationError

from decode.config import settings as singleton
from decode.config.settings import Settings

# Every provider var introduced by ADR-0005 (task 037). Cleared in default/.env tests so a
# developer's real environment cannot leak into the assertions.
_PROVIDER_ENV_VARS = (
    "LLM_PROVIDER",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "MODAL_ENDPOINT_URL",
    "MODAL_ENDPOINT_MODEL",
    "MODAL_PROXY_TOKEN_ID",
    "MODAL_PROXY_TOKEN_SECRET",
)

# Context compaction vars introduced by ADR-0006 (task 041). Cleared in default/invariant tests so a
# developer's real environment cannot leak into the assertions.
_COMPACTION_ENV_VARS = (
    "COMPACTION_ENABLED",
    "COMPACTION_CONTEXT_WINDOW_TOKENS",
    "COMPACTION_RESERVE_FRACTION",
    "MICROCOMPACTION_RESERVE_FRACTION",
    "COMPACTION_KEEP_RECENT_TOKENS",
    "MEMORY_COMPRESSION_ENABLED",
)

# LSP / code intelligence vars introduced by ADR-0007 (task 050). Cleared in default tests so a
# developer's real environment cannot leak into the assertions.
_LSP_ENV_VARS = (
    "LSP_ENABLED",
    "LSP_SERVER_COMMAND",
    "LSP_SERVER_ARGS",
    "LSP_DIAGNOSTICS_ON_EDIT",
    "LSP_REQUEST_TIMEOUT_S",
)

# Kitaru durable runtime vars introduced by ADR-0008 (task 057). Cleared in default tests so a
# developer's real environment cannot leak into the assertions.
_RUNTIME_ENV_VARS = (
    "RUNTIME_ENABLED",
    "RUNTIME_CHECKPOINT_STRATEGY",
    "RUNTIME_WAIT_TIMEOUT_S",
    "RUNTIME_CREDENTIALS_PROXY_ENABLED",
    "RUNTIME_SECRET_NAME",
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


def test_env_example_lists_every_lsp_var():
    """Drift guard: each LSP setting has a matching line in .env.example (AGENTS.md gate)."""
    env_example = (Path(__file__).parents[4] / ".env.example").read_text()
    for var in _LSP_ENV_VARS:
        assert var in env_example, f"{var} missing from .env.example"


def test_runtime_defaults(monkeypatch):
    for var in _RUNTIME_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.runtime_enabled is True
    assert s.runtime_checkpoint_strategy == "turn"
    assert s.runtime_wait_timeout_s == 600.0
    assert s.runtime_credentials_proxy_enabled is False
    assert s.runtime_secret_name == "decode-llm-creds"


def test_reads_runtime_vars_from_process_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_ENABLED", "false")
    monkeypatch.setenv("RUNTIME_CHECKPOINT_STRATEGY", "calls")
    monkeypatch.setenv("RUNTIME_WAIT_TIMEOUT_S", "120.0")
    monkeypatch.setenv("RUNTIME_CREDENTIALS_PROXY_ENABLED", "true")
    monkeypatch.setenv("RUNTIME_SECRET_NAME", "my-creds")
    s = Settings(_env_file=None)
    assert s.runtime_enabled is False
    assert s.runtime_checkpoint_strategy == "calls"
    assert s.runtime_wait_timeout_s == 120.0
    assert s.runtime_credentials_proxy_enabled is True
    assert s.runtime_secret_name == "my-creds"


def test_loads_runtime_vars_from_a_dotenv_file(tmp_path, monkeypatch):
    for var in _RUNTIME_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "RUNTIME_ENABLED=false\n"
        "RUNTIME_CHECKPOINT_STRATEGY=calls\n"
        "RUNTIME_WAIT_TIMEOUT_S=300.0\n"
        "RUNTIME_CREDENTIALS_PROXY_ENABLED=true\n"
        "RUNTIME_SECRET_NAME=dotenv-creds\n"
    )
    s = Settings(_env_file=str(env))
    assert s.runtime_enabled is False
    assert s.runtime_checkpoint_strategy == "calls"
    assert s.runtime_wait_timeout_s == 300.0
    assert s.runtime_credentials_proxy_enabled is True
    assert s.runtime_secret_name == "dotenv-creds"


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


def test_env_example_lists_every_runtime_var():
    """Drift guard: each runtime setting has a matching line in .env.example (AGENTS.md gate)."""
    env_example = (Path(__file__).parents[4] / ".env.example").read_text()
    for var in _RUNTIME_ENV_VARS:
        assert var in env_example, f"{var} missing from .env.example"
