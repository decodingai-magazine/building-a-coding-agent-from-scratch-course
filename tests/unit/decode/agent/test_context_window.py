"""Unit tests for the context-window resolution seam (task 123).

The network boundary is stubbed in EVERY test — the unit suite never touches a live provider.

Every Settings here comes from ``hermetic_settings``: run as a whole suite, ``os.environ`` carries
the developer's ``.env`` (litellm's ``load_dotenv``), which would otherwise inject a real
``LLM_PROVIDER`` / ``MODAL_ENDPOINT_URL`` into these instances. See ``support.settings_env``.
"""

from __future__ import annotations

import httpx
import pytest
from support.settings_env import hermetic_settings

from decode.agent import context_window as cw

# A model id deliberately absent from MODEL_CONTEXT_WINDOWS, so the table cannot mask a probe.
UNKNOWN_MODEL = "acme/unlisted-model-v1"

# Captured at import, BEFORE the rootdir ``_no_context_window_probe`` guard stubs it out: this is
# the module under test, so it must run its real probe against a stubbed transport rather than the
# suite-wide no-op.
_REAL_PROBE = cw._probe


@pytest.fixture(autouse=True)
def _real_probe_against_a_stubbed_network(monkeypatch):
    """Undo the suite-wide probe guard here, and clear the memo around every test.

    The guard keeps every OTHER test off the network; this file is where the probe itself is under
    test, so it gets the real function and stubs the transport underneath instead. The memo is
    process-scoped, so leaking it across tests would let one test's answer silently satisfy the
    next and hide a probe that never fired.
    """
    monkeypatch.setattr(cw, "_probe", _REAL_PROBE)
    cw.reset_probe_cache()
    yield
    cw.reset_probe_cache()


@pytest.fixture
def make_settings(monkeypatch):
    """A real Settings built from explicit values only — never the developer's .env or host env.

    Real (not a mock) because "explicit wins" reads ``model_fields_set``, which only pydantic
    populates correctly; faking it would make the test agree with a bug.
    """

    def _make(**overrides):
        return hermetic_settings(monkeypatch, **overrides)

    return _make


def fake_response(payload: object, status: int = 200) -> httpx.Response:
    """An httpx.Response the probe can call ``raise_for_status`` / ``json`` on."""
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "https://stub"))


# --------------------------------------------------------------------------------------------
# Resolution order
# --------------------------------------------------------------------------------------------


def test_explicit_setting_wins_and_never_probes(make_settings, mocker):
    """An operator-supplied window short-circuits before any network call (AC 4)."""
    probe = mocker.patch.object(cw.httpx, "get")
    config = make_settings(
        llm_provider="modal",
        modal_endpoint_url="https://stub.modal.run",
        modal_endpoint_model=UNKNOWN_MODEL,
        compaction_context_window_tokens=8192,
    )

    resolved = cw.resolve_context_window_detail(config=config)

    assert resolved.tokens == 8192
    assert resolved.source == "explicit"
    assert resolved.is_assumed is False
    probe.assert_not_called()


def test_model_override_resolves_the_overridden_models_window(make_settings, mocker):
    """``--model`` decides the window, not the configured model (AC 1)."""
    mocker.patch.object(cw.httpx, "get", side_effect=httpx.ConnectError("offline"))
    # Configured model is Gemini's 1M window; the override is the 262k Qwen table row.
    config = make_settings(llm_provider="gemini", gemini_model="gemini-3.5-flash")

    assert cw.resolve_context_window(config=config) == 1_048_576
    assert cw.resolve_context_window("Qwen/Qwen3.6-35B-A3B-FP8", config=config) == 262_144


def test_table_wins_when_the_probe_cannot_answer(make_settings, mocker):
    """A dead probe degrades to the static table, not to the fallback (AC 8)."""
    mocker.patch.object(cw.httpx, "get", side_effect=httpx.ConnectError("offline"))
    config = make_settings(llm_provider="gemini", gemini_model="gemini-2.5-pro")

    resolved = cw.resolve_context_window_detail(config=config)

    assert resolved.tokens == 1_048_576
    assert resolved.source == "table"


def test_unknown_model_with_a_dead_probe_falls_back_and_is_flagged_assumed(make_settings):
    """Neither probe nor table → 200,000, and the notice is allowed to say so."""
    config = make_settings(llm_provider="openrouter", openrouter_model=UNKNOWN_MODEL)

    resolved = cw.resolve_context_window_detail(config=config)

    assert resolved.tokens == 200_000
    assert resolved.source == "fallback"
    assert resolved.is_assumed is True


# --------------------------------------------------------------------------------------------
# Per-provider probes (AC 2, 3)
# --------------------------------------------------------------------------------------------


