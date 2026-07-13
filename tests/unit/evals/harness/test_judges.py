"""Offline unit tests for the G-Eval judge factory (ADR-0017 §7; task 104).

``judge_model()`` is a pure LiteLLM-string resolver: the explicit ``EVAL_JUDGE_MODEL`` override
wins, else the string derives from ``settings.llm_provider`` (the three provider routes). Each route
is asserted without a network call. ``make_judge`` construction is smoke-tested — the returned
``GEval`` must carry the resolved model string on its LiteLLM model — again with no LLM call.
"""

from __future__ import annotations

from opik.evaluation.metrics import GEval

from evals.harness import judges


def test_explicit_override_wins(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "openrouter/anthropic/claude")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")
    assert judges.judge_model() == "openrouter/anthropic/claude"


def test_gemini_route_is_the_default(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")
    assert judges.judge_model() == "gemini/gemini-2.5-flash"


def test_whitespace_override_falls_back_to_provider(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "   ")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")
    assert judges.judge_model() == "gemini/gemini-2.5-flash"


def test_openrouter_route(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "openrouter")
    mocker.patch.object(judges.settings, "openrouter_model", "meta/llama-3")
    assert judges.judge_model() == "openrouter/meta/llama-3"


def test_modal_route(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    assert judges.judge_model() == "openai/Qwen/Qwen3"


def test_make_judge_carries_resolved_model_string(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")

    judge = judges.make_judge(
        task_introduction="Judge the assistant's answer.",
        evaluation_criteria="Is the answer factually correct and grounded?",
    )

    assert isinstance(judge, GEval)
    assert judge.task_introduction == "Judge the assistant's answer."
    assert judge.evaluation_criteria == "Is the answer factually correct and grounded?"
    assert judge._model.model_name == "gemini/gemini-2.5-flash"


def test_make_judge_wires_modal_base_url(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")

    judge = judges.make_judge(
        task_introduction="Judge.",
        evaluation_criteria="Correct?",
    )

    assert judge._model.model_name == "openai/Qwen/Qwen3"
    assert judge._model._completion_kwargs["api_base"] == "https://user--endpoint.modal.run/v1"
