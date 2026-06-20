from pathlib import Path

from decode.config import settings as singleton
from decode.config.settings import Settings


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