def test_modal_probe_reads_max_model_len(make_settings, mocker):
    """LLM_PROVIDER=modal + a model absent from the table resolves to the endpoint's number."""
    get = mocker.patch.object(
        cw.httpx,
        "get",
        return_value=fake_response({"data": [{"id": UNKNOWN_MODEL, "max_model_len": 262_144}]}),
    )
    config = make_settings(
        llm_provider="modal",
        modal_endpoint_url="https://stub.modal.run",
        modal_endpoint_model=UNKNOWN_MODEL,
    )

    resolved = cw.resolve_context_window_detail(config=config)

    assert resolved.tokens == 262_144
    assert resolved.source == "probe"
    assert get.call_args.args[0] == "https://stub.modal.run/v1/models"


def test_modal_probe_sends_the_dual_proxy_headers_when_both_tokens_are_set(make_settings, mocker):
    """Auth mirrors the factory: Modal-Key / Modal-Secret, never a Bearer token."""
    get = mocker.patch.object(
        cw.httpx,
        "get",
        return_value=fake_response({"data": [{"id": UNKNOWN_MODEL, "max_model_len": 4096}]}),
    )
    config = make_settings(
        llm_provider="modal",
        modal_endpoint_url="https://stub.modal.run",
        modal_endpoint_model=UNKNOWN_MODEL,
        modal_proxy_token_id="tok-id",
        modal_proxy_token_secret="tok-secret",
    )

    cw.resolve_context_window(config=config)

    assert get.call_args.kwargs["headers"] == {
        "Modal-Key": "tok-id",
        "Modal-Secret": "tok-secret",
    }


def test_modal_probe_sends_no_headers_on_an_unauthenticated_endpoint(make_settings, mocker):
    get = mocker.patch.object(
        cw.httpx,
        "get",
        return_value=fake_response({"data": [{"id": UNKNOWN_MODEL, "max_model_len": 4096}]}),
    )
    config = make_settings(
        llm_provider="modal",
        modal_endpoint_url="https://stub.modal.run",
        modal_endpoint_model=UNKNOWN_MODEL,
    )

    cw.resolve_context_window(config=config)

    assert get.call_args.kwargs["headers"] is None


def test_modal_probe_uses_the_sole_entry_when_the_id_does_not_match(make_settings, mocker):
    """A dedicated vLLM endpoint serves one model; a prefix/suffix mismatch is not ambiguity."""
    mocker.patch.object(
        cw.httpx,
        "get",
        return_value=fake_response(
            {"data": [{"id": "served/other-name", "max_model_len": 32_768}]}
        ),
    )
    config = make_settings(
        llm_provider="modal",
        modal_endpoint_url="https://stub.modal.run",
        modal_endpoint_model=UNKNOWN_MODEL,
    )

    assert cw.resolve_context_window(config=config) == 32_768


def test_probe_declines_to_guess_between_several_unmatched_entries(make_settings, mocker):
    """Several rows and no id match → fall through, because guessing is the bug being fixed."""
    mocker.patch.object(
        cw.httpx,
        "get",
        return_value=fake_response(
            {"data": [{"id": "a", "context_length": 1000}, {"id": "b", "context_length": 2000}]}
        ),
    )
    config = make_settings(llm_provider="openrouter", openrouter_model=UNKNOWN_MODEL)

    assert cw.resolve_context_window(config=config) == 200_000


def test_openrouter_probe_reads_context_length(make_settings, mocker):
    get = mocker.patch.object(
        cw.httpx,
        "get",
        return_value=fake_response(
            {
                "data": [
                    {"id": "other/model", "context_length": 8_192},
                    {"id": UNKNOWN_MODEL, "context_length": 131_072},
                ]
            }
        ),
    )
    config = make_settings(llm_provider="openrouter", openrouter_model=UNKNOWN_MODEL)

    resolved = cw.resolve_context_window_detail(config=config)

    assert resolved.tokens == 131_072
    assert resolved.source == "probe"
    assert get.call_args.args[0] == "https://openrouter.ai/api/v1/models"


def test_gemini_probe_reads_input_token_limit(make_settings, mocker):
    client = mocker.MagicMock()
    client.models.get.return_value = mocker.MagicMock(input_token_limit=1_048_576)
    mocker.patch("google.genai.Client", return_value=client)
    config = make_settings(
        llm_provider="gemini", gemini_model=UNKNOWN_MODEL, gemini_api_key="key-123"
    )

    resolved = cw.resolve_context_window_detail(config=config)

    assert resolved.tokens == 1_048_576
    assert resolved.source == "probe"
    assert client.models.get.call_args.kwargs["model"] == UNKNOWN_MODEL


def test_gemini_probe_is_skipped_without_an_api_key(make_settings, mocker):
    """No key means a guaranteed 401 — skip rather than burn the timeout."""
    build_client = mocker.patch("google.genai.Client")
    config = make_settings(llm_provider="gemini", gemini_model=UNKNOWN_MODEL, gemini_api_key="")

    assert cw.resolve_context_window(config=config) == 200_000
    build_client.assert_not_called()


