"""Unit tests for the Opik tracing init seam (ADR-0014, task 091).

Two hermetic layers, per ADR-0014 §7:

* **Mocked boundary** (most tests) — ``logfire.configure`` / ``instrument_pydantic_ai`` and the OTLP
  ``OTLPSpanExporter`` / ``BatchSpanProcessor`` are patched, so the wiring is asserted precisely (called
  once, right endpoint + headers) with **no** real global-provider mutation and **no** network. Mocking
  the exporter is a stronger no-network guarantee than a live in-memory exporter — the real HTTP
  exporter is never even constructed.
* **``logfire.testing`` in-memory** (one test) — the ``capfire`` fixture proves an *active* ``root_span``
  emits a real, capturable logfire span carrying the ``thread_id`` attribute, entirely in memory.

The module ``_active`` flag is reset around every test by the local autouse fixture so init state never
leaks; the rootdir ``_no_opik_tracing`` fixture blanks the key so the no-key path is the default.
"""

import contextlib
import logging
import os
from types import SimpleNamespace

import pytest
from logfire.testing import (
    capfire,  # noqa: F401 — imported so pytest registers the in-memory fixture
)
from pydantic import SecretStr

from decode.config.settings import settings
from decode.observability import tracing
from decode.observability.tracing import (
    init_tracing,
    is_tracing_active,
    reset_tracing,
    root_span,
)

_CLOUD_ENDPOINT = "https://www.comet.com/opik/api/v1/private/otel/v1/traces"


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    """Clear the module ``_active`` flag around every test so ``init_tracing`` state never leaks."""
    reset_tracing()
    yield
    reset_tracing()


@pytest.fixture
def fake_opik_key(monkeypatch):
    """Opt into tracing: set a fake key on the singleton (the autouse conftest blanks it otherwise)."""
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("fake-opik-key-xyz"), raising=False)
    return "fake-opik-key-xyz"


@pytest.fixture
def mock_logfire(mocker):
    """Patch the logfire + OTLP boundary so no real configure / exporter / network ever happens."""
    return SimpleNamespace(
        exporter_cls=mocker.patch("decode.observability.tracing.OTLPSpanExporter"),
        bsp_cls=mocker.patch("decode.observability.tracing.BatchSpanProcessor"),
        configure=mocker.patch("decode.observability.tracing.logfire.configure"),
        instrument=mocker.patch("decode.observability.tracing.logfire.instrument_pydantic_ai"),
    )


# --- no key: presence-based silent no-op -------------------------------------------------------


def test_init_tracing_without_key_returns_false_and_configures_nothing(mock_logfire):
    assert settings.opik_api_key.get_secret_value() == ""  # blanked by the autouse conftest fixture

    result = init_tracing()

    assert result is False
    assert is_tracing_active() is False
    mock_logfire.configure.assert_not_called()
    mock_logfire.instrument.assert_not_called()
    mock_logfire.exporter_cls.assert_not_called()


def test_init_tracing_without_key_leaves_otel_environ_unchanged():
    before = {k: v for k, v in os.environ.items() if k.startswith("OTEL_")}

    assert init_tracing() is False

    after = {k: v for k, v in os.environ.items() if k.startswith("OTEL_")}
    assert before == after  # export is settings-driven — no global OTEL_* env is ever mutated


def test_root_span_is_nullcontext_when_inactive():
    span = root_span("chat_turn", thread_id="s1")

    assert isinstance(span, contextlib.nullcontext)
    with span as value:
        assert value is None


# --- fake key: active wiring -------------------------------------------------------------------


def test_init_tracing_with_key_returns_true_and_configures_once(fake_opik_key, mock_logfire):
    result = init_tracing()

    assert result is True
    assert is_tracing_active() is True
    mock_logfire.configure.assert_called_once()
    kwargs = mock_logfire.configure.call_args.kwargs
    assert kwargs["send_to_logfire"] is False
    assert kwargs["additional_span_processors"] == [mock_logfire.bsp_cls.return_value]
    mock_logfire.instrument.assert_called_once_with()


def test_init_tracing_builds_cloud_exporter_with_settings_headers(monkeypatch, mock_logfire):
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("k-123"), raising=False)
    monkeypatch.setattr(settings, "opik_workspace", "ws-A", raising=False)
    monkeypatch.setattr(settings, "opik_project_name", "proj-B", raising=False)
    monkeypatch.setattr(settings, "opik_url_override", None, raising=False)

    init_tracing()

    mock_logfire.exporter_cls.assert_called_once_with(
        endpoint=_CLOUD_ENDPOINT,
        headers={"Authorization": "k-123", "Comet-Workspace": "ws-A", "projectName": "proj-B"},
    )
    # The exporter is wrapped in a BatchSpanProcessor handed to logfire.configure.
    mock_logfire.bsp_cls.assert_called_once_with(mock_logfire.exporter_cls.return_value)


