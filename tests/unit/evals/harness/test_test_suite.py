"""Offline tests for the Opik 2.0 Test Suites surface (ADR-0017 §6; task 116).

No infra, no keys, no Opik 2.0: the 2.0 surface (``Opik.get_or_create_test_suite`` / ``opik.run_tests``)
is MOCKED, since this repo is pinned to opik 1.9.8 (the litellm/rustc gate — see the module docstring).
The tests cover the version guard, the item shaping, the ``{"input", "output"}`` adapter (and that it
never leaks an expected answer into ``input``), the suite wiring, and the pass-rate gate.
"""

from __future__ import annotations

import pytest

from evals.harness.test_suite import (
    GLOBAL_ASSERTIONS,
    ITEM_ASSERTIONS,
    SUITE_PASS_BAR,
    SUITE_PROBE_IDS,
    TEST_SUITE_NAME,
    SuitePassRateError,
    SuiteSelectionError,
    SuiteUnavailableError,
    assert_pass_rate,
    build_suite,
    make_suite_task_fn,
    run_test_suite,
    select_suite_probes,
    suite_api_available,
    suite_items,
)
from evals.regression.loader import load_probes
from evals.regression.probe import RegressionProbe


def _probe(probe_id: str, prompt: str = "do the thing") -> RegressionProbe:
    """A minimal probe standing in for a real one — id + prompt is all the suite surface reads."""
    return RegressionProbe(
        id=probe_id,
        prompt=prompt,
        fixture=lambda _w: None,
        metrics=[object()],  # the suite surface never scores with these; one is enough to construct
    )


# --- the version guard (path b) ---------------------------------------------------------------------


def test_suite_api_unavailable_on_installed_opik_1_9():
    """The installed opik 1.9.8 has neither half of the 2.0 surface, so the probe reports unavailable."""
    assert suite_api_available(object()) is False


def test_suite_api_available_true_when_both_halves_present(mocker):
    """With ``opik.run_tests`` present and a client exposing ``get_or_create_test_suite`` → available."""
    mocker.patch("opik.run_tests", create=True)
    client = mocker.Mock(spec=["get_or_create_test_suite"])

    assert suite_api_available(client) is True


def test_run_test_suite_raises_a_clear_versioned_stop_when_unavailable(mocker):
    """Without the 2.0 surface, ``run_test_suite`` stops loudly and names the version + rustc reason."""
    client = mocker.Mock(spec=[])  # no get_or_create_test_suite → guard fails

    with pytest.raises(SuiteUnavailableError) as excinfo:
        run_test_suite(client=client)

    message = str(excinfo.value)
    assert "opik>=2" in message
    assert "1.9.8" in message  # the INSTALLED version, read live
    assert "rustc" in message
    assert "116" in message  # points the reader at the task log


# --- item + assertion shaping -----------------------------------------------------------------------


def test_suite_probe_ids_include_the_three_named_judge_probes():
    """The subset is anchored on the ADR-named judge probes 17/18/19 (task 116)."""
    assert {"17-grounded-answer", "18-no-hallucinated-files", "19-template-compliance"} <= set(
        SUITE_PROBE_IDS
    )


def test_every_suite_probe_has_item_assertions():
    """Each probe in the subset ships at least one natural-language item assertion."""
    for probe_id in SUITE_PROBE_IDS:
        assert ITEM_ASSERTIONS.get(probe_id), probe_id


def test_suite_items_carry_probe_id_data_and_item_assertions():
    """One item per probe: ``data`` keyed by probe id, ``assertions`` from the per-probe rubric."""
    probes = [_probe("17-grounded-answer"), _probe("18-no-hallucinated-files")]

    items = suite_items(probes)

    assert [item["data"]["probe_id"] for item in items] == [
        "17-grounded-answer",
        "18-no-hallucinated-files",
    ]
    assert items[0]["assertions"] == list(ITEM_ASSERTIONS["17-grounded-answer"])


def test_suite_item_data_never_carries_the_prompt_or_an_expected_answer():
    """``data`` is just the probe id — no prompt, no expected answer that a judge could read (§6)."""
    probes = [_probe("17-grounded-answer", prompt="what is the Quibbler responsible for?")]

    items = suite_items(probes)

    assert items[0]["data"] == {"probe_id": "17-grounded-answer"}
    assert "prompt" not in items[0]["data"]


def test_select_suite_probes_orders_by_subset_and_skips_missing(mocker):
    """The selection follows ``SUITE_PROBE_IDS`` order and drops (warns on) any id not in the registry."""
    warn = mocker.patch("evals.harness.test_suite.logger.warning")
    # Registry has two of the subset (out of order) plus an unrelated probe.
    registry = [
        _probe("18-no-hallucinated-files"),
        _probe("unrelated"),
        _probe("17-grounded-answer"),
    ]

    selected = select_suite_probes(registry)

    assert [probe.id for probe in selected] == ["17-grounded-answer", "18-no-hallucinated-files"]
    assert warn.called  # the missing subset ids were logged


