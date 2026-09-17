"""The G-Eval judge factory — LLM judges for what code cannot score (ADR-0017 §7; tasks 104, 170).

Judges are reserved for quality / groundedness / minimal-diff style grading; everything mechanical
stays a :class:`~opik.evaluation.metrics.base_metric.BaseMetric` in ``metrics.py``. One factory,
:func:`make_judge`, builds an Opik :class:`~opik.evaluation.metrics.GEval` on the model
:func:`resolve_judge_model` resolves.

**The judge's provider is its own** (ADR-0022 §7). :func:`judge_provider` is ``EVAL_JUDGE_PROVIDER``
or, when that is empty, the agent's ``settings.llm_provider`` — so the default is unchanged, and
grading a modal-served agent with a gemini judge (or the reverse) is one env var.
:func:`judge_model` then routes on THAT provider, with ``EVAL_JUDGE_MODEL`` overriding the model
string on whichever route is in force. Both are pure and network-free, so the routing is unit-tested
without keys.

The ``modal`` route is the one that needs an object rather than a string: its OpenAI-compatible
endpoint needs a per-user ``base_url`` AND proxy-token auth, neither of which a bare LiteLLM model
string can carry (GEval takes only ``model`` — a string — or a pre-built Opik model). Two more
things are true of the Modal Auto Endpoint decode serves (SGLang + DFLASH speculative decoding), and
both were found by running the judge against it, so :class:`ModalJudgeModel` carries the fixes:

* **No logprobs.** G-Eval asks for ``logprobs=True, top_logprobs=20`` whenever the model claims to
  support them (``g_eval/metric.py::_init_model``), and litellm advertises both for the openai
  route — but the server answers ``DFLASH speculative decoding does not support return_logprob
  yet``. :attr:`ModalJudgeModel.supported_params` hides exactly those two, so G-Eval takes its
  non-logprob parse path (it reads the score out of the ``response_format`` JSON instead of
  weighting it by token probabilities). Scores from a logprob judge and a non-logprob judge are
  therefore NOT directly comparable — compare a modal judge only against itself.
* **No thinking.** Qwen3.6's thinking mode turns the chain-of-thought call into a multi-minute one
  that blows ``LiteLLMChatModel``'s 60 s default read timeout. :data:`MODAL_JUDGE_EXTRA_BODY`
  switches it off through SGLang's ``chat_template_kwargs`` (verified live: the same prompt answers
  in 2 completion tokens with no ``reasoning_content``, against 115 with thinking on), and
  :data:`MODAL_JUDGE_TIMEOUT_S` still leaves 300 s of room for a long rubric.
"""

from __future__ import annotations

from typing import Any

from opik.evaluation.metrics import GEval
from opik.evaluation.models.litellm.litellm_chat_model import LiteLLMChatModel

from decode.config.settings import settings

# The fixed default judge — a gemini judge provider maps here regardless of ``settings.gemini_model``
# (ADR-0017 §7): a small, cheap, capable judge model, pinned so eval scores stay comparable. Re-pinned
# from ``gemini-2.5-flash`` when Google retired it for new keys (the API answers 404 "no longer
# available to new users"), onto the same flash generation the agent defaults to
# (``settings.gemini_model``); a re-pin resets the comparability window.
DEFAULT_GEMINI_JUDGE = "gemini/gemini-3.8-flash"

# The modal route's ``api_key``. The Modal Auto Endpoint authenticates on the Modal-Key/Modal-Secret
# proxy headers and IGNORES the Bearer token, but litellm's openai route refuses to build a request
# without a non-empty key ("Missing credentials … set OPENAI_API_KEY"). A placeholder satisfies that
# check without copying a secret into a second place — the real auth rides the headers.
MODAL_PROXY_PLACEHOLDER_API_KEY = "modal-proxy"

# The modal judge's read timeout, in SECONDS — deliberately a float, not the ``httpx.Timeout`` opik
# builds for its own 60 s default. Opik injects that per call (``generate_provider_response`` does
# ``all_kwargs.setdefault("timeout", …)``), so it never lands in ``_completion_kwargs``; ours would,
# and ``GEval._model_cache_fingerprint`` freezes that dict into the chain-of-thought CACHE KEY —
# ``httpx.Timeout`` is unhashable, so the first ``score()`` would die with ``TypeError`` before a
# request ever left the process. 300 s is roomy for a cold endpoint plus a long rubric.
MODAL_JUDGE_TIMEOUT_S = 300.0

# SGLang's per-request switch for Qwen3.6's thinking mode, forwarded by litellm as ``extra_body``.
# Off, because a thinking judge spends minutes (and hundreds of tokens) restating the rubric before
# the score that is the only part G-Eval parses.
MODAL_JUDGE_EXTRA_BODY: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}

# The two G-Eval asks for when a model claims them, and the two DFLASH speculative decoding rejects.
_LOGPROB_PARAMS = frozenset({"logprobs", "top_logprobs"})


