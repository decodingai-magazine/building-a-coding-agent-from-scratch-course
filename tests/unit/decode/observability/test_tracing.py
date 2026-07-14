"""Unit tests for the Opik tracing init seam (ADR-0014 §7, task 091).

Two hermetic layers: most tests patch the ``logfire.configure`` / ``instrument_pydantic_ai`` +
OTLP exporter boundary (the real HTTP exporter is never constructed — no network, no global
provider mutation); one test uses ``logfire.testing``'s in-memory ``capfire`` to prove an
active ``root_span`` emits a real span. The module ``_active`` flag is reset around every test;
the rootdir ``_no_opik_tracing`` fixture blanks the key so the no-key path is the default.
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

from decode.config.settings import Settings, settings
from decode.observability import tracing
from decode.observability.tracing import (
    init_tracing,
    is_tracing_active,
    record_output,
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


# no key: presence-based silent no-op


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


# fake key: active wiring


def test_init_tracing_with_key_returns_true_and_configures_once(fake_opik_key, mock_logfire):
    result = init_tracing()

    assert result is True
    assert is_tracing_active() is True
    mock_logfire.configure.assert_called_once()
    kwargs = mock_logfire.configure.call_args.kwargs
    assert kwargs["send_to_logfire"] is False
    assert kwargs["console"] is False
    assert kwargs["additional_span_processors"] == [mock_logfire.bsp_cls.return_value]
    mock_logfire.instrument.assert_called_once_with()


def test_init_tracing_disables_the_logfire_console_exporter(fake_opik_key, mock_logfire):
    """``console=False``: logfire must NOT print spans to stdout — the REPL owns that surface.

    Regression pin. ``send_to_logfire=False`` only disables cloud egress; logfire's DEFAULT console
    setting installs a ``ShowParentsConsoleSpanExporter`` straight to stdout, which flooded the TUI
    with a raw span trace (timestamps, ``_MAIN_AGENT run``, ``running tool: grep``) *through* the
    ``patch_stdout()`` the prompt is pinned under. decode renders events through its OWN event bus.
    """
    init_tracing()

    assert mock_logfire.configure.call_args.kwargs["console"] is False
    # ...and the OTLP export path is untouched: Opik still receives the spans.
    assert mock_logfire.configure.call_args.kwargs["additional_span_processors"] == [
        mock_logfire.bsp_cls.return_value
    ]


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


def test_init_tracing_header_carries_the_derived_per_env_project_name(monkeypatch, mock_logfire):
    """Spot-check the whole path: the derived ``decode-<DECODE_ENV>`` reaches the OTLP header (ADR-0015 §8).

    ``tracing.py`` itself is unchanged — it already reads ``settings.opik_project_name``; this proves
    the derived value (nobody set ``OPIK_PROJECT_NAME``) is what an Opik trace is filed under.
    """
    monkeypatch.delenv("DECODE_ENV", raising=False)
    monkeypatch.delenv("OPIK_PROJECT_NAME", raising=False)
    derived = Settings(_env_file=None).opik_project_name
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("k-123"), raising=False)
    monkeypatch.setattr(settings, "opik_workspace", "default", raising=False)
    monkeypatch.setattr(settings, "opik_project_name", derived, raising=False)
    monkeypatch.setattr(settings, "opik_url_override", None, raising=False)

    init_tracing()

    assert derived == "decode-local"
    kwargs = mock_logfire.exporter_cls.call_args.kwargs
    assert kwargs["headers"]["projectName"] == "decode-local"


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


def test_root_span_sets_input_attribute_so_opik_populates_trace_input(
    fake_opik_key, mock_logfire, mocker
):
    """The turn/run input rides as the ``input`` span attribute (Opik buckets it into trace INPUT)."""
    span_fn = mocker.patch("decode.observability.tracing.logfire.span")
    init_tracing()

    root_span("chat_turn", thread_id="s1", input="what can you do?")

    span_fn.assert_called_once_with("chat_turn", thread_id="s1", input="what can you do?")


def test_root_span_omits_input_attribute_when_empty(fake_opik_key, mock_logfire, mocker):
    """An empty/None input is not set as an attribute (nothing to show — keep the span clean)."""
    span_fn = mocker.patch("decode.observability.tracing.logfire.span")
    init_tracing()

    root_span("chat_turn", thread_id="s1", input="")

    span_fn.assert_called_once_with("chat_turn", thread_id="s1")


def test_record_output_sets_output_only_for_non_empty_text(mocker):
    """``record_output`` sets the ``output`` attribute for real text and no-ops otherwise (ADR-0014 §4)."""
    span = mocker.Mock()

    record_output(None, "text")  # tracing off (nullcontext yielded None) — must not raise
    record_output(span, "")  # empty output — nothing to record
    record_output(span, 123)  # non-str output — skip rather than store a bad attribute
    span.set_attribute.assert_not_called()

    record_output(span, "the turn is done")
    span.set_attribute.assert_called_once_with("output", "the turn is done")


# logfire.testing in-memory: a real span is emitted when active


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
