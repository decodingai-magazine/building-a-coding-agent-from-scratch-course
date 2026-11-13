"""Offline tests for the Langfuse regression glue (ADR-0022 §8; ADR-0017 §3,4,6).

No infra and no keys: the agent runs a scripted model (``install_model``) host-native on a temp dir,
and Langfuse datasets / experiments are mocked. The tests cover the task-fn payload shape,
the fixture being built, the gate honored, the temp dir cleaned, crashed-run / fixture-failure
surfacing, per-case metric binding, that every case knob reaches the driver, the ``evaluate`` wiring
(scoped ids, case-scoped metrics, the per-metric experiment score, ``experiment_config``,
single-threaded), and the tier-aware experiment naming.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from support.eval_models import crashing_model, echo_line, read_then_finish, write_then_finish

from decode.config.settings import settings
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.rules import Rule, RuleSet
from decode.permissions.types import PermissionMode
from evals.harness.datasets import case_checksum
from evals.harness.metrics import ToolCalledMetric
from evals.harness.regression import (
    GATE_EXPERIMENT_NAME,
    CaseScopedMetric,
    RegressionSelectionError,
    _scoring_metrics,
    experiment_config,
    make_regression_task_fn,
    mean_per_metric,
    run_case,
    run_regression,
    scoped_name,
)
from evals.regression.case import RegressionCase
from evals.regression.fixtures import near_limit_history

_NOTES = "notes.txt"
_NOTES_BODY = "The launch code is 4127."


def _seed_notes(workspace: Path) -> None:
    (workspace / _NOTES).write_text(_NOTES_BODY, encoding="utf-8")


def _read_case(**overrides: object) -> RegressionCase:
    """A case that asks the agent to read the seeded notes file, graded on read-tool use."""
    base: dict[str, object] = {
        "id": "read-case",
        "prompt": f"Read {_NOTES} and tell me what it says.",
        "fixture": _seed_notes,
        "metrics": [ToolCalledMetric("read")],
        "max_requests": 6,
        "difficulty": "easy",
        "description": "Tests that reading a file uses the read tool.",
        "symptom": "harness invariant: reading a file uses the read tool.",
        "assertion": "The response reports what the file says.",
    }
    base.update(overrides)
    return RegressionCase(**base)  # type: ignore[arg-type]


def _item(item_id: str, case: RegressionCase) -> dict[str, str]:
    """A FRESH dataset item for ``case`` — what the checksum-scoped selection matches on."""
    return {"id": item_id, "case_id": case.id, "checksum": case_checksum(case)}


def test_task_fn_runs_a_case_and_returns_the_metric_payload(install_model):
    """The happy path: one read + a final line → the flat payload the metrics read, scoring 1.0."""
    install_model(read_then_finish(_NOTES, "It says the launch code is 4127."))
    case = _read_case()
    task_fn = make_regression_task_fn({case.id: case})

    payload = task_fn({"case_id": case.id})

    assert payload["output"] == "It says the launch code is 4127."
    assert payload["tool_calls"] == [{"name": "read", "args": {"path": _NOTES}}]
    assert payload["steps"] == 2
    assert payload["max_steps"] == 6
    assert payload["file_state"] == {_NOTES: _NOTES_BODY}  # the fixture was built
    assert payload["agent_error"] is None
    assert payload["infra_error"] is None
    # The case's own metric scores the payload green.
    assert ToolCalledMetric("read").score(**payload).value == 1.0


def test_gate_is_honored_a_denied_mutation_never_hits_disk(install_model):
    """A case under ``DEFAULT`` gate with the deny default blocks a mutating write (ADR-0017 §6)."""
    install_model(write_then_finish("out.txt", "hi", "stopped"))
    case = _read_case(
        id="deny-case",
        prompt="write a file",
        fixture=lambda _w: None,
        gate_mode=PermissionMode.DEFAULT,
    )
    payload = run_case(case)

    assert payload["denied_tools"] == ["write"]
    assert "out.txt" not in payload["file_state"]


def test_custom_resolver_and_rules_reach_the_run(install_model):
    """A case-supplied allow rule threads into the gate — the write lands (resolvers reachable)."""
    install_model(write_then_finish("ruled.txt", "by rule", "done"))
    case = _read_case(
        id="rule-case",
        prompt="write a file",
        fixture=lambda _w: None,
        gate_mode=PermissionMode.DEFAULT,
        permission_rules=RuleSet(allow=[Rule(tool_name="write")]),
    )
    payload = run_case(case)

    assert payload["denied_tools"] == []
    assert payload["file_state"].get("ruled.txt") == "by rule"


def test_every_case_knob_is_forwarded_to_the_driver(mocker):
    """Gate mode / rules / resolvers / message-history / cap all reach ``run_agent_once_sync``.

    Proves the acceptance criterion "gate modes / resolvers / message-history pre-fill all reachable
    from a case declaration" — the case's fields thread straight into the eval driver call.
    """
    run = mocker.patch("evals.harness.regression.run_agent_once_sync")
    run.return_value.agent_error = None
    run.return_value.output = ""
    run.return_value.tool_calls = []
    run.return_value.steps = 0
    run.return_value.input_tokens = 0
    run.return_value.output_tokens = 0
    run.return_value.denied_tools = []

    async def approve(_request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    async def answer(_question: str) -> str:
        return "yes"

    rules = RuleSet(allow=[Rule(tool_name="write")])
    history = near_limit_history(target_tokens=200)
    case = _read_case(
        gate_mode=PermissionMode.DEFAULT,
        permission_rules=rules,
        resolve_permission=approve,
        resolve_user_question=answer,
        message_history=lambda: history,
        max_requests=9,
    )

    run_case(case)

    _, kwargs = run.call_args
    assert kwargs["gate_mode"] == PermissionMode.DEFAULT
    assert kwargs["permission_rules"] is rules
    assert kwargs["resolve_permission"] is approve
    assert kwargs["resolve_user_question"] is answer
    assert kwargs["message_history"] == history
    assert kwargs["max_requests"] == 9


def test_context_manager_is_entered_around_the_run(install_model):
    """A case's ``context`` (e.g. the http.server fixture) is entered around the run and torn down."""
    install_model(echo_line("done"))
    events: list[str] = []

    @contextmanager
    def context(_workspace: Path):
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    case = _read_case(id="ctx-case", fixture=lambda _w: None, context=context)

    run_case(case)

    assert events == ["enter", "exit"]


