"""Offline tests for the Opik regression glue (ADR-0017 §3,4,6; task 111).

No infra and no keys: the agent runs a scripted model (``install_model``) host-native on a temp dir,
and ``opik.evaluation.evaluate`` / ``opik.Opik`` are mocked. The tests cover the task-fn payload shape,
the fixture being built, the gate honored, the temp dir cleaned, crashed-run / fixture-failure
surfacing, per-probe metric binding, that every probe knob reaches the driver, and the ``evaluate``
wiring (scoped ids, probe-scoped metrics, ``experiment_config``, single-threaded).
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
from evals.harness.metrics import ToolCalledMetric
from evals.harness.regression import (
    ProbeScopedMetric,
    RegressionSelectionError,
    _scoring_metrics,
    experiment_config,
    make_regression_task_fn,
    run_probe,
    run_regression,
)
from evals.regression.fixtures import near_limit_history
from evals.regression.probe import RegressionProbe

_NOTES = "notes.txt"
_NOTES_BODY = "The launch code is 4127."


def _seed_notes(workspace: Path) -> None:
    (workspace / _NOTES).write_text(_NOTES_BODY, encoding="utf-8")


def _read_probe(**overrides: object) -> RegressionProbe:
    """A probe that asks the agent to read the seeded notes file, graded on read-tool use."""
    base: dict[str, object] = {
        "id": "read-probe",
        "prompt": f"Read {_NOTES} and tell me what it says.",
        "fixture": _seed_notes,
        "metrics": [ToolCalledMetric("read")],
        "max_requests": 6,
    }
    base.update(overrides)
    return RegressionProbe(**base)  # type: ignore[arg-type]


def test_task_fn_runs_a_probe_and_returns_the_metric_payload(install_model):
    """The happy path: one read + a final line → the flat payload the metrics read, scoring 1.0."""
    install_model(read_then_finish(_NOTES, "It says the launch code is 4127."))
    probe = _read_probe()
    task_fn = make_regression_task_fn({probe.id: probe})

    payload = task_fn({"probe_id": probe.id})

    assert payload["output"] == "It says the launch code is 4127."
    assert payload["tool_calls"] == [{"name": "read", "args": {"path": _NOTES}}]
    assert payload["steps"] == 2
    assert payload["max_steps"] == 6
    assert payload["file_state"] == {_NOTES: _NOTES_BODY}  # the fixture was built
    assert payload["agent_error"] is None
    assert payload["infra_error"] is None
    # The probe's own metric scores the payload green.
    assert ToolCalledMetric("read").score(**payload).value == 1.0


def test_gate_is_honored_a_denied_mutation_never_hits_disk(install_model):
    """A probe under ``DEFAULT`` gate with the deny default blocks a mutating write (ADR-0017 §6)."""
    install_model(write_then_finish("out.txt", "hi", "stopped"))
    probe = _read_probe(
        id="deny-probe",
        prompt="write a file",
        fixture=lambda _w: None,
        gate_mode=PermissionMode.DEFAULT,
    )
    payload = run_probe(probe)

    assert payload["denied_tools"] == ["write"]
    assert "out.txt" not in payload["file_state"]


def test_custom_resolver_and_rules_reach_the_run(install_model):
    """A probe-supplied allow rule threads into the gate — the write lands (resolvers reachable)."""
    install_model(write_then_finish("ruled.txt", "by rule", "done"))
    probe = _read_probe(
        id="rule-probe",
        prompt="write a file",
        fixture=lambda _w: None,
        gate_mode=PermissionMode.DEFAULT,
        permission_rules=RuleSet(allow=[Rule(tool_name="write")]),
    )
    payload = run_probe(probe)

    assert payload["denied_tools"] == []
    assert payload["file_state"].get("ruled.txt") == "by rule"


def test_every_probe_knob_is_forwarded_to_the_driver(mocker):
    """Gate mode / rules / resolvers / message-history / cap all reach ``run_agent_once_sync``.

    Proves the acceptance criterion "gate modes / resolvers / message-history pre-fill all reachable
    from a probe declaration" — the probe's fields thread straight into the eval driver call.
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
    probe = _read_probe(
        gate_mode=PermissionMode.DEFAULT,
        permission_rules=rules,
        resolve_permission=approve,
        resolve_user_question=answer,
        message_history=lambda: history,
        max_requests=9,
    )

    run_probe(probe)

    _, kwargs = run.call_args
    assert kwargs["gate_mode"] == PermissionMode.DEFAULT
    assert kwargs["permission_rules"] is rules
    assert kwargs["resolve_permission"] is approve
    assert kwargs["resolve_user_question"] is answer
    assert kwargs["message_history"] == history
    assert kwargs["max_requests"] == 9


def test_context_manager_is_entered_around_the_run(install_model):
    """A probe's ``context`` (e.g. the http.server fixture) is entered around the run and torn down."""
    install_model(echo_line("done"))
    events: list[str] = []

    @contextmanager
    def context(_workspace: Path):
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    probe = _read_probe(id="ctx-probe", fixture=lambda _w: None, context=context)

    run_probe(probe)

    assert events == ["enter", "exit"]