def test_the_real_registry_supplies_every_suite_probe():
    """Every id in the subset resolves against the real probe registry (no stale/renamed id)."""
    selected = select_suite_probes(load_probes())

    assert [probe.id for probe in selected] == list(SUITE_PROBE_IDS)


# --- the {"input", "output"} adapter ----------------------------------------------------------------


def test_suite_task_fn_shapes_input_and_output_from_the_regression_payload(mocker):
    """The adapter reuses the regression task fn and returns the ``{input, output}`` Test Suite contract."""
    probe = _probe("17-grounded-answer", prompt="what is the Quibbler responsible for?")
    fake_regression = mocker.Mock(return_value={"output": "The Quibbler deduplicates webhooks."})
    mocker.patch("evals.harness.test_suite.make_regression_task_fn", return_value=fake_regression)

    task = make_suite_task_fn({probe.id: probe})
    result = task({"probe_id": probe.id})

    fake_regression.assert_called_once_with({"probe_id": probe.id})
    assert result["output"] == "The Quibbler deduplicates webhooks."
    assert result["input"] == {"prompt": probe.prompt}


def test_suite_task_fn_input_never_leaks_an_expected_answer(mocker):
    """Even if the regression payload carries file_state / errors, ``input`` stays the prompt alone (§6).

    The docs warn a leaked expectation in ``input`` lets the NL judge cheat, so the adapter must forward
    ONLY the prompt the agent actually received — never the graded payload's internals.
    """
    probe = _probe("18-no-hallucinated-files", prompt="what does does_not_exist.py do?")
    fake_regression = mocker.Mock(
        return_value={
            "output": "That file does not exist in the project.",
            "file_state": {"README.md": "# Sample"},
            "agent_error": None,
        }
    )
    mocker.patch("evals.harness.test_suite.make_regression_task_fn", return_value=fake_regression)

    task = make_suite_task_fn({probe.id: probe})
    result = task({"probe_id": probe.id})

    assert result["input"] == {"prompt": probe.prompt}
    assert "file_state" not in result["input"]
    assert set(result.keys()) == {"input", "output"}


# --- suite construction + run wiring ----------------------------------------------------------------


def test_build_suite_creates_the_named_suite_with_global_assertions_and_inserts_items(mocker):
    """``build_suite`` sets the global NL bars + project on the suite and inserts one item per probe."""
    from decode.config.settings import settings

    probes = [_probe("17-grounded-answer"), _probe("18-no-hallucinated-files")]
    client = mocker.Mock()
    suite = client.get_or_create_test_suite.return_value

    returned = build_suite(client, probes)

    assert returned is suite
    _, kwargs = client.get_or_create_test_suite.call_args
    assert kwargs["name"] == TEST_SUITE_NAME
    assert kwargs["global_assertions"] == list(GLOBAL_ASSERTIONS)
    assert kwargs["project_name"] == settings.eval_project_name
    suite.insert.assert_called_once()
    inserted = suite.insert.call_args[0][0]
    assert [item["data"]["probe_id"] for item in inserted] == [
        "17-grounded-answer",
        "18-no-hallucinated-files",
    ]


def test_run_test_suite_builds_the_suite_and_runs_it(mocker):
    """The happy path: guard passes → build_suite → ``opik.run_tests`` with the adapter, result returned."""
    run_tests = mocker.patch("opik.run_tests", create=True)
    client = mocker.Mock(spec=["get_or_create_test_suite"])
    suite = client.get_or_create_test_suite.return_value

    result = run_test_suite(client=client)

    assert result is run_tests.return_value
    _, kwargs = run_tests.call_args
    assert kwargs["test_suite"] is suite
    assert callable(kwargs["task"])


def test_run_test_suite_raises_when_no_subset_probe_is_in_the_registry(mocker):
    """An empty selection is a friendly stop, never a silent zero-item suite run."""
    mocker.patch("opik.run_tests", create=True)
    client = mocker.Mock(spec=["get_or_create_test_suite"])
    mocker.patch("evals.harness.test_suite.load_probes", return_value=[_probe("unrelated")])

    with pytest.raises(SuiteSelectionError):
        run_test_suite(client=client)


# --- the pass-rate gate -----------------------------------------------------------------------------


def test_assert_pass_rate_passes_at_and_above_the_bar():
    """A pass rate at or above the bar clears the gate (no raise)."""
    assert_pass_rate(SUITE_PASS_BAR)
    assert_pass_rate(1.0)


def test_assert_pass_rate_raises_below_the_bar():
    """A pass rate below the bar fails the gate loudly — the CLI turns this into a non-zero exit."""
    with pytest.raises(SuitePassRateError) as excinfo:
        assert_pass_rate(SUITE_PASS_BAR - 0.01)

    assert "below" in str(excinfo.value)
