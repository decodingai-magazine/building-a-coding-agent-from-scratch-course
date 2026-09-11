"""The ``response_quality`` **Online Rule**, created from the CLI (ADR-0022 §13).

An **Online Rule** (the glossary's term) is an LLM-as-judge the Opik *server* runs on every trace
as it arrives, attaching a feedback score you can then filter and chart. ``evals/README.md`` used
to ask a human to build it in the project UI — seven steps, a hand-typed prompt, and a live project (``decode-prod``)
that still had 0 rules and 68 traces, so ``feedback_scores.response_quality`` was empty and
``evals mine --preset low-quality`` had nothing to find. :func:`create_response_quality_rule` is
that walkthrough as one idempotent command.

Three properties worth naming:

* **Idempotent.** ``find_evaluators(project_id=…, name=…)`` first; an existing rule is reported and
  nothing is written. The backend's ``name`` query is a *substring* match, so the page is
  re-filtered for an EXACT :data:`RULE_NAME` before that call is believed.
* **The prompt is ONE constant** (:data:`RESPONSE_QUALITY_PROMPT`) the README quotes verbatim — the
  qualitative phrasing task 114 paid for, kept in one place so the doc cannot drift from the rule
  (``tests/unit/evals/harness/test_online_rule.py`` asserts the README still quotes it).
* **Keyless / opik-free until it runs.** ``opik`` is imported inside the functions that call it, so
  ``python -m evals online-rule create --help`` needs no key and no network (ADR-0017 §1).

The judge model is Opik's, not LiteLLM's: the rule runs server-side on a provider configured under
the workspace's **AI Providers**, so the model id carries no ``gemini/`` route prefix
(:func:`opik_judge_model`). Only the gemini route can be derived that way; the openrouter/modal
routes refuse and ask for an explicit ``--model``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from decode.config.settings import settings
from evals.harness.judges import judge_model, judge_provider

if TYPE_CHECKING:
    from opik.rest_api.types import AutomationRuleEvaluatorWrite_LlmAsJudge

logger = logging.getLogger(__name__)

# The rule's name AND the name of the feedback score it attaches — ``evals mine --preset
# low-quality`` filters on ``feedback_scores.response_quality``, so the two must be the same string.
RULE_NAME = "response_quality"

# The judge's output schema: one 0-10 integer. INTEGER (not DOUBLE) because the prompt asks for a
# 0-10 placement, and not BOOLEAN because a pass/fail collapses exactly the gradient we mine on.
SCORE_TYPE = "INTEGER"
SCORE_DESCRIPTION = "0-10 qualitative score: how well the answer resolves the request, grounded."

# The trace fields the rule's ``{{input}}`` / ``{{output}}`` placeholders read — the SAME two fields
# the scripted thread pass transforms (``evals.harness.online``), so the two judges see one story.
RULE_VARIABLES = {"input": "input", "output": "output"}

# The qualitative scoring prompt — the ONE copy; ``evals/README.md`` quotes it verbatim.
# Phrased as QUALITIES to look for, never as numeric verdicts: a "Score 1.0 if grounded, 0.0
# otherwise" instruction collides with the judge's own 0-10 output scale and produces incoherent
# scores (the task-114 lesson).
RESPONSE_QUALITY_PROMPT = """\
Judge how well the assistant's final answer addresses the user's request, grounded in the
files, tool outputs, and prompt it was given.

A high-scoring answer directly resolves what the user asked, cites only facts present in the
workspace or the prompt, and invents no file, function, or value. A low-scoring answer drifts
off the request, is vague, or asserts things nothing in the trace supports.

The user's request:
{{input}}

