"""Unit tests for the Langfuse tracing seam (ADR-0024)."""

import base64
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
    CostAnnotatingExporter,
    init_tracing,
    is_tracing_active,
    record_output,
    reset_tracing,
    root_span,
)


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    reset_tracing()
    yield
    reset_tracing()


@pytest.fixture
def fake_langfuse_keys(monkeypatch):
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr("fake-secret"), raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", SecretStr("fake-public"), raising=False)
    monkeypatch.setattr(settings, "langfuse_base_url", "https://cloud.langfuse.com", raising=False)


@pytest.fixture
def mock_logfire(mocker):
    return SimpleNamespace(
        exporter_cls=mocker.patch("decode.observability.tracing.OTLPSpanExporter"),
        bsp_cls=mocker.patch("decode.observability.tracing.BatchSpanProcessor"),
        configure=mocker.patch("decode.observability.tracing.logfire.configure"),
        instrument=mocker.patch("decode.observability.tracing.logfire.instrument_pydantic_ai"),
    )


def test_init_tracing_without_keys_is_a_silent_noop(monkeypatch, mock_logfire):
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr(""), raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", SecretStr(""), raising=False)

    assert init_tracing() is False
    assert is_tracing_active() is False
    mock_logfire.configure.assert_not_called()
    mock_logfire.instrument.assert_not_called()
    mock_logfire.exporter_cls.assert_not_called()


def test_init_tracing_does_not_change_otel_environment():
    before = {key: value for key, value in os.environ.items() if key.startswith("OTEL_")}

    assert init_tracing() is False

    after = {key: value for key, value in os.environ.items() if key.startswith("OTEL_")}
    assert before == after


@pytest.mark.parametrize(
    ("secret_key", "public_key"),
    [("secret", ""), ("", "public")],
)
def test_init_tracing_rejects_incomplete_credentials(
    monkeypatch, mock_logfire, secret_key, public_key
):
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr(secret_key), raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", SecretStr(public_key), raising=False)

    with pytest.raises(ValueError, match="requires both LANGFUSE_PUBLIC_KEY"):
        init_tracing()

    mock_logfire.exporter_cls.assert_not_called()


def test_init_tracing_builds_langfuse_otlp_exporter_with_basic_auth(
    monkeypatch, fake_langfuse_keys, mock_logfire
):
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr("secret-123"), raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", SecretStr("public-456"), raising=False)
    monkeypatch.setattr(
        settings, "langfuse_base_url", "https://us.cloud.langfuse.com/", raising=False
    )

    assert init_tracing() is True

    auth = base64.b64encode(b"public-456:secret-123").decode("ascii")
    mock_logfire.exporter_cls.assert_called_once_with(
        endpoint="https://us.cloud.langfuse.com/api/public/otel/v1/traces",
        headers={
            "Authorization": f"Basic {auth}",
            "x-langfuse-ingestion-version": "4",
        },
    )
    (wrapper,) = mock_logfire.bsp_cls.call_args.args
    assert isinstance(wrapper, CostAnnotatingExporter)
    assert wrapper._wrapped is mock_logfire.exporter_cls.return_value
    assert mock_logfire.configure.call_args.kwargs["console"] is False
    assert mock_logfire.configure.call_args.kwargs["send_to_logfire"] is False
    scrubber = mock_logfire.configure.call_args.kwargs["scrubbing"]
    assert scrubber.callback is tracing._preserve_langfuse_session_id
    assert (
        scrubber.callback(SimpleNamespace(path=("attributes", "session.id"), value="opaque-id"))
        == "opaque-id"
    )
    assert (
        scrubber.callback(
            SimpleNamespace(path=("attributes", "langfuse.session.id"), value="opaque-id")
        )
        == "opaque-id"
    )
    assert (
        scrubber.callback(SimpleNamespace(path=("attributes", "password"), value="secret")) is None
    )
    mock_logfire.instrument.assert_called_once_with()