def test_init_tracing_uses_url_override_base_when_set(monkeypatch, mock_logfire):
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("k"), raising=False)
    monkeypatch.setattr(
        settings, "opik_url_override", "http://localhost:5173/api/v1/private/otel", raising=False
    )

    init_tracing()

    kwargs = mock_logfire.exporter_cls.call_args.kwargs
    assert kwargs["endpoint"] == "http://localhost:5173/api/v1/private/otel/v1/traces"


def test_init_tracing_strips_a_trailing_slash_from_the_url_override(monkeypatch, mock_logfire):
    """A trailing-slash ``opik_url_override`` must not produce a ``//v1/traces`` (task-091 review)."""
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("k"), raising=False)
    monkeypatch.setattr(
        settings, "opik_url_override", "http://localhost:5173/api/v1/private/otel/", raising=False
    )

    init_tracing()

    kwargs = mock_logfire.exporter_cls.call_args.kwargs
    assert kwargs["endpoint"] == "http://localhost:5173/api/v1/private/otel/v1/traces"


def test_init_tracing_logs_one_info_line_naming_project_and_target(
    monkeypatch, mock_logfire, caplog
):
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("secret-key-abc"), raising=False)
    monkeypatch.setattr(settings, "opik_project_name", "proj-B", raising=False)
    monkeypatch.setattr(settings, "opik_url_override", None, raising=False)

    with caplog.at_level(logging.INFO, logger="decode.observability.tracing"):
        init_tracing()

    infos = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and r.name == "decode.observability.tracing"
    ]
    assert len(infos) == 1
    message = infos[0].getMessage()
    assert "proj-B" in message
    assert "cloud" in message
    assert "secret-key-abc" not in message  # the key must never be logged


def test_init_tracing_logs_self_hosted_target_when_override_set(monkeypatch, mock_logfire, caplog):
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("k"), raising=False)
    monkeypatch.setattr(
        settings, "opik_url_override", "http://localhost:5173/api/v1/private/otel", raising=False
    )

    with caplog.at_level(logging.INFO, logger="decode.observability.tracing"):
        init_tracing()

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "self-hosted" in infos[0].getMessage()


def test_init_tracing_is_idempotent(fake_opik_key, mock_logfire):
    assert init_tracing() is True
    assert init_tracing() is True  # second call must reconfigure nothing (process-global provider)

    mock_logfire.configure.assert_called_once()
    mock_logfire.instrument.assert_called_once()
    mock_logfire.exporter_cls.assert_called_once()


def test_reset_tracing_allows_reinit(fake_opik_key, mock_logfire):
    init_tracing()
    assert is_tracing_active() is True

    reset_tracing()
    assert is_tracing_active() is False

    init_tracing()
    assert mock_logfire.configure.call_count == 2  # cleared flag re-drives a fresh configure


def test_root_span_opens_logfire_span_when_active(fake_opik_key, mock_logfire, mocker):
    span_fn = mocker.patch("decode.observability.tracing.logfire.span")
    init_tracing()

    result = root_span("chat_turn", thread_id="sess-9")

    span_fn.assert_called_once_with("chat_turn", thread_id="sess-9")
    assert result is span_fn.return_value


# --- logfire.testing in-memory: a real span is emitted when active -----------------------------


def test_root_span_emits_a_real_span_captured_in_memory(monkeypatch, capfire):  # noqa: F811
    """An active ``root_span`` emits a real logfire span the in-memory exporter captures (ADR-0014 §7).

    ``_active`` is forced True directly rather than through ``init_tracing`` because that path's real
    ``logfire.configure`` would replace ``capfire``'s in-memory exporter; here we only prove ``root_span``
    opens a genuine span (name + ``thread_id`` attribute) with no network.
    """
    monkeypatch.setattr(tracing, "_active", True)

    with root_span("chat_turn", thread_id="sess-1"):
        pass

    spans = capfire.exporter.exported_spans_as_dict()
    named = [s for s in spans if s["name"] == "chat_turn"]
    assert named, [s["name"] for s in spans]
    assert named[0]["attributes"]["thread_id"] == "sess-1"