The assistant's answer:
{{output}}"""


class OnlineRuleError(Exception):
    """A rule cannot be built or the project cannot be resolved — surfaced as ONE CLI line."""


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """What one ``online-rule create`` did: ``created`` / ``exists`` / ``dry-run`` (ADR-0022 §13).

    ``rule_id`` is the evaluator's id — re-read after a create, because Opik's
    ``create_automation_rule_evaluator`` returns ``None`` (verified against the installed 2.2.36
    client), and ``None`` on a dry run. ``payload`` is the exact JSON body the create would send,
    so ``--dry-run`` can print what a real run would write.
    """

    action: str
    project: str
    model: str
    rule_id: str | None = None
    payload: str | None = None


def opik_judge_model(override: str | None = None) -> str:
    """The model id Opik's SERVER-side judge runs on (ADR-0022 §13).

    An explicit ``--model`` wins verbatim — the operator owns the routing then. Otherwise the id
    derives from the shared eval routing (:func:`evals.harness.judges.judge_model`, which follows the
    JUDGE's provider, ADR-0022 §7) and only the gemini spelling can be translated: an online rule
    names a model the Opik workspace has configured under **AI Providers**, so it carries no LiteLLM
    route prefix (``gemini/gemini-2.5-flash`` → ``gemini-2.5-flash``). Every other route
    (``openrouter/…``, ``openai/…`` for the modal endpoint) raises :class:`OnlineRuleError` asking for
    an explicit ``--model``, because guessing would create a rule that is accepted and then silently
    fails to score. A modal JUDGE refuses like any other: this rule runs inside Opik's server, not
    through litellm, so decode's endpoint and its proxy headers are no help here. The refusal names
    the judge's provider — blaming ``LLM_PROVIDER`` would send the operator to the wrong env var.
    """
    if override and override.strip():
        return override.strip()
    routed = judge_model()
    if routed.startswith("gemini/"):
        return routed.split("/", 1)[1]
    raise OnlineRuleError(
        f"cannot derive an Opik judge model from the {judge_provider()!r} judge route "
        f"({routed!r}) — "
        "pass --model <id> naming a model your Opik workspace has configured under AI Providers."
    )


def build_rule_request(
    *, project_id: str, model: str, sampling: float
) -> AutomationRuleEvaluatorWrite_LlmAsJudge:
    """Build the LLM-as-judge create payload for :data:`RULE_NAME` (ADR-0022 §13).

    Field spellings verified against the INSTALLED opik 2.2.36 write types, which beat the task
    text in two places: ``LlmAsJudgeCodeWrite``'s schema field is ``schema_`` (serialised under its
    ``schema`` alias — passing ``schema=`` raises a "Field required" ValidationError), and the base
    write model documents ``project_id`` as legacy with ``project_ids`` as "used when
    creating/updating rules", so BOTH are set. ``trigger_scope="production"`` scopes the rule to
    live traffic (not experiment traces); ``temperature=0.0`` keeps repeat scores comparable.
    """
    from opik.rest_api.types import (
        AutomationRuleEvaluatorWrite_LlmAsJudge,
        LlmAsJudgeCodeWrite,
        LlmAsJudgeMessageWrite,
        LlmAsJudgeModelParametersWrite,
        LlmAsJudgeOutputSchemaWrite,
    )

    return AutomationRuleEvaluatorWrite_LlmAsJudge(
        project_id=project_id,
        project_ids=[project_id],
        name=RULE_NAME,
        sampling_rate=sampling,
        enabled=True,
        action="evaluator",
        trigger_scope="production",
        code=LlmAsJudgeCodeWrite(
            model=LlmAsJudgeModelParametersWrite(name=model, temperature=0.0),
            messages=[LlmAsJudgeMessageWrite(role="USER", content=RESPONSE_QUALITY_PROMPT)],
            variables=dict(RULE_VARIABLES),
            schema_=[
                LlmAsJudgeOutputSchemaWrite(
                    name=RULE_NAME, type=SCORE_TYPE, description=SCORE_DESCRIPTION
                )
            ],
        ),
    )


def rule_payload(request: AutomationRuleEvaluatorWrite_LlmAsJudge) -> str:
    """The EXACT JSON body a create would POST, pretty-printed for ``--dry-run``.

    ``dict(by_alias=True)`` is the spelling that matches the wire: the model's own ``.json()``
    does NOT apply the ``schema_`` → ``schema`` alias and drops the ``type`` discriminator, so a
    rehearsal printed from it would misreport the body it is rehearsing.
    """
    import json

    return json.dumps(request.dict(by_alias=True), indent=2, sort_keys=True, default=str)


def resolve_project_id(rest_client: Any, project: str) -> str:
    """The Opik project id for ``project``, matched EXACTLY by name (ADR-0022 §13).

    ``projects.retrieve_project(name=…)`` (``POST v1/private/projects/retrieve``) is the exact-match,
    unpaginated lookup — one request, one project, ``404`` when there is none. The earlier
    ``find_projects(name=…)`` spelling was a PAGINATED SUBSTRING query whose first page was
    re-filtered here, so a workspace with enough projects containing ``decode`` could push the exact
    match off page one and report a project that exists as missing.

    A project that does not exist raises :class:`OnlineRuleError` — it is created by the first trace
    decode sends, not by this command. ``opik`` is imported here, not at module scope, so the CLI
    stays opik-free until it runs.
    """
    from opik.rest_api.errors import NotFoundError

    try:
        found = rest_client.projects.retrieve_project(name=project)
    except NotFoundError as exc:
        raise OnlineRuleError(
            f"no Opik project named {project!r} — run decode once so the project exists, "
            "or pass --project with the right name."
        ) from exc
    return str(found.id)


def find_rule_id(rest_client: Any, *, project_id: str, name: str = RULE_NAME) -> str | None:
    """The id of the evaluator named exactly ``name`` in this project, else ``None``.

    The exact-name re-filter matters here: the backend's ``name`` parameter matches substrings, so
    a rule called ``response_quality_v2`` must not read as "already exists". Unlike projects, rules
    have NO retrieve-by-name endpoint in opik 2.2.36, so this stays a paginated substring query and
    reads PAGE ONE only — fine while a project holds a handful of rules (``decode-prod`` holds one);
    past a page of rules whose names contain :data:`RULE_NAME`, page through before believing the
    ``None``.
    """
    page = rest_client.automation_rule_evaluators.find_evaluators(project_id=project_id, name=name)
    for candidate in getattr(page, "content", None) or []:
        if getattr(candidate, "name", None) == name:
            return str(candidate.id)
    return None


def create_response_quality_rule(
    *,
    project: str | None = None,
    model: str | None = None,
    sampling: float = 1.0,
    dry_run: bool = False,
) -> RuleOutcome:
    """Create the ``response_quality`` online rule on the LIVE project, once (ADR-0022 §13).

    ``project`` defaults to ``settings.opik_project_name`` — the LIVE project decode's traces land
    in (``decode``/``decode-<env>``), never ``eval_project_name``: an online rule grades real
    traffic in place. Idempotent: an existing rule is returned as ``action="exists"`` with its id
    and nothing is written. ``dry_run`` builds and returns the payload without touching the
    evaluator API (the project lookup still runs — a dry run that cannot name the project is not a
    useful rehearsal). ``opik`` is imported here, not at module scope, so the CLI stays opik-free.
    """
    import opik

    target = project or settings.opik_project_name
    resolved_model = opik_judge_model(model)
    rest_client = opik.Opik(project_name=target).rest_client
    project_id = resolve_project_id(rest_client, target)

    existing = find_rule_id(rest_client, project_id=project_id)
    if existing is not None:
        logger.info("[eval] online rule %s already exists in %s (%s)", RULE_NAME, target, existing)
        return RuleOutcome(action="exists", project=target, model=resolved_model, rule_id=existing)

    request = build_rule_request(project_id=project_id, model=resolved_model, sampling=sampling)
    payload = rule_payload(request)
    if dry_run:
        return RuleOutcome(action="dry-run", project=target, model=resolved_model, payload=payload)

    rest_client.automation_rule_evaluators.create_automation_rule_evaluator(request=request)
    # The create returns None (opik 2.2.36), so the id is re-read the same way idempotency reads it.
    rule_id = find_rule_id(rest_client, project_id=project_id)
    logger.info("[eval] created online rule %s in %s (%s)", RULE_NAME, target, rule_id)
    return RuleOutcome(
        action="created", project=target, model=resolved_model, rule_id=rule_id, payload=payload
    )