def test_modal_probe_is_skipped_without_an_endpoint_url(make_settings, mocker):
    get = mocker.patch.object(cw.httpx, "get")
    config = make_settings(llm_provider="modal", modal_endpoint_model=UNKNOWN_MODEL)

    assert cw.resolve_context_window(config=config) == 200_000
    get.assert_not_called()


# --------------------------------------------------------------------------------------------
# Failure modes — one test per mode, none may propagate (AC 5)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "outcome"),
    [
        ("timeout", httpx.ReadTimeout("too slow")),
        ("connect_error", httpx.ConnectError("dns")),
        ("unexpected_exception", RuntimeError("boom")),
    ],
)
def test_probe_exceptions_fall_through_to_the_table(make_settings, mocker, name, outcome):
    mocker.patch.object(cw.httpx, "get", side_effect=outcome)
    config = make_settings(llm_provider="openrouter", openrouter_model="gemini-3.5-flash")

    resolved = cw.resolve_context_window_detail(config=config)

    assert resolved.tokens == 1_048_576, name
    assert resolved.source == "table"


@pytest.mark.parametrize(
    ("name", "payload", "status"),
    [
        ("non_200", {"data": []}, 500),
        ("malformed_not_a_mapping", ["nope"], 200),
        ("malformed_data_not_a_list", {"data": {"id": "x"}}, 200),
        ("missing_field", {"data": [{"id": UNKNOWN_MODEL}]}, 200),
        ("field_is_null", {"data": [{"id": UNKNOWN_MODEL, "context_length": None}]}, 200),
        ("field_is_a_string", {"data": [{"id": UNKNOWN_MODEL, "context_length": "lots"}]}, 200),
        ("field_is_zero", {"data": [{"id": UNKNOWN_MODEL, "context_length": 0}]}, 200),
        ("field_is_negative", {"data": [{"id": UNKNOWN_MODEL, "context_length": -1}]}, 200),
        ("empty_catalog", {"data": []}, 200),
    ],
)
def test_bad_payloads_fall_through_to_the_fallback(make_settings, mocker, name, payload, status):
    """Every malformed shape degrades to 200,000 — never a crash, never a traceback."""
    mocker.patch.object(cw.httpx, "get", return_value=fake_response(payload, status))
    config = make_settings(llm_provider="openrouter", openrouter_model=UNKNOWN_MODEL)

    resolved = cw.resolve_context_window_detail(config=config)

    assert resolved.tokens == 200_000, name
    assert resolved.source == "fallback"


def test_a_probe_failure_logs_at_debug_and_does_not_warn(make_settings, mocker, caplog):
    """A failed probe is a DEBUG line: expected weather, not an operator-facing problem."""
    mocker.patch.object(cw.httpx, "get", side_effect=httpx.ConnectError("offline"))
    config = make_settings(llm_provider="openrouter", openrouter_model=UNKNOWN_MODEL)

    with caplog.at_level("DEBUG", logger=cw.logger.name):
        cw.resolve_context_window(config=config)

    assert any(record.levelname == "DEBUG" for record in caplog.records)
    assert not [record for record in caplog.records if record.levelno >= 30]


# --------------------------------------------------------------------------------------------
# Memoisation (AC 6)
# --------------------------------------------------------------------------------------------


def test_probing_is_memoised_per_model_id(make_settings, mocker):
    get = mocker.patch.object(
        cw.httpx,
        "get",
        return_value=fake_response({"data": [{"id": UNKNOWN_MODEL, "context_length": 4096}]}),
    )
    config = make_settings(llm_provider="openrouter", openrouter_model=UNKNOWN_MODEL)

    first = cw.resolve_context_window(config=config)
    second = cw.resolve_context_window(config=config)

    assert first == second == 4096
    assert get.call_count == 1


def test_a_failed_probe_is_memoised_too(make_settings, mocker):
    """Otherwise an offline run pays the timeout once per turn instead of once per process."""
    get = mocker.patch.object(cw.httpx, "get", side_effect=httpx.ConnectError("offline"))
    config = make_settings(llm_provider="openrouter", openrouter_model=UNKNOWN_MODEL)

    cw.resolve_context_window(config=config)
    cw.resolve_context_window(config=config)

    assert get.call_count == 1


def test_a_different_model_id_is_probed_separately(make_settings, mocker):
    get = mocker.patch.object(
        cw.httpx, "get", return_value=fake_response({"data": [{"id": "x", "context_length": 10}]})
    )
    config = make_settings(llm_provider="openrouter", openrouter_model=UNKNOWN_MODEL)

    cw.resolve_context_window(config=config)
    cw.resolve_context_window("another/model", config=config)

    assert get.call_count == 2


def test_resolve_defaults_to_the_process_settings_singleton(make_settings, mocker):
    """The no-argument call reads the singleton, which is what production call sites do."""
    mocker.patch.object(cw.httpx, "get", side_effect=httpx.ConnectError("offline"))
    mocker.patch.object(
        cw, "settings", make_settings(llm_provider="gemini", gemini_model="gemini-3.5-flash")
    )

    assert cw.resolve_context_window() == 1_048_576
