"""Provider-routed LLM judges for the Langfuse evaluation harness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import litellm

from decode.config.settings import settings
from evals.harness.metrics import BaseMetric, ScoreResult

DEFAULT_GEMINI_JUDGE = "gemini/gemini-3.8-flash"
MODAL_PROXY_PLACEHOLDER_API_KEY = "modal-proxy"
MODAL_JUDGE_TIMEOUT_S = 300.0
MODAL_JUDGE_EXTRA_BODY: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}


@dataclass(frozen=True)
class JudgeRoute:
    model: str
    completion_kwargs: dict[str, Any]


def judge_provider() -> str:
    """Return the judge provider, defaulting to the agent provider."""
    return settings.eval_judge_provider or settings.llm_provider


def judge_model() -> str:
    """Resolve the model string for the selected judge provider."""
    override = settings.eval_judge_model.strip()
    if override:
        return override
    provider = judge_provider()
    if provider == "openrouter":
        model = settings.openrouter_model
        return model if model.startswith("openrouter/") else f"openrouter/{model}"
    if provider == "modal":
        model = settings.modal_endpoint_model
        return model if model.startswith("openai/") else f"openai/{model}"
    return DEFAULT_GEMINI_JUDGE


def resolve_judge_model() -> JudgeRoute:
    """Resolve the model and provider-specific request settings without making a network call."""
    model = judge_model()
    if judge_provider() != "modal":
        return JudgeRoute(model=model, completion_kwargs={})

    completion_kwargs: dict[str, Any] = {
        "api_base": f"{settings.modal_endpoint_url.rstrip('/')}/v1",
        "api_key": MODAL_PROXY_PLACEHOLDER_API_KEY,
        "timeout": MODAL_JUDGE_TIMEOUT_S,
        "extra_body": MODAL_JUDGE_EXTRA_BODY,
    }
    token_id = settings.modal_proxy_token_id.get_secret_value()
    token_secret = settings.modal_proxy_token_secret.get_secret_value()
    if token_id and token_secret:
        completion_kwargs["extra_headers"] = {
            "Modal-Key": token_id,
            "Modal-Secret": token_secret,
        }
    return JudgeRoute(model=model, completion_kwargs=completion_kwargs)


class LLMJudgeMetric(BaseMetric):
    """Run one rubric judge and return a normalized score plus its explanation."""

    def __init__(
        self,
        *,
        task_introduction: str,
        evaluation_criteria: str,
        name: str = "llm_judge",
    ) -> None:
        super().__init__(name=name)
        self.task_introduction = task_introduction
        self.evaluation_criteria = evaluation_criteria
        self.route = resolve_judge_model()

    def score(
        self,
        *,
        input: Any = None,
        output: Any = None,
        expected_output: Any = None,
        **ignored_kwargs: Any,
    ) -> ScoreResult:
        request = {
            "input": input,
            "output": output,
            "expected_output": expected_output,
        }
        response = litellm.completion(
            model=self.route.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"{self.task_introduction}\n\n"
                        f"Evaluation criteria:\n{self.evaluation_criteria}\n\n"
                        'Return only a JSON object with "score" (a number from 0 to 1) '
                        'and "reason" (a short explanation).'
                    ),
                },
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            **self.route.completion_kwargs,
        )
        content = response.choices[0].message.content
        if not isinstance(content, str):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason="judge returned no text",
                scoring_failed=True,
            )
        try:
            result = json.loads(content)
            value = float(result["score"])
            reason = str(result["reason"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason="judge returned an invalid score object",
                scoring_failed=True,
            )
        if not 0.0 <= value <= 1.0:
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason="judge score was outside the range 0 to 1",
                scoring_failed=True,
            )
        return ScoreResult(name=self.name, value=value, reason=reason)


def make_judge(task_introduction: str, evaluation_criteria: str) -> LLMJudgeMetric:
    """Build a rubric judge; construction is network-free."""
    return LLMJudgeMetric(
        task_introduction=task_introduction,
        evaluation_criteria=evaluation_criteria,
    )
