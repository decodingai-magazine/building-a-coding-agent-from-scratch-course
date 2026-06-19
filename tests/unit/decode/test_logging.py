import logging

import decode.logging as dlog


def test_init_logger_sets_root_level(monkeypatch):
    monkeypatch.setattr(dlog, "_INITIALIZED", False)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    dlog.init_logger()
    assert logging.getLogger().level == logging.WARNING


def test_explicit_level_overrides_env(monkeypatch):
    monkeypatch.setattr(dlog, "_INITIALIZED", False)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    dlog.init_logger("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_init_logger_is_idempotent(monkeypatch):
    monkeypatch.setattr(dlog, "_INITIALIZED", False)
    dlog.init_logger("INFO")
    before = len(logging.getLogger().handlers)
    dlog.init_logger("INFO")  # second call is a no-op
    assert len(logging.getLogger().handlers) == before