def test_temp_workspace_is_removed_after_the_run(install_model, mocker):
    """The case's fresh temp dir is cleaned whether the run passes or not (a tested criterion)."""
    install_model(echo_line("done"))
    real_dir = Path(tempfile.mkdtemp(prefix="decode-regression-case-test-"))
    mocker.patch("evals.harness.regression.tempfile.mkdtemp", return_value=str(real_dir))
    case = _read_case(id="clean-case", fixture=lambda _w: None)

    run_case(case)

    assert not real_dir.exists()


def test_sandbox_mode_is_restored_after_the_run(install_model):
    """``run_case`` forces host-native ``none`` mode for the run and restores the prior mode after."""
    install_model(echo_line("done"))
    previous = settings.sandbox_mode
    case = _read_case(id="mode-case", fixture=lambda _w: None)

    run_case(case)

    assert settings.sandbox_mode == previous


def test_settings_overrides_are_applied_during_and_restored_after_the_run(install_model):
    """A case's ``settings_overrides`` bite for the run and are rolled back after (the compaction knob)."""
    seen: dict[str, int] = {}

    def _record_fixture(_workspace: Path) -> None:
        seen["window"] = settings.compaction_context_window_tokens

    install_model(echo_line("done"))
    previous = settings.compaction_context_window_tokens
    case = _read_case(
        id="override-case",
        fixture=_record_fixture,
        settings_overrides={"compaction_context_window_tokens": 1234},
    )

    run_case(case)

    assert seen["window"] == 1234  # the override was live during the run
    assert settings.compaction_context_window_tokens == previous  # and rolled back after


def test_unknown_settings_override_is_surfaced_as_infra_error(install_model):
    """A typo'd override key fails loudly (as an ``infra_error``), never silently no-ops."""
    install_model(echo_line("done"))
    case = _read_case(
        id="bad-override-case",
        fixture=lambda _w: None,
        settings_overrides={"nonexistent_setting": 1},
    )

    payload = run_case(case)

    assert payload["infra_error"] is not None
    assert "nonexistent_setting" in payload["infra_error"]


def test_compaction_events_are_surfaced_in_the_payload(install_model):
    """The payload carries a ``compaction_events`` count (0 for a case that does not enable it)."""
    install_model(echo_line("done"))
    case = _read_case(id="no-compaction-case", fixture=lambda _w: None)

    payload = run_case(case)

    assert payload["compaction_events"] == 0


