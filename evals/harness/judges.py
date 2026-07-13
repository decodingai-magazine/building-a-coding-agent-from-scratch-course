"""The G-Eval judge factory — LLM judges for what code cannot score (ADR-0017 §7; task 104).

Judges are reserved for quality / groundedness / minimal-diff style grading; everything mechanical
stays a :class:`~opik.evaluation.metrics.base_metric.BaseMetric` in ``metrics.py``. One factory,
:func:`make_judge`, builds an Opik :class:`~opik.evaluation.metrics.GEval` whose judge model follows
decode's own provider (ADR-0017 §7): :func:`judge_model` resolves the LiteLLM model string —
``EVAL_JUDGE_MODEL`` overrides, else the string derives from ``settings.llm_provider``.

:func:`judge_model` is pure and network-free, so its routing is unit-tested without keys. The one
wrinkle is the ``modal`` route: its OpenAI-compatible endpoint needs a per-user ``base_url``, which a
bare LiteLLM model string cannot carry — GEval 1.9.8 takes only ``model`` (a string) or a pre-built
Opik model. :func:`make_judge` therefore hands GEval a :class:`LiteLLMChatModel` with ``api_base``
set for the modal derivation, and the plain string for every other route.
"""

from __future__ import annotations

from opik.evaluation.metrics import GEval
from opik.evaluation.models.litellm.litellm_chat_model import LiteLLMChatModel

from decode.config.settings import settings

# The fixed default judge — decode's gemini provider maps here regardless of ``settings.gemini_model``
# (ADR-0017 §7): a small, cheap, capable judge model, pinned so eval scores stay comparable.
DEFAULT_GEMINI_JUDGE = "gemini/gemini-2.5-flash"


def judge_model() -> str:
    """Resolve the LiteLLM model string the G-Eval judge runs on (ADR-0017 §7).

    An explicit ``settings.eval_judge_model`` (``EVAL_JUDGE_MODEL``) wins verbatim (whitespace-only
    is treated as unset). Otherwise the string derives from ``settings.llm_provider``: ``gemini`` →
    :data:`DEFAULT_GEMINI_JUDGE`; ``openrouter`` → ``openrouter/<settings.openrouter_model>``;
    ``modal`` → ``openai/<settings.modal_endpoint_model>`` (the endpoint ``base_url`` is applied
    separately, in :func:`make_judge`). Pure and network-free — safe to unit-test without keys.
    """
    override = settings.eval_judge_model.strip()
    if override:
        return override
    provider = settings.llm_provider
    if provider == "openrouter":
        return f"openrouter/{settings.openrouter_model}"
    if provider == "modal":
        return f"openai/{settings.modal_endpoint_model}"
    return DEFAULT_GEMINI_JUDGE


def make_judge(task_introduction: str, evaluation_criteria: str) -> GEval:
    """Build a G-Eval judge carrying the model :func:`judge_model` resolved (ADR-0017 §7).

    ``task_introduction`` frames what the judge is grading; ``evaluation_criteria`` is the rubric.
    For every route but ``modal`` the resolved string goes straight to ``GEval(model=...)``. The
    ``modal`` derivation instead gets a :class:`LiteLLMChatModel` pre-built with ``api_base`` pointed
    at ``{settings.modal_endpoint_url}/v1`` (the OpenAI-compatible route), because GEval's string
    surface cannot carry a base URL. Construction makes no LLM call.
    """
    model_string = judge_model()
    model: str | LiteLLMChatModel = model_string
    if not settings.eval_judge_model.strip() and settings.llm_provider == "modal":
        model = LiteLLMChatModel(
            model_name=model_string,
            api_base=f"{settings.modal_endpoint_url}/v1",
        )
    return GEval(
        task_introduction=task_introduction,
        evaluation_criteria=evaluation_criteria,
        model=model,
    )
