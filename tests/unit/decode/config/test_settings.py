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
    assert s.openrouter_model == "qwen/qwen3-coder:free"
    assert s.modal_endpoint_model == "openai/gpt-oss-120b"
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