def test_a_crashed_agent_run_is_surfaced_as_agent_error(install_model):
    """A crashed run grades as fail-with-reason (``agent_error`` set), never silently empty."""
    install_model(crashing_model("kaboom"))
    case = _read_case(id="crash-case", fixture=lambda _w: None)

    payload = run_case(case)

    assert payload["agent_error"] is not None
    assert "kaboom" in payload["agent_error"]
    assert payload["infra_error"] is None


def test_a_fixture_failure_is_surfaced_as_infra_error(install_model):
    """A fixture that raises grades as fail-with-reason (``infra_error``), never aborts the experiment."""
    install_model(echo_line("done"))

    def broken_fixture(_workspace: Path) -> None:
        raise RuntimeError("cannot seed workspace")

    case = _read_case(id="broken-case", fixture=broken_fixture)

    payload = run_case(case)  # must NOT raise

    assert payload["infra_error"] is not None
    assert "cannot seed workspace" in payload["infra_error"]
    assert payload["agent_error"] is None
    assert payload["file_state"] == {}


def test_case_scoped_metric_scores_only_its_own_item():
    """``CaseScopedMetric`` grades its case's item and contributes nothing to any other (§6)."""
    inner = ToolCalledMetric("read")
    wrapped = CaseScopedMetric("mine", inner)
    payload = {"tool_calls": [{"name": "read", "args": {}}]}

    on_match = wrapped.score(case_id="mine", **payload)
    off_match = wrapped.score(case_id="other", **payload)

    assert on_match.value == 1.0
    assert on_match.name == inner.name
    assert off_match == []  # zero score results for a different case's item


def test_scoring_metrics_wrap_every_cases_metrics():
    """Each selected case's metrics are wrapped into per-case-scoped metrics for one metric list."""
    a = _read_case(id="a")
    b = _read_case(id="b", metrics=[ToolCalledMetric("read"), ToolCalledMetric("grep")])

    metrics = _scoring_metrics([a, b])

    assert len(metrics) == 3  # 1 from a + 2 from b
    assert all(isinstance(metric, CaseScopedMetric) for metric in metrics)


def _install_langfuse(mocker, cases, items=None):
    from types import SimpleNamespace

    from evals.harness.datasets import case_checksum

    records = (
        items
        if items is not None
        else [
            {
                "id": f"item-{case.id}",
                "input": {"case_id": case.id, "prompt": case.prompt},
                "metadata": {
                    "key": case.id,
                    "case_id": case.id,
                    "checksum": case_checksum(case),
                    "difficulty": case.difficulty,
                },
            }
            for case in cases
        ]
    )
    dataset = SimpleNamespace(name="decode-regression", items=records)
    client = mocker.Mock()
    client.get_dataset.return_value = dataset
    client.run_experiment.return_value = SimpleNamespace(
        name="decode-regression-gate", experiment_id="experiment-1", item_results=[]
    )
    mocker.patch("evals.harness.datasets.langfuse_client", return_value=client)
    mocker.patch("evals.harness.regression.langfuse_client", return_value=client)
    return client


def test_run_regression_uses_langfuse_experiment_and_only_current_items(mocker):
    case = _read_case(id="wired-case")
    mocker.patch("evals.harness.regression.load_cases", return_value=[case])
    client = _install_langfuse(mocker, [case])

    result = run_regression(case_id=case.id)

    kwargs = client.run_experiment.call_args.kwargs
    assert result.experiment_id == "experiment-1"
    assert kwargs["name"] == f"{GATE_EXPERIMENT_NAME}-{case.id}"
    assert kwargs["data"][0]["metadata"]["source_dataset_item_id"] == "item-wired-case"
    assert kwargs["max_concurrency"] == 1
    assert len(kwargs["evaluators"]) == 1
    assert kwargs["run_evaluators"]
    assert kwargs["metadata"]["harness"] == "regression"
    assert kwargs["metadata"]["case_count"] == 1


