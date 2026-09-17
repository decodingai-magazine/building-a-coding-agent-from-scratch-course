"""Offline unit tests for the G-Eval judge factory (ADR-0017 §7; tasks 104, 170).

``judge_model()`` is a pure LiteLLM-string resolver: the explicit ``EVAL_JUDGE_MODEL`` override
wins the model STRING, else the string derives from :func:`~evals.harness.judges.judge_provider`
(``EVAL_JUDGE_PROVIDER`` or, empty, the agent's ``llm_provider``). Each route is asserted without a
network call. ``make_judge`` construction is smoke-tested — the returned ``GEval`` must carry the
resolved model on its LiteLLM model — again with no LLM call.

The ``modal`` route carries the two live workarounds the Modal Auto Endpoint forced (task 170):
``ModalJudgeModel`` hides ``logprobs``/``top_logprobs`` so G-Eval takes its non-logprob parse path,
and the completion kwargs switch Qwen's thinking off under a 300 s timeout. Both are pinned here.
"""

from __future__ import annotations

import pytest
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
    assert judges.judge_model() == "gemini/gemini-3.8-flash"


def test_whitespace_override_falls_back_to_provider(mocker) -> None:
    mocker.patch.object(judges.settings, "eval_judge_model", "   ")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")
    assert judges.judge_model() == "gemini/gemini-3.8-flash"


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

    assert judges.resolve_judge_model() == "gemini/gemini-3.8-flash"


def test_resolve_judge_model_carries_the_base_url_on_the_modal_route(mocker) -> None:
    """The modal derivation returns a base-URL-bearing LiteLLMChatModel a bare string can't carry."""
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://modal.example")

    model = judges.resolve_judge_model()

    assert isinstance(model, LiteLLMChatModel)
    assert model.model_name == "openai/Qwen/Qwen3"


def test_an_explicit_override_stays_a_plain_string_off_the_modal_route(mocker) -> None:
    """Off the modal route an EVAL_JUDGE_MODEL override needs no wrapping — it is just the string."""
    mocker.patch.object(judges.settings, "eval_judge_model", "openrouter/anthropic/claude")
    mocker.patch.object(judges.settings, "eval_judge_provider", "")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")

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
    assert judge._model.model_name == "gemini/gemini-3.8-flash"


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


# --- judge_provider(): the judge's provider is selectable independently of the agent's (task 170) ---


def test_judge_provider_follows_the_agent_provider_when_unset(mocker) -> None:
    """Empty ``EVAL_JUDGE_PROVIDER`` = today's behaviour: the judge rides decode's own provider."""
    mocker.patch.object(judges.settings, "eval_judge_provider", "")
    mocker.patch.object(judges.settings, "llm_provider", "openrouter")

    assert judges.judge_provider() == "openrouter"


def test_judge_provider_overrides_the_agent_provider(mocker) -> None:
    """A modal-served AGENT graded by a gemini judge is one env var, not a code change."""
    mocker.patch.object(judges.settings, "eval_judge_provider", "gemini")
    mocker.patch.object(judges.settings, "llm_provider", "modal")

    assert judges.judge_provider() == "gemini"


@pytest.mark.parametrize(
    ("judge_provider_value", "expected"),
    [
        ("", "openai/Qwen/Qwen3"),  # follows the modal agent
        ("gemini", DEFAULT_GEMINI_JUDGE),
        ("openrouter", "openrouter/meta/llama-3"),
        ("modal", "openai/Qwen/Qwen3"),
    ],
)
def test_judge_model_routes_on_the_judge_provider(
    mocker, judge_provider_value: str, expected: str
) -> None:
    """The whole routing matrix off ONE agent provider (``modal``) — the judge routes on its own."""
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "eval_judge_provider", judge_provider_value)
    mocker.patch.object(judges.settings, "openrouter_model", "meta/llama-3")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")

    assert judges.judge_model() == expected


@pytest.mark.parametrize("judge_provider_value", ["", "gemini", "openrouter", "modal"])
def test_the_model_override_wins_on_every_judge_provider(mocker, judge_provider_value: str) -> None:
    """``EVAL_JUDGE_MODEL`` still owns the model STRING on every route (the two knobs compose)."""
    mocker.patch.object(judges.settings, "eval_judge_model", "openai/Qwen/Qwen-Other")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")
    mocker.patch.object(judges.settings, "eval_judge_provider", judge_provider_value)
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")

    assert judges.judge_model() == "openai/Qwen/Qwen-Other"


def test_a_gemini_judge_on_a_modal_agent_is_a_plain_string(mocker) -> None:
    """Judge gemini + agent modal: no base url, no proxy headers — a bare LiteLLM string."""
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "eval_judge_provider", "gemini")

    assert judges.resolve_judge_model() == DEFAULT_GEMINI_JUDGE


