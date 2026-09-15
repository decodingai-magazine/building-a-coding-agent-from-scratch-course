"""`evals online-rule create` — the idempotent `response_quality` LLM-as-judge rule (task 163).

The Opik client is a fake shaped like the INSTALLED 2.2.36 REST surface: ``projects.retrieve_project``
matches a name exactly and raises the real ``NotFoundError`` otherwise, ``automation_rule_evaluators.
find_evaluators`` returns a page with ``.content`` for a SUBSTRING query, and
``create_automation_rule_evaluator`` returns ``None`` (which is why the id is re-read afterwards).
No network, no key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from opik.rest_api.errors import NotFoundError

from evals.harness import online_rule
from evals.harness.online_rule import (
    RESPONSE_QUALITY_PROMPT,
    RULE_NAME,
    OnlineRuleError,
    build_rule_request,
    create_response_quality_rule,
    find_rule_id,
    opik_judge_model,
    resolve_project_id,
    rule_payload,
)

README = Path(__file__).resolve().parents[3].parent / "evals" / "README.md"


@dataclass(frozen=True)
class Named:
    id: str
    name: str


class Page:
    def __init__(self, content: list[Named]) -> None:
        self.content = content


class FakeProjects:
    def __init__(self, projects: list[Named]) -> None:
        self.projects = projects
        self.queries: list[str] = []

    def retrieve_project(self, *, name: str) -> Named:
        self.queries.append(name)
        # ``POST v1/private/projects/retrieve`` matches the name EXACTLY and 404s otherwise — the
        # fake does the same, so a substring can never resolve to a neighbouring project's id.
        for project in self.projects:
            if project.name == name:
                return project
        raise NotFoundError(headers={}, body={"message": f"Project not found: {name}"})


class FakeEvaluators:
    def __init__(self, rules: list[Named] | None = None) -> None:
        self.rules = list(rules or [])
        self.created: list[Any] = []
        self.queries: list[tuple[str, str]] = []

    def find_evaluators(self, *, project_id: str, name: str) -> Page:
        self.queries.append((project_id, name))
        return Page([rule for rule in self.rules if name in rule.name])

    def create_automation_rule_evaluator(self, *, request: Any) -> None:
        self.created.append(request)
        self.rules.append(Named(id="rule-new", name=request.name))
        return None  # exactly what opik 2.2.36 returns


class FakeRest:
    def __init__(self, projects: FakeProjects, evaluators: FakeEvaluators) -> None:
        self.projects = projects
        self.automation_rule_evaluators = evaluators


@pytest.fixture
def rest() -> FakeRest:
    return FakeRest(
        FakeProjects(
            [
                Named(id="pid-prod", name="decode-prod"),
                Named(id="pid-x", name="decode-prod-scratch"),
                Named(id="pid-plain", name="decode"),
            ]
        ),
        FakeEvaluators(),
    )


@pytest.fixture
def opik_client(mocker, rest: FakeRest):
    """Patch ``opik.Opik`` so the command's lazy client is the fake (no key, no network)."""
    import opik

    client = mocker.Mock()
    client.rest_client = rest
    factory = mocker.patch.object(opik, "Opik", return_value=client)
    return factory


# --- judge-model routing -------------------------------------------------------------------------


def test_the_gemini_route_becomes_an_opik_model_id_without_the_litellm_prefix(mocker) -> None:
    mocker.patch.object(online_rule, "judge_model", return_value="gemini/gemini-2.5-flash")

    assert opik_judge_model() == "gemini-2.5-flash"


def test_an_explicit_model_wins_verbatim(mocker) -> None:
    mocker.patch.object(online_rule, "judge_model", return_value="gemini/gemini-2.5-flash")

    assert opik_judge_model(" openai/gpt-4o-mini ") == "openai/gpt-4o-mini"


@pytest.mark.parametrize("routed", ["openrouter/meta/llama-3", "openai/Qwen/Qwen3"])
def test_the_other_routes_refuse_and_ask_for_an_explicit_model(mocker, routed: str) -> None:
    mocker.patch.object(online_rule, "judge_model", return_value=routed)

    with pytest.raises(OnlineRuleError, match="--model"):
        opik_judge_model()


def test_the_default_model_follows_the_judge_provider_not_the_agents(mocker) -> None:
    """A gemini JUDGE on a modal agent derives the id that used to be unreachable (task 170)."""
    mocker.patch.object(online_rule.settings, "llm_provider", "modal")
    mocker.patch.object(online_rule.settings, "eval_judge_provider", "gemini")
    mocker.patch.object(online_rule.settings, "eval_judge_model", "")

    assert opik_judge_model() == "gemini-2.5-flash"


def test_a_modal_judge_still_refuses_and_names_the_judge_provider(mocker) -> None:
    """The online rule runs inside OPIK, never through litellm — so the modal endpoint is no help.

    The refusal must name the JUDGE's provider: with a gemini agent and a modal judge, blaming
    ``llm_provider`` would send the operator to the wrong env var.
    """
    mocker.patch.object(online_rule.settings, "llm_provider", "gemini")
    mocker.patch.object(online_rule.settings, "eval_judge_provider", "modal")
    mocker.patch.object(online_rule.settings, "eval_judge_model", "")
    mocker.patch.object(online_rule.settings, "modal_endpoint_model", "Qwen/Qwen3")

    with pytest.raises(OnlineRuleError, match="--model") as excinfo:
        opik_judge_model()

    assert "'modal'" in str(excinfo.value)


