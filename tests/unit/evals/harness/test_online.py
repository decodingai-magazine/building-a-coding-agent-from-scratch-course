"""Offline tests for the online eval track — live-thread scoring (ADR-0017 §10; task 117).

No infra, no keys, no network: ``opik.evaluation.evaluate_threads`` is MOCKED, so the wiring
(project selection, the single conversation metric, the required transforms, ``eval_project_name=None``)
is asserted without ever reaching Opik. The offline pieces — key gating, project selection, the metric
construction, the input/output transforms, and the score formatter — are tested directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opik.evaluation.metrics.conversation.conversation_thread_metric import (
    ConversationThreadMetric,
)

from evals.harness import online


@pytest.fixture
def with_keys(mocker):
    """Give ``settings`` a gemini provider with both required keys present (the happy path)."""
    mocker.patch.object(online.settings, "llm_provider", "gemini")
    mocker.patch.object(
        online.settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )
    mocker.patch.object(
        online.settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: "gem-key")
    )
    mocker.patch.object(online.settings, "eval_judge_model", "")


# --- live project selection: the LIVE project, never eval_project_name (ADR-0017 §10) ---------------


def test_live_project_is_the_repl_project_not_the_eval_project(mocker):
    mocker.patch.object(online.settings, "opik_project_name", "decode-prod")
    mocker.patch.object(online.settings, "eval_project_name", "decode-evals")

    assert online.live_project_name() == "decode-prod"


# --- key gating: friendly skip when a required key is missing ---------------------------------------


def test_no_missing_keys_when_opik_and_gemini_present(with_keys):
    assert online.online_keys_missing() == []


def test_missing_opik_key_is_reported(with_keys, mocker):
    mocker.patch.object(
        online.settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "  ")
    )

    assert online.online_keys_missing() == ["OPIK_API_KEY"]


def test_missing_gemini_judge_key_is_reported(with_keys, mocker):
    mocker.patch.object(
        online.settings, "gemini_api_key", SimpleNamespace(get_secret_value=lambda: "")
    )

    assert online.online_keys_missing() == ["GEMINI_API_KEY"]


def test_openrouter_provider_requires_the_openrouter_key(mocker):
    mocker.patch.object(online.settings, "llm_provider", "openrouter")
    mocker.patch.object(
        online.settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )
    mocker.patch.object(
        online.settings, "openrouter_api_key", SimpleNamespace(get_secret_value=lambda: "")
    )

    assert online.online_keys_missing() == ["OPENROUTER_API_KEY"]


def test_modal_provider_requires_the_endpoint_url(mocker):
    mocker.patch.object(online.settings, "llm_provider", "modal")
    mocker.patch.object(
        online.settings, "opik_api_key", SimpleNamespace(get_secret_value=lambda: "opik-key")
    )
    mocker.patch.object(online.settings, "modal_endpoint_url", "")

    assert online.online_keys_missing() == ["MODAL_ENDPOINT_URL"]


# --- the single conversation metric constructs offline with the routed judge model -----------------


def test_make_conversation_metric_is_a_conversation_thread_metric(mocker):
    mocker.patch.object(online.settings, "eval_judge_model", "")
    mocker.patch.object(online.settings, "llm_provider", "gemini")

    metric = online.make_conversation_metric()

    assert isinstance(metric, ConversationThreadMetric)
    assert metric.name == online.CONVERSATION_METRIC_NAME


def test_conversation_metric_carries_the_routed_judge_model(mocker):
    """The judge model follows decode's provider via the shared resolver (ADR-0017 §7)."""
    mocker.patch.object(online.settings, "eval_judge_model", "")
    mocker.patch.object(online.settings, "llm_provider", "gemini")

    metric = online.make_conversation_metric()

    assert metric._model.model_name == "gemini/gemini-2.5-flash"


# --- the input/output transforms coerce every recorded trace shape to a string ---------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain string", "plain string"),
        ({"output": "the answer"}, "the answer"),
        ({"content": "hi there"}, "hi there"),
        ({"input": "a question"}, "a question"),
    ],
)
def test_stringify_prefers_text_bearing_shapes(value, expected):
    assert online._stringify(value) == expected


def test_stringify_falls_back_to_json_for_an_opaque_dict():
    out = online._stringify({"steps": 3, "tool": "bash"})

    assert '"steps": 3' in out and '"tool": "bash"' in out


def test_transforms_delegate_to_stringify():
    assert online.trace_input_transform({"input": "q"}) == "q"
    assert online.trace_output_transform("a") == "a"


# --- run_online_eval wiring: evaluate_threads called with the right args (opik mocked) --------------


def test_run_online_eval_passes_live_project_and_single_metric(mocker, with_keys):
    mocker.patch.object(online.settings, "opik_project_name", "decode-dev")
    fake_result = SimpleNamespace(results=[])
    fake_evaluate = mocker.patch("opik.evaluation.evaluate_threads", return_value=fake_result)

    result = online.run_online_eval(filter_string='status = "inactive"')

    assert result is fake_result
    kwargs = fake_evaluate.call_args.kwargs
    assert kwargs["project_name"] == "decode-dev"  # the LIVE project
    assert kwargs["eval_project_name"] is None  # scores logged back on the live threads
    assert kwargs["filter_string"] == 'status = "inactive"'
    assert len(kwargs["metrics"]) == 1
    assert isinstance(kwargs["metrics"][0], ConversationThreadMetric)
    # both transforms are required callables in opik 1.9.8's signature
    assert kwargs["trace_input_transform"]({"input": "q"}) == "q"
    assert kwargs["trace_output_transform"]("a") == "a"


def test_run_online_eval_defaults_filter_to_none(mocker, with_keys):
    fake_evaluate = mocker.patch(
        "opik.evaluation.evaluate_threads", return_value=SimpleNamespace(results=[])
    )

    online.run_online_eval()

    assert fake_evaluate.call_args.kwargs["filter_string"] is None


# --- the score formatter renders per-thread lines (pure, no run) -----------------------------------


def _score(name, value, *, failed=False, reason=None):
    return SimpleNamespace(name=name, value=value, scoring_failed=failed, reason=reason)


def test_format_thread_scores_renders_one_line_per_thread():
    result = SimpleNamespace(
        results=[
            SimpleNamespace(thread_id="sess-1", scores=[_score("conversation_coherence", 0.875)]),
            SimpleNamespace(thread_id="sess-2", scores=[_score("conversation_coherence", 0.5)]),
        ]
    )

    lines = online.format_thread_scores(result)

    assert lines == [
        "sess-1: conversation_coherence=0.88",
        "sess-2: conversation_coherence=0.50",
    ]


def test_format_thread_scores_marks_a_threadless_score_and_a_failed_score():
    result = SimpleNamespace(
        results=[
            SimpleNamespace(thread_id="sess-empty", scores=[]),
            SimpleNamespace(
                thread_id="sess-fail",
                scores=[_score("conversation_coherence", 0.0, failed=True, reason="judge error")],
            ),
        ]
    )

    lines = online.format_thread_scores(result)

    assert lines[0] == "sess-empty: no scores"
    assert lines[1] == "sess-fail: conversation_coherence=failed (judge error)"