class ModalJudgeModel(LiteLLMChatModel):
    """A LiteLLM judge model that does not claim the logprob params (task 170).

    The ONLY difference from its parent, and the whole reason the subclass exists: litellm
    advertises ``logprobs``/``top_logprobs`` for the openai route the Modal Auto Endpoint speaks, so
    ``GEval._init_model`` would set ``_log_probs_supported`` and every scoring call would ask for
    them — and the endpoint answers ``DFLASH speculative decoding does not support return_logprob
    yet``. Hiding the two here flips that one flag at construction, with no fork of G-Eval.
    """

    @property
    def supported_params(self) -> set[str]:
        """The parent's advertised params MINUS the two the endpoint cannot serve.

        A ``property`` over the parent's ``cached_property`` is deliberate: a data descriptor on the
        subclass shadows the value the parent caches in the instance ``__dict__``, so the subtraction
        cannot be bypassed while the cached lookup underneath still happens exactly once.
        """
        return super().supported_params - _LOGPROB_PARAMS


def judge_provider() -> str:
    """The provider the JUDGE runs on — its own knob, defaulting to the agent's (ADR-0022 §7).

    ``settings.eval_judge_provider`` (``EVAL_JUDGE_PROVIDER``) when set, else the agent's
    ``settings.llm_provider``. Empty is the shipped default, so a checkout that never sets it keeps
    the historical behaviour: the judge follows decode's own provider. Pure and network-free.
    """
    return settings.eval_judge_provider or settings.llm_provider


def judge_model() -> str:
    """Resolve the LiteLLM model string the G-Eval judge runs on (ADR-0017 §7; ADR-0022 §7).

    An explicit ``settings.eval_judge_model`` (``EVAL_JUDGE_MODEL``) wins verbatim (whitespace-only
    is treated as unset) — it names the MODEL on whatever route :func:`judge_provider` selected, so
    the two knobs compose instead of fighting. Otherwise the string derives from
    :func:`judge_provider`: ``gemini`` → :data:`DEFAULT_GEMINI_JUDGE`; ``openrouter`` →
    ``openrouter/<settings.openrouter_model>``; ``modal`` → ``openai/<settings.modal_endpoint_model>``
    (the endpoint ``base_url`` and auth are applied separately, in :func:`resolve_judge_model`). Pure
    and network-free — safe to unit-test without keys.
    """
    override = settings.eval_judge_model.strip()
    if override:
        return override
    provider = judge_provider()
    if provider == "openrouter":
        return f"openrouter/{settings.openrouter_model}"
    if provider == "modal":
        return f"openai/{settings.modal_endpoint_model}"
    return DEFAULT_GEMINI_JUDGE


def resolve_judge_model() -> str | LiteLLMChatModel:
    """The judge model object every Opik metric factory feeds to its ``model=`` slot (ADR-0017 §7).

    For every route but ``modal`` this is just the :func:`judge_model` string. A ``modal`` judge
    instead gets a :class:`ModalJudgeModel` pre-built with ``api_base`` pointed at
    ``{settings.modal_endpoint_url}/v1`` (the OpenAI-compatible route — the setting is a BARE base by
    contract, so every consumer appends the suffix) plus the endpoint's auth, because a bare LiteLLM
    string can carry neither. Auth mirrors :func:`decode.agent.factory._build_model`: the dual
    ``Modal-Key`` / ``Modal-Secret`` proxy-token headers when BOTH are set, none at all for an
    ``--unauthenticated`` endpoint — over a constant :data:`MODAL_PROXY_PLACEHOLDER_API_KEY` litellm
    needs non-empty either way. It also carries the endpoint's two workarounds
    (:data:`MODAL_JUDGE_TIMEOUT_S`, :data:`MODAL_JUDGE_EXTRA_BODY` — see the module docstring). The
    kwargs reach ``litellm.completion`` verbatim (``LiteLLMChatModel`` merges ``_completion_kwargs``
    into every call). Used by :func:`make_judge` (the G-Eval trace judge) so the modal wrinkle lives in one
    place. Construction makes no LLM call.

    An ``EVAL_JUDGE_MODEL`` override does NOT demote this route to a plain string: on ``modal`` it
    replaces the model INSIDE the authenticated object (it names another model the same endpoint
    serves). Changing route is ``EVAL_JUDGE_PROVIDER``'s job — an override that silently dropped the
    base url and the proxy headers would 401 instead.
    """
    model_string = judge_model()
    if judge_provider() != "modal":
        return model_string

    completion_kwargs: dict[str, Any] = {
        "api_base": f"{settings.modal_endpoint_url}/v1",
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
    return ModalJudgeModel(model_name=model_string, **completion_kwargs)


def make_judge(task_introduction: str, evaluation_criteria: str) -> GEval:
    """Build a G-Eval judge carrying the model :func:`resolve_judge_model` resolved (ADR-0017 §7).

    ``task_introduction`` frames what the judge is grading; ``evaluation_criteria`` is the rubric.
    The model comes from :func:`resolve_judge_model` (a plain string, or a :class:`ModalJudgeModel`
    on the ``modal`` judge route). Construction makes no LLM call.
    """
    return GEval(
        task_introduction=task_introduction,
        evaluation_criteria=evaluation_criteria,
        model=resolve_judge_model(),
    )