def test_a_modal_judge_on_a_gemini_agent_is_the_authenticated_modal_model(mocker) -> None:
    """Judge modal + agent gemini: the judge still gets the endpoint's base url and auth."""
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "gemini")
    mocker.patch.object(judges.settings, "eval_judge_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr("wk-test-id"))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr("ws-test-secret"))

    model = judges.resolve_judge_model()

    assert isinstance(model, judges.ModalJudgeModel)
    assert model.model_name == "openai/Qwen/Qwen3"
    assert model._completion_kwargs["api_base"] == "https://user--endpoint.modal.run/v1"
    assert model._completion_kwargs["extra_headers"] == {
        "Modal-Key": "wk-test-id",
        "Modal-Secret": "ws-test-secret",
    }


# --- ModalJudgeModel: the two live blockers on the SGLang Auto Endpoint (task 170) ------------------


def test_modal_judge_model_hides_the_logprob_params(mocker) -> None:
    """Blocker (a): DFLASH speculative decoding answers 400 on ``return_logprob``.

    litellm advertises ``logprobs``/``top_logprobs`` for the openai route, and G-Eval asks for them
    whenever the model claims them — so the judge model drops exactly those two and keeps the rest.
    """
    model = judges.ModalJudgeModel(model_name="openai/Qwen/Qwen3", api_key="k")

    params = model.supported_params

    assert "logprobs" not in params
    assert "top_logprobs" not in params
    # Everything else survives — ``response_format`` is what G-Eval parses the score out of.
    assert "response_format" in params
    assert "temperature" in params


def test_geval_on_the_modal_judge_takes_the_non_logprob_parse_path(mocker) -> None:
    """The reason the property exists: ``GEval._init_model`` gates its logprob path on it."""
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "eval_judge_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")

    judge = judges.make_judge("Judge.", "Correct?")

    assert judge._log_probs_supported is False


def test_modal_judge_carries_the_timeout_and_the_thinking_switch(mocker) -> None:
    """Blocker (b): Qwen3.6 thinking blows opik's 60 s read timeout — so: off, and 300 s of room."""
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "eval_judge_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")

    model = judges.resolve_judge_model()

    assert isinstance(model, judges.ModalJudgeModel)
    kwargs = model._completion_kwargs
    assert kwargs["timeout"] == judges.MODAL_JUDGE_TIMEOUT_S == 300.0
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_the_modal_judges_completion_kwargs_stay_hashable(mocker) -> None:
    """Every constructor kwarg lands in G-Eval's chain-of-thought CACHE KEY — it must hash.

    ``GEval._model_cache_fingerprint`` freezes ``_completion_kwargs`` into a tuple used as a dict
    key, and ``_freeze_for_cache`` passes anything that is not a dict/list/set through VERBATIM. An
    ``httpx.Timeout`` (opik's own default shape, injected per-call so it never reaches this dict) is
    unhashable, so the judge's timeout is a plain float — the first ``score()`` would otherwise die
    with ``TypeError: unhashable type`` before a single request left the process.
    """
    mocker.patch.object(judges.settings, "eval_judge_model", "")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "eval_judge_provider", "modal")
    mocker.patch.object(judges.settings, "modal_endpoint_model", "Qwen/Qwen3")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr("wk-test-id"))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr("ws-test-secret"))

    judge = judges.make_judge("Judge.", "Correct?")

    hash(judge._chain_of_thought_cache_key())  # raises TypeError if any kwarg is unhashable


def test_the_model_override_keeps_the_modal_auth(mocker) -> None:
    """On the modal route the override names the model the ENDPOINT serves, auth unchanged.

    Behaviour change (task 170): the override used to demote the modal route to a bare string,
    which silently dropped the base url and the proxy headers. ``EVAL_JUDGE_PROVIDER`` is now the
    lever for changing ROUTE; ``EVAL_JUDGE_MODEL`` only changes the model on the route in force.
    """
    mocker.patch.object(judges.settings, "eval_judge_model", "openai/Qwen/Qwen3-Other")
    mocker.patch.object(judges.settings, "llm_provider", "modal")
    mocker.patch.object(judges.settings, "eval_judge_provider", "")
    mocker.patch.object(judges.settings, "modal_endpoint_url", "https://user--endpoint.modal.run")
    mocker.patch.object(judges.settings, "modal_proxy_token_id", SecretStr("wk-test-id"))
    mocker.patch.object(judges.settings, "modal_proxy_token_secret", SecretStr("ws-test-secret"))

    model = judges.resolve_judge_model()

    assert isinstance(model, judges.ModalJudgeModel)
    assert model.model_name == "openai/Qwen/Qwen3-Other"
    assert model._completion_kwargs["api_base"] == "https://user--endpoint.modal.run/v1"
    assert model._completion_kwargs["extra_headers"] == {
        "Modal-Key": "wk-test-id",
        "Modal-Secret": "ws-test-secret",
    }
