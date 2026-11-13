"""Offline tests for local, provider-routed Langfuse evaluator adapters."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from evals.harness import judges
from evals.harness.metrics import ScoreResult


def test_judge_model_uses_explicit_override(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "openrouter/anthropic/claude")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")
    assert judges.judge_model() == "openrouter/anthropic/claude"


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("gemini", "gemini/gemini-3.8-flash"),
        ("openrouter", "openrouter/meta/llama-3"),
        ("modal", "openai/Qwen/Qwen3"),
    ],
)
def test_judge_model_uses_provider_default(mocker, provider: str, expected: str) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "eval_judge_provider", provider)
    mocker.patch.object(judges.settings, "llm_provider", "gemini")
    mocker.patch.object(judges.settings, "openrouter_model", "meta/llama-3")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    assert judges.judge_model() == expected


def test_unset_judge_provider_follows_agent_provider(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_provider", "")
    mocker.patch.object(judges.settings, "llm_provider", "openrouter")
    assert judges.judge_provider() == "openrouter"


def test_modal_route_includes_endpoint_and_optional_proxy_headers(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "eval_judge_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://endpoint.modal.run")
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr("wk-id"))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr("ws-secret"))

    route = judges.resolve_judge_model()

    assert route.model == "openai/Qwen/Qwen3"
    assert route.completion_kwargs["api_base"] == "https://endpoint.modal.run/v1"
    assert route.completion_kwargs["extra_headers"] == {
        "Modal-Key": "wk-id",
        "Modal-Secret": "ws-secret",
    }


def test_modal_route_sends_no_partial_proxy_auth(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "eval_judge_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://endpoint.modal.run")
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr("wk-id"))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr(""))

    route = judges.resolve_judge_model()

    assert "extra_headers" not in route.completion_kwargs
    assert route.completion_kwargs["api_key"] == judges.MODAL_PROXY_PLACEHOLDER_API_KEY


def test_make_judge_uses_local_score_contract(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "gemini/gemini-3.8-flash")

    judge = judges.make_judge("Judge the answer.", "Is it correct?")

    assert isinstance(judge, judges.LLMJudgeMetric)
    assert judge.task_introduction == "Judge the answer."
    assert judge.evaluation_criteria == "Is it correct?"
    assert judge.route.model == "gemini/gemini-3.8-flash"


def test_judge_scores_with_litellm_and_returns_local_score(mocker) -> None:
    completion = mocker.patch(
        "evals.harness.judges.litellm.completion",
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"score": 0.8, "reason": "good"}'))
            ]
        ),
    )
    judge = judges.make_judge("Judge.", "Check correctness.")

    score = judge.score(input="prompt", output="answer", expected_output="reference")

    assert score == ScoreResult(name="llm_judge", value=0.8, reason="good")
    assert completion.call_args.kwargs["model"] == judge.route.model


@pytest.mark.parametrize(
    "content",
    [
        None,
        "not json",
        '{"score": 1.2, "reason": "out of range"}',
    ],
)
def test_invalid_judge_responses_are_unscorable(mocker, content: str | None) -> None:
    mocker.patch(
        "evals.harness.judges.litellm.completion",
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        ),
    )

    score = judges.make_judge("Judge.", "Check.").score(input="x", output="y")

    assert score.value == 0.0
    assert score.scoring_failed