# --- the payload ---------------------------------------------------------------------------------


def test_the_create_payload_matches_the_installed_write_types() -> None:
    body = json.loads(rule_payload(build_rule_request(project_id="pid", model="m", sampling=0.5)))

    assert body["type"] == "llm_as_judge"
    assert body["name"] == RULE_NAME == "response_quality"
    assert body["project_id"] == "pid"
    # `project_ids` is the field the write API documents for create/update; `project_id` is legacy.
    assert body["project_ids"] == ["pid"]
    assert body["sampling_rate"] == 0.5
    assert body["enabled"] is True
    assert body["action"] == "evaluator"
    assert body["trigger_scope"] == "production"
    assert body["code"]["model"] == {"name": "m", "temperature": 0.0}
    assert body["code"]["variables"] == {"input": "input", "output": "output"}
    # Serialised under its `schema` alias — the field is spelled `schema_` on the model.
    assert body["code"]["schema"][0]["name"] == RULE_NAME
    assert body["code"]["schema"][0]["type"] == "INTEGER"


def test_the_prompt_is_the_module_constant_with_both_placeholders() -> None:
    message = build_rule_request(project_id="pid", model="m", sampling=1.0).code.messages[0]

    assert message.role == "USER"
    assert message.content == RESPONSE_QUALITY_PROMPT
    assert "{{input}}" in RESPONSE_QUALITY_PROMPT
    assert "{{output}}" in RESPONSE_QUALITY_PROMPT


def test_the_prompt_is_phrased_qualitatively_not_as_a_numeric_verdict() -> None:
    """The task-114 lesson: a 1.0/0.0 instruction collides with the judge's own 0-10 scale."""
    lowered = RESPONSE_QUALITY_PROMPT.lower()

    assert "1.0" not in lowered
    assert "0.0" not in lowered
    assert "high-scoring" in lowered


def test_the_readme_quotes_the_prompt_constant_verbatim_so_it_cannot_drift() -> None:
    assert RESPONSE_QUALITY_PROMPT in README.read_text()


# --- project + rule lookup -----------------------------------------------------------------------


def test_the_project_id_is_matched_exactly_never_by_substring(rest: FakeRest) -> None:
    assert resolve_project_id(rest, "decode-prod") == "pid-prod"
    # The one-request retrieve-by-name: no page to re-filter, and a name that is a substring of
    # another project resolves to ITS OWN id, never to the longer neighbour's.
    assert resolve_project_id(rest, "decode") == "pid-plain"
    assert rest.projects.queries == ["decode-prod", "decode"]


def test_a_project_that_does_not_exist_is_one_friendly_error(rest: FakeRest) -> None:
    with pytest.raises(OnlineRuleError, match="no Opik project"):
        resolve_project_id(rest, "decode-nowhere")


def test_a_rule_whose_name_merely_contains_the_rule_name_does_not_count_as_existing(
    rest: FakeRest,
) -> None:
    rest.automation_rule_evaluators.rules = [Named(id="other", name=f"{RULE_NAME}_v2")]

    assert find_rule_id(rest, project_id="pid-prod") is None


# --- the command ---------------------------------------------------------------------------------


def test_a_dry_run_prints_the_payload_and_writes_nothing(rest: FakeRest, opik_client) -> None:
    outcome = create_response_quality_rule(project="decode-prod", model="m", dry_run=True)

    assert outcome.action == "dry-run"
    assert outcome.rule_id is None
    assert json.loads(outcome.payload or "")["name"] == RULE_NAME
    assert rest.automation_rule_evaluators.created == []


def test_the_first_run_creates_the_rule_once_and_reports_its_id(
    rest: FakeRest, opik_client
) -> None:
    outcome = create_response_quality_rule(project="decode-prod", model="m", sampling=1.0)

    assert outcome.action == "created"
    assert outcome.rule_id == "rule-new"
    assert len(rest.automation_rule_evaluators.created) == 1
    assert rest.automation_rule_evaluators.created[0].project_id == "pid-prod"


def test_the_second_run_finds_it_and_writes_nothing(rest: FakeRest, opik_client) -> None:
    create_response_quality_rule(project="decode-prod", model="m")

    outcome = create_response_quality_rule(project="decode-prod", model="m")

    assert outcome.action == "exists"
    assert outcome.rule_id == "rule-new"
    assert len(rest.automation_rule_evaluators.created) == 1


def test_the_default_project_is_the_live_one_never_the_eval_project(
    mocker, rest: FakeRest, opik_client
) -> None:
    mocker.patch.object(online_rule.settings, "opik_project_name", "decode-prod")
    mocker.patch.object(online_rule.settings, "eval_project_name", "decode-evals")

    outcome = create_response_quality_rule(model="m", dry_run=True)

    assert outcome.project == "decode-prod"
    assert opik_client.call_args.kwargs == {"project_name": "decode-prod"}