def test_regression_experiment_names_preserve_case_and_tier_slices(mocker):
    easy = _read_case(id="easy-case")
    hard = _read_case(id="hard-case", difficulty="hard")
    mocker.patch("evals.harness.regression.load_cases", return_value=[easy, hard])
    client = _install_langfuse(mocker, [easy, hard])

    run_regression(difficulty="hard")

    kwargs = client.run_experiment.call_args.kwargs
    assert kwargs["name"] == "decode-regression-gate-hard"
    assert len(kwargs["data"]) == 1
    assert kwargs["data"][0]["metadata"]["case_id"] == "hard-case"
    assert kwargs["metadata"]["difficulty"] == "hard"


def test_an_explicit_experiment_name_wins(mocker):
    case = _read_case(id="named-case")
    mocker.patch("evals.harness.regression.load_cases", return_value=[case])
    client = _install_langfuse(mocker, [case])

    run_regression(case_id=case.id, experiment_name="one-off")

    assert client.run_experiment.call_args.kwargs["name"] == "one-off"


def test_scoped_name_appends_the_slice_and_prefers_the_case_id():
    assert scoped_name("base") == "base"
    assert scoped_name("base", difficulty="hard") == "base-hard"
    assert scoped_name("base", case_id="17-grounded-answer") == "base-17-grounded-answer"
    assert scoped_name("base", case_id="c", difficulty="hard") == "base-c"


def test_stale_dataset_item_is_not_selected(mocker):
    case = _read_case(id="edited-case")
    mocker.patch("evals.harness.regression.load_cases", return_value=[case])
    client = _install_langfuse(
        mocker,
        [case],
        items=[
            {
                "id": "stale",
                "input": {"case_id": case.id},
                "metadata": {"case_id": case.id, "checksum": "0" * 64},
            },
            {
                "id": "fresh",
                "input": {"case_id": case.id, "prompt": case.prompt},
                "metadata": {"key": case.id, "case_id": case.id, "checksum": case_checksum(case)},
            },
        ],
    )

    run_regression(case_id=case.id)

    assert (
        client.run_experiment.call_args.kwargs["data"][0]["metadata"]["source_dataset_item_id"]
        == "fresh"
    )


def test_run_regression_stops_when_no_current_dataset_item_exists(mocker):
    case = _read_case(id="unsynced-case")
    mocker.patch("evals.harness.regression.load_cases", return_value=[case])
    client = _install_langfuse(
        mocker,
        [case],
        items=[
            {
                "id": "stale",
                "input": {"case_id": case.id},
                "metadata": {"case_id": case.id, "checksum": "0" * 64},
            }
        ],
    )

    with pytest.raises(RegressionSelectionError, match=r"evals sync --regression"):
        run_regression(case_id=case.id)
    client.run_experiment.assert_not_called()


def test_run_regression_rejects_empty_or_skip_guarded_selection(mocker):
    mocker.patch("evals.harness.regression.load_cases", return_value=[_read_case(id="only")])
    with pytest.raises(RegressionSelectionError):
        run_regression(case_id="does-not-exist")

    skipped = _read_case(id="skipped", skip_reason="not ready")
    mocker.patch("evals.harness.regression.load_cases", return_value=[skipped])
    with pytest.raises(RegressionSelectionError):
        run_regression(case_id="skipped")


def test_experiment_config_has_model_provider_git_sha_and_shape():
    config = experiment_config(case_count=8, difficulty="hard")
    assert config["provider"] == settings.llm_provider
    assert config["agent_model"]
    assert config["git_sha"]
    assert config["harness"] == "regression"
    assert config["case_count"] == 8
    assert config["difficulty"] == "hard"


class _Score:
    def __init__(self, name: str, value: float, scoring_failed: bool = False) -> None:
        self.name = name
        self.value = value
        self.scoring_failed = scoring_failed


class _Result:
    def __init__(self, *scores: _Score) -> None:
        self.score_results = list(scores)


def test_mean_per_metric_averages_only_scored_items():
    results = [
        _Result(_Score("tool_called_read", 1.0), _Score("max_steps", 1.0)),
        _Result(_Score("tool_called_read", 0.0)),
    ]
    assert [(score.name, score.value) for score in mean_per_metric(results)] == [
        ("mean_max_steps", 1.0),
        ("mean_tool_called_read", 0.5),
    ]


def test_mean_per_metric_ignores_failed_metrics_and_empty_runs():
    assert [
        (score.name, score.value)
        for score in mean_per_metric(
            [_Result(_Score("max_steps", 1.0)), _Result(_Score("max_steps", 0.0, True))]
        )
    ] == [("mean_max_steps", 1.0)]
    assert mean_per_metric([]) == []
