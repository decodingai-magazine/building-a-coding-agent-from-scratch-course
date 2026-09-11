"""Offline unit tests for the G-Eval judge factory (ADR-0017 §7; task 104).

``judge_model()`` is a pure LiteLLM-string resolver: the explicit ``EVAL_JUDGE_MODEL`` override
wins, else the string derives from ``settings.llm_provider`` (the three provider routes). Each route
is asserted without a network call. ``make_judge`` construction is smoke-tested — the returned
``GEval`` must carry the resolved model string on its LiteLLM model — again with no LLM call.
"""

from __future__ import annotations

from opik.evaluation.metrics import GEval
from opik.evaluation.models.litellm.litellm_chat_model import LiteLLMChatModel
from pydantic import SecretStr

from evals.harness import judges
from evals.harness.judges import DEFAULT_GEMINI_JUDGE


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


def test_resolve_judge_model_is_a_plain_string_off_the_modal_route(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")

    assert judges.resolve_judge_model() == "gemini/gemini-2.5-flash"


def test_resolve_judge_model_carries_the_base_url_on_the_modal_route(mocker) -> None:
    """The modal derivation returns a base-URL-bearing LiteLLMChatModel a bare string can't carry."""
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://modal.example")

    model = judges.resolve_judge_model()

    assert isinstance(model, LiteLLMChatModel)
    assert model.model_name == "openai/Qwen/Qwen3"


def test_resolve_judge_model_keeps_an_explicit_override_a_plain_string_on_modal(mocker) -> None:
    """An explicit EVAL_JUDGE_MODEL owns its own routing — no base-URL wrapping is applied."""
    mocker.patch.object(judges.settings, "eval_judge_model", "openrouter/anthropic/claude")
    mocker.patch.object(judges.settings, "llm_provider", "modal")

    assert judges.resolve_judge_model() == "openrouter/anthropic/claude"


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


def test_modal_route_carries_the_proxy_headers_and_a_placeholder_key(mocker) -> None:
    """Both proxy tokens set → dual Modal-Key / Modal-Secret headers plus a non-empty api_key.

    Mirrors :func:`decode.agent.factory._build_model`: the Modal Auto Endpoint authenticates on the
    headers, while litellm's openai route refuses to build a request without a non-empty api_key.
    """
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr("wk-test-id"))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr("ws-test-secret"))

    model = judges.resolve_judge_model()

    assert isinstance(model, LiteLLMChatModel)
    kwargs = model._completion_kwargs
    assert kwargs["api_base"] == "https://user--endpoint.modal.run/v1"
    assert kwargs["api_key"] == judges.MODAL_PROXY_PLACEHOLDER_API_KEY
    assert kwargs["api_key"]  # litellm's openai route rejects an empty key outright
    assert kwargs["extra_headers"] == {
        "Modal-Key": "wk-test-id",
        "Modal-Secret": "ws-test-secret",
    }


def test_modal_route_sends_no_headers_on_an_unauthenticated_endpoint(mocker) -> None:
    """No proxy tokens → an ``--unauthenticated`` endpoint: no Modal headers, key still non-empty."""
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr(""))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr(""))

    model = judges.resolve_judge_model()

    assert isinstance(model, LiteLLMChatModel)
    assert "extra_headers" not in model._completion_kwargs
    assert model._completion_kwargs["api_key"] == judges.MODAL_PROXY_PLACEHOLDER_API_KEY


def test_modal_route_appends_v1_to_the_endpoint_url_verbatim(mocker) -> None:
    """``MODAL_ENDPOINT_URL`` is a BARE base by contract — every consumer appends ``/v1``.

    A value that already carries the suffix is a misconfiguration, and one that breaks
    :func:`decode.agent.factory._build_model` identically; the judge route deliberately does not
    paper over it, so a bad url fails the same way everywhere.
    """
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr(""))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr(""))

    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://bare.modal.run")
    bare = judges.resolve_judge_model()
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://suffixed.modal.run/v1")
    suffixed = judges.resolve_judge_model()

    assert isinstance(bare, LiteLLMChatModel)
    assert isinstance(suffixed, LiteLLMChatModel)
    assert bare._completion_kwargs["api_base"] == "https://bare.modal.run/v1"
    assert suffixed._completion_kwargs["api_base"] == "https://suffixed.modal.run/v1/v1"


def test_gemini_route_carries_no_modal_auth(mocker) -> None:
    """The gemini derivation stays a plain string — no api_key, no headers, no base url."""
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr("wk-test-id"))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr("ws-test-secret"))

    assert judges.resolve_judge_model() == DEFAULT_GEMINI_JUDGE


def test_modal_route_sends_no_headers_when_only_one_proxy_token_is_set(mocker) -> None:
    """Half-configured proxy tokens send NO headers — both-or-neither, exactly like the factory.

    ``decode.agent.factory`` leans on the cli guard for both-or-neither, and ``python -m evals``
    never passes through that guard, so the judge route pins the rule itself: one token alone is not
    a credential, and a lone ``Modal-Key`` would be rejected by the endpoint anyway.
    """
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")

    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr("wk-test-id"))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr(""))
    id_only = judges.resolve_judge_model()
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr(""))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr("ws-test-secret"))
    secret_only = judges.resolve_judge_model()

    assert isinstance(id_only, LiteLLMChatModel)
    assert isinstance(secret_only, LiteLLMChatModel)
    assert "extra_headers" not in id_only._completion_kwargs
    assert "extra_headers" not in secret_only._completion_kwargs
    assert id_only._completion_kwargs["api_key"] == judges.MODAL_PROXY_PLACEHOLDER_API_KEY
    assert secret_only._completion_kwargs["api_key"] == judges.MODAL_PROXY_PLACEHOLDER_API_KEY