def test_temp_workspace_is_removed_after_the_run(install_model, mocker):
    """The probe's fresh temp dir is cleaned whether the run passes or not (a tested criterion)."""
    install_model(echo_line("done"))
    real_dir = Path(tempfile.mkdtemp(prefix="decode-regression-probe-test-"))
    mocker.patch("evals.harness.regression.tempfile.mkdtemp", return_value=str(real_dir))
    probe = _read_probe(id="clean-probe", fixture=lambda _w: None)

    run_probe(probe)

    assert not real_dir.exists()


def test_sandbox_mode_is_restored_after_the_run(install_model):
    """``run_probe`` forces host-native ``none`` mode for the run and restores the prior mode after."""
    install_model(echo_line("done"))
    previous = settings.sandbox_mode
    probe = _read_probe(id="mode-probe", fixture=lambda _w: None)

    run_probe(probe)

    assert settings.sandbox_mode == previous


def test_a_crashed_agent_run_is_surfaced_as_agent_error(install_model):
    """A crashed run grades as fail-with-reason (``agent_error`` set), never silently empty."""
    install_model(crashing_model("kaboom"))
    probe = _read_probe(id="crash-probe", fixture=lambda _w: None)

    payload = run_probe(probe)

    assert payload["agent_error"] is not None
    assert "kaboom" in payload["agent_error"]
    assert payload["infra_error"] is None


def test_a_fixture_failure_is_surfaced_as_infra_error(install_model):
    """A fixture that raises grades as fail-with-reason (``infra_error``), never aborts the experiment."""
    install_model(echo_line("done"))

    def broken_fixture(_workspace: Path) -> None:
        raise RuntimeError("cannot seed workspace")

    probe = _read_probe(id="broken-probe", fixture=broken_fixture)

    payload = run_probe(probe)  # must NOT raise

    assert payload["infra_error"] is not None
    assert "cannot seed workspace" in payload["infra_error"]
    assert payload["agent_error"] is None
    assert payload["file_state"] == {}


def test_probe_scoped_metric_scores_only_its_own_item():
    """``ProbeScopedMetric`` grades its probe's item and contributes nothing to any other (§6)."""
    inner = ToolCalledMetric("read")
    wrapped = ProbeScopedMetric("mine", inner)
    payload = {"tool_calls": [{"name": "read", "args": {}}]}

    on_match = wrapped.score(probe_id="mine", **payload)
    off_match = wrapped.score(probe_id="other", **payload)

    assert on_match.value == 1.0
    assert on_match.name == inner.name
    assert off_match == []  # zero score results for a different probe's item


def test_scoring_metrics_wrap_every_probes_metrics():
    """Each selected probe's metrics are wrapped into per-probe-scoped metrics for one metric list."""
    a = _read_probe(id="a")
    b = _read_probe(id="b", metrics=[ToolCalledMetric("read"), ToolCalledMetric("grep")])

    metrics = _scoring_metrics([a, b])

    assert len(metrics) == 3  # 1 from a + 2 from b
    assert all(isinstance(metric, ProbeScopedMetric) for metric in metrics)


def test_run_regression_wires_evaluate(mocker):
    """``run_regression`` scopes ``evaluate`` to the selected probe item with the scoped metrics + config."""
    probe = _read_probe(id="wired-probe")
    mocker.patch("evals.harness.regression.load_probes", return_value=[probe])
    evaluate = mocker.patch("opik.evaluation.evaluate")
    opik_cls = mocker.patch("opik.Opik")
    dataset = opik_cls.return_value.get_or_create_dataset.return_value
    dataset.get_items.return_value = [{"id": "item-1", "probe_id": probe.id}]

    result = run_regression(probe_id=probe.id)

    assert result is evaluate.return_value
    _, kwargs = evaluate.call_args
    assert kwargs["dataset"] is dataset
    assert kwargs["project_name"] == settings.eval_project_name
    assert kwargs["dataset_item_ids"] == ["item-1"]
    assert kwargs["task_threads"] == 1  # the bash seam is process-global — no concurrent task fns
    assert all(isinstance(metric, ProbeScopedMetric) for metric in kwargs["scoring_metrics"])
    config = kwargs["experiment_config"]
    assert config["agent_model"]
    assert config["git_sha"]
    assert config["harness"] == "regression"


def test_run_regression_raises_when_no_probe_matches(mocker):
    """An empty selection is a loud, friendly stop — never a silent zero-item experiment."""
    mocker.patch("evals.harness.regression.load_probes", return_value=[_read_probe(id="only")])

    with pytest.raises(RegressionSelectionError):
        run_regression(probe_id="does-not-exist")


def test_experiment_config_has_model_provider_and_git_sha():
    config = experiment_config()

    assert config["provider"] == settings.llm_provider
    assert config["agent_model"]
    assert config["git_sha"]  # this repo is a git checkout
    assert config["harness"] == "regression"