def test_init_tracing_rejects_a_non_http_host(monkeypatch, fake_langfuse_keys, mock_logfire):
    monkeypatch.setattr(settings, "langfuse_base_url", "langfuse.local", raising=False)

    with pytest.raises(ValueError, match="LANGFUSE_BASE_URL must be an http"):
        init_tracing()

    mock_logfire.exporter_cls.assert_not_called()


def test_init_tracing_logs_host_but_never_credentials(fake_langfuse_keys, mock_logfire, caplog):
    with caplog.at_level(logging.INFO, logger="decode.observability.tracing"):
        init_tracing()

    infos = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and record.name == "decode.observability.tracing"
    ]
    assert len(infos) == 1
    message = infos[0].getMessage()
    assert "cloud.langfuse.com" in message
    assert "fake-secret" not in message
    assert "fake-public" not in message


def test_init_tracing_is_idempotent(fake_langfuse_keys, mock_logfire):
    assert init_tracing() is True
    assert init_tracing() is True
    mock_logfire.configure.assert_called_once()
    mock_logfire.instrument.assert_called_once()
    mock_logfire.exporter_cls.assert_called_once()


def test_reset_tracing_allows_reinitialization(fake_langfuse_keys, mock_logfire):
    init_tracing()
    reset_tracing()
    assert is_tracing_active() is False

    init_tracing()
    assert mock_logfire.configure.call_count == 2


def test_root_span_is_nullcontext_when_inactive():
    span = root_span("chat_turn", session_id="s1")

    assert isinstance(span, contextlib.nullcontext)
    with span as value:
        assert value is None


def test_root_span_sets_langfuse_fields_and_propagates_metadata(
    fake_langfuse_keys, mock_logfire, mocker
):
    init_tracing()
    span_value = mocker.MagicMock()
    span_context = mocker.MagicMock()
    span_context.__enter__.return_value = span_value
    span_fn = mocker.patch("decode.observability.tracing.logfire.span", return_value=span_context)
    propagation = mocker.patch(
        "decode.observability.tracing.propagate_attributes",
        return_value=contextlib.nullcontext(),
    )

    with root_span(
        "decode_run",
        session_id="session-1",
        input="run this",
        metadata={"git_sha": "abc123", "model": "gemini"},
    ) as span:
        assert span is span_value

    span_fn.assert_called_once_with("decode_run", **{"langfuse.trace.input": "run this"})
    propagation.assert_called_once_with(
        session_id="session-1",
        metadata={"git_sha": "abc123", "model": "gemini"},
        trace_name="decode_run",
    )
    span_value.set_attribute.assert_called_once_with("langfuse.session.id", "session-1")


def test_root_span_omits_empty_input(fake_langfuse_keys, mock_logfire, mocker):
    init_tracing()
    span_context = mocker.MagicMock()
    span_context.__enter__.return_value = None
    span_fn = mocker.patch("decode.observability.tracing.logfire.span", return_value=span_context)
    mocker.patch(
        "decode.observability.tracing.propagate_attributes",
        return_value=contextlib.nullcontext(),
    )

    with root_span("chat_turn", input=""):
        pass

    span_fn.assert_called_once_with("chat_turn")


def test_record_output_only_sets_non_empty_text(mocker):
    span = mocker.Mock()

    record_output(None, "text")
    record_output(span, "")
    record_output(span, 123)
    span.set_attribute.assert_not_called()

    record_output(span, "the turn is done")
    span.set_attribute.assert_called_once_with("langfuse.trace.output", "the turn is done")


def test_real_root_span_emits_langfuse_attributes_to_memory(monkeypatch, capfire):  # noqa: F811
    monkeypatch.setattr(tracing, "_active", True)

    with root_span(
        "chat_turn",
        session_id="session-1",
        input="hello",
        metadata={"git_sha": "abc123"},
    ):
        pass

    spans = capfire.exporter.exported_spans_as_dict()
    roots = [span for span in spans if span["name"] == "chat_turn"]
    assert roots
    attributes = roots[0]["attributes"]
    assert attributes["langfuse.trace.metadata.git_sha"] == '"abc123"'
    assert attributes["langfuse.trace.input"] == "hello"
